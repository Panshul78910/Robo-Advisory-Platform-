"""List the models a Groq key can actually reach, and time one of them.

Published model line-ups move, and a HOSTED_JUDGE_MODEL that 404s is indis-
tinguishable from a rate limit once it has fallen back to Ollama -- the request
still gets a verdict, just slowly. Running this first settles what to put in
.env, and the timing shows whether the trip is worth making at all.

    python -m scripts.hosted_models
    python -m scripts.hosted_models --time qwen/qwen3-32b
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.llm.judge import HOSTED_MAX_TOKENS  # noqa: E402


def _probe(settings, model: str) -> None:
    """One judge-shaped call: short JSON out, which is all these judges ever do."""
    started = time.perf_counter()
    r = httpx.post(
        f"{settings.judge_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.judge_api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": 'Reply with compact JSON only: {"ok":true}'},
                {"role": "user", "content": "Return the JSON now."},
            ],
            "temperature": 0,
            "max_tokens": HOSTED_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        },
        timeout=settings.hosted_timeout_s,
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    if r.status_code != 200:
        print(f"  {model}: HTTP {r.status_code} ({elapsed}ms)")
        # `failed_generation` carries what the model actually produced, which is
        # the only thing that distinguishes "cannot do JSON mode" from "spent the
        # budget reasoning before it got to the JSON".
        try:
            err = r.json().get("error", {})
            print(f"    {err.get('code')}: {err.get('message')}")
            if failed := err.get("failed_generation"):
                print(f"    failed_generation: {str(failed)[:600]!r}")
        except ValueError:
            print(f"    {r.text[:600]}")
        return
    content = r.json()["choices"][0]["message"]["content"].strip()
    print(f"  {model}: {elapsed}ms  {content[:80]!r}")
    print("\n  Compare: the local 7B input judge measured ~11s on this machine.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--time", metavar="MODEL", help="send one judge-shaped call to MODEL")
    args = ap.parse_args()

    settings = get_settings()
    if not settings.judge_api_key:
        print("JUDGE_API_KEY is not set. Add it to .env first.")
        return 1

    if args.time:
        _probe(settings, args.time)
        return 0

    r = httpx.get(
        f"{settings.judge_base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {settings.judge_api_key}"},
        timeout=settings.hosted_timeout_s,
    )
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return 1

    ids = sorted(m.get("id", "") for m in r.json().get("data", []))
    print(f"{len(ids)} models reachable with this key:\n")
    for model_id in ids:
        # Flag the family the judge prompts were tuned against.
        note = "   <- same family as the tuned prompts" if "qwen" in model_id.lower() else ""
        print(f"  {model_id}{note}")
    print("\nSet HOSTED_JUDGE_MODEL to one of these, then:")
    print("  python -m scripts.hosted_models --time <model>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
