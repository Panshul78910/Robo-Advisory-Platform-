"""Measure the shared HPC endpoint and write docs/hpc_probe.md.

Run this before relying on the context builder's token accounting: whether a real
tokenizer is available decides between exact counts and the conservative
heuristic, and the measured context limit decides the block budgets.

    python -m scripts.probe_hpc

Uses ~25 completions. Do not run it in a loop -- the endpoint is shared.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import statistics
import time

import httpx

from app.core.config import get_settings
from app.llm.hpc import normalise_base_url

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "hpc_probe.md"
LATENCY_SAMPLES = 20


async def _post(client: httpx.AsyncClient, body: dict, headers: dict) -> httpx.Response:
    return await client.post("/v1/chat/completions", json=body, headers=headers)


async def probe() -> dict:
    s = get_settings()
    findings: dict = {"base_url_configured": bool(s.hpc_base_url), "model": s.hpc_model_name}
    if not s.hpc_base_url:
        return findings

    headers = {"Content-Type": "application/json"}
    if s.hpc_api_key:
        headers["Authorization"] = f"Bearer {s.hpc_api_key}"

    async with httpx.AsyncClient(
        base_url=normalise_base_url(s.hpc_base_url),
        timeout=httpx.Timeout(connect=5, read=60, write=5, pool=5),
    ) as client:
        # --- shape of the API ------------------------------------------------
        try:
            models = await client.get("/v1/models", headers=headers)
            findings["openai_compatible"] = models.status_code == 200
            findings["models_endpoint"] = models.status_code
            if models.status_code == 200:
                findings["models"] = [m.get("id") for m in models.json().get("data", [])][:10]
        except httpx.HTTPError as exc:
            findings["openai_compatible"] = False
            findings["models_error"] = type(exc).__name__

        base = {"model": s.hpc_model_name, "max_tokens": 16}

        # --- separate system role? -------------------------------------------
        probe_system = await _post(
            client,
            {
                **base,
                "messages": [
                    {"role": "system", "content": "Reply with exactly: SYSTEM_OK"},
                    {"role": "user", "content": "hello"},
                ],
            },
            headers,
        )
        findings["system_role_status"] = probe_system.status_code
        findings["system_role_honoured"] = (
            probe_system.status_code == 200 and "SYSTEM_OK" in probe_system.text
        )
        if probe_system.status_code == 200:
            findings["response_keys"] = sorted(probe_system.json().keys())
            findings["usage_reported"] = "usage" in probe_system.json()

        # --- streaming --------------------------------------------------------
        try:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    **base,
                    "messages": [{"role": "user", "content": "count to three"}],
                    "stream": True,
                },
                headers=headers,
            ) as stream:
                findings["streaming_status"] = stream.status_code
                first = ""
                async for line in stream.aiter_lines():
                    if line.strip():
                        first = line
                        break
                findings["streaming_supported"] = stream.status_code == 200 and first.startswith(
                    "data:"
                )
        except httpx.HTTPError as exc:
            findings["streaming_supported"] = False
            findings["streaming_error"] = type(exc).__name__

        # --- seed honoured? ---------------------------------------------------
        seeded = []
        for _ in range(2):
            r = await _post(
                client,
                {
                    **base,
                    "seed": 42,
                    "temperature": 0.9,
                    "messages": [{"role": "user", "content": "Name one colour."}],
                },
                headers,
            )
            seeded.append(
                r.json()["choices"][0]["message"]["content"] if r.status_code == 200 else None
            )
        findings["seed_honoured"] = bool(seeded[0]) and seeded[0] == seeded[1]

        # --- context limit ----------------------------------------------------
        findings["context_limit_probe"] = await _probe_context_limit(client, headers, base)

        # --- rate limit shape -------------------------------------------------
        findings["rate_limit"] = await _probe_rate_limit(client, headers, base)

        # --- latency ----------------------------------------------------------
        latencies = []
        for _ in range(LATENCY_SAMPLES):
            started = time.perf_counter()
            r = await _post(
                client,
                {**base, "messages": [{"role": "user", "content": "Say OK."}]},
                headers,
            )
            if r.status_code == 200:
                latencies.append((time.perf_counter() - started) * 1000)
        if latencies:
            latencies.sort()
            findings["latency_ms"] = {
                "n": len(latencies),
                "p50": round(statistics.median(latencies)),
                "p95": round(latencies[int(len(latencies) * 0.95) - 1]),
                "max": round(latencies[-1]),
            }

    findings["tokenizer_available"] = _tokenizer_available(s.hpc_model_name)
    return findings


async def _probe_context_limit(client, headers, base) -> dict:
    """Grow the prompt until the endpoint rejects it, to find the real ceiling."""
    filler = "word " * 512  # ~512 tokens per unit
    for units in (2, 4, 8, 16, 32, 64, 128):
        r = await _post(
            client,
            {**base, "messages": [{"role": "user", "content": filler * units}]},
            headers,
        )
        if r.status_code != 200:
            return {
                "rejected_at_approx_tokens": units * 512,
                "status": r.status_code,
                "message": r.text[:300],
            }
    return {"accepted_at_least_approx_tokens": 128 * 512}


async def _probe_rate_limit(client, headers, base) -> dict:
    """Fire a short burst and record the 429 shape, if one comes back."""

    async def one():
        return await _post(
            client, {**base, "messages": [{"role": "user", "content": "hi"}]}, headers
        )

    responses = await asyncio.gather(*(one() for _ in range(8)), return_exceptions=True)
    for r in responses:
        if isinstance(r, httpx.Response) and r.status_code == 429:
            return {
                "observed": True,
                "retry_after_header": r.headers.get("Retry-After"),
                "body": r.text[:300],
                "headers": {k: v for k, v in r.headers.items() if "limit" in k.lower()},
            }
    return {"observed": False, "note": "no 429 in an 8-request burst"}


def _tokenizer_available(model_name: str) -> dict:
    try:
        from transformers import AutoTokenizer  # type: ignore

        AutoTokenizer.from_pretrained(model_name)
        return {"available": True, "via": "transformers", "model": model_name}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}"}


TEMPLATE = """# HPC endpoint probe

Generated by `python -m scripts.probe_hpc`. Commit this file: the context
builder's token accounting and block budgets are configured from it.

**Status:** {status}

## Findings

```json
{findings}
```

## What this means for the context builder

| Question | Answer | Consequence |
| --- | --- | --- |
| OpenAI-compatible? | {openai} | `app/llm/hpc.py` posts to `/v1/chat/completions` |
| Separate system role? | {system} | If no, the SYSTEM block is prepended to the user turn instead |
| Real tokenizer? | {tokenizer} | If no, `orchestration/tokens.py` uses its heuristic |
| Context limit | {limit} | Sets `HPC_CONTEXT_LIMIT` |
| Streaming? | {streaming} | If yes, buffer server-side, run egress checks, then stream |
| Retry-After on 429? | {retry_after} | Honoured in preference to the backoff schedule |
| p50 / p95 latency | {latency} | Sets the read timeout and the frontend's progress threshold |
"""


def render(findings: dict) -> str:
    ctx = findings.get("context_limit_probe") or {}
    limit = (
        f"~{ctx['rejected_at_approx_tokens']} tokens (rejected)"
        if "rejected_at_approx_tokens" in ctx
        else f"at least ~{ctx.get('accepted_at_least_approx_tokens', 'unknown')} tokens"
    )
    latency = findings.get("latency_ms")
    return TEMPLATE.format(
        status="probed" if findings.get("base_url_configured") else "NOT RUN - HPC_BASE_URL unset",
        findings=json.dumps(findings, indent=2, sort_keys=True),
        openai=findings.get("openai_compatible", "unknown"),
        system=findings.get("system_role_honoured", "unknown"),
        tokenizer=(findings.get("tokenizer_available") or {}).get("available", "unknown"),
        limit=limit,
        streaming=findings.get("streaming_supported", "unknown"),
        retry_after=(findings.get("rate_limit") or {}).get("retry_after_header", "not observed"),
        latency=(f"{latency['p50']} ms / {latency['p95']} ms" if latency else "not measured"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    args = ap.parse_args()

    findings = asyncio.run(probe())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(findings), encoding="utf-8")
    print(json.dumps(findings, indent=2, sort_keys=True))
    print(f"\nwrote {args.out}")
    if not findings.get("base_url_configured"):
        print("\nHPC_BASE_URL is unset -- set it in .env and re-run before Task 11 tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
