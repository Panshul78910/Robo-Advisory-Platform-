"""Measure the guardrails (D.3 evidence): block rate on attacks, false-positive rate on
legitimate questions, for regex-only vs regex + Ollama judge.

  python -m scripts.eval_guardrails            # regex layer only (no Ollama needed)
  python -m scripts.eval_guardrails --judge    # regex + Ollama (needs ollama running)

Multi-turn cases have several turns; the last is the attack and earlier ones are history.
Writes redteam_report.csv so the numbers can go straight into the report.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CASES = pathlib.Path(__file__).resolve().parents[1] / "tests" / "redteam" / "cases.json"


async def _corpus_topics(use_judge: bool) -> tuple[str, ...]:
    """The document titles the pipeline hands the judge, read from the database.

    Without these the harness measured a judge that cannot see what the firm has
    published, while production (`pipeline.py`, `scope_topics=`) always supplies
    them -- so the two disagreed in the one direction that matters: legitimate
    questions the corpus answers were scored as refusals here and answered in
    reality. Hardcoding the titles instead would reintroduce the drift the
    `corpus_topics` lookup exists to remove.

    Only needed for the judge; the regex layer never sees topics. A database
    that is unreachable degrades to the old behaviour with a warning rather than
    failing the run.
    """
    if not use_judge:
        return ()
    try:
        from app.db.session import get_engine
        from app.rag.retrieve import corpus_topics

        async with get_engine().begin() as conn:
            return await corpus_topics(conn)
    except Exception as exc:  # noqa: BLE001 -- a measurement tool must still run
        print(f"warning: no corpus topics ({type(exc).__name__}); judging without them", flush=True)
        return ()


async def run(use_judge: bool) -> list[dict]:
    import os

    os.environ["JUDGE_ENABLED"] = "true" if use_judge else "false"
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.orchestration import guardrails as g

    topics = await _corpus_topics(use_judge)

    data = json.loads(CASES.read_text(encoding="utf-8"))
    rows = []
    for kind in ("attacks", "legit", "out_of_scope"):
        for case in data.get(kind, []):
            *hist, msg = case["turns"]
            v, _ = await g.screen_input(
                message=msg,
                intent="",
                history=tuple(hist),
                facts={"clientName": "Chloe Lim"},
                scope_topics=topics,
            )
            rows.append(
                {
                    "kind": kind,
                    "item": case.get("item", ""),
                    "message": msg[:90],
                    "blocked": v.blocked,
                    "by": ",".join(v.triggered),
                }
            )
    return rows


def summarise(label: str, rows: list[dict]) -> None:
    """Report both populations together.

    Reducing over-refusal reliably costs attack coverage unless both are measured
    at once (arXiv 2510.08158; ICLR 2026), so a run quoting one number without
    the other says nothing about whether the balance actually improved.
    """
    att = [r for r in rows if r["kind"] == "attacks"]
    oos = [r for r in rows if r["kind"] == "out_of_scope"]
    leg = [r for r in rows if r["kind"] == "legit"]
    caught = sum(r["blocked"] for r in att)
    refused_oos = sum(r["blocked"] for r in oos)
    fp = sum(r["blocked"] for r in leg)
    print(f"{label}:")
    print(f"   attacks blocked      {caught}/{len(att)} ({caught / len(att):.0%})")
    if oos:
        print(f"   out-of-scope refused {refused_oos}/{len(oos)} ({refused_oos / len(oos):.0%})")
    print(
        f"   FALSE REFUSAL RATE   {fp}/{len(leg)} ({fp / len(leg):.0%})"
        "  <- legitimate questions blocked"
    )
    for r in oos:
        if not r["blocked"]:
            print(f"   LEAKED  [{r['item']}] {r['message']}")
    for r in att:
        if not r["blocked"]:
            print(f"   MISSED  [{r['item']}] {r['message']}")
    for r in leg:
        if r["blocked"]:
            print(f"   FALSE+  {r['message']}  ({r['by']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", action="store_true")
    args = ap.parse_args()
    # Reading corpus topics opens a psycopg connection, which refuses Windows'
    # default Proactor loop. Without this the lookup raises, the warning path
    # swallows it, and the run silently measures a judge with no topics -- the
    # exact blind spot this harness was just corrected for.
    from app.core.eventloop import use_compatible_event_loop

    use_compatible_event_loop()
    rows = asyncio.run(run(args.judge))
    summarise("regex + judge" if args.judge else "regex only", rows)
    with open("redteam_report.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("wrote redteam_report.csv")
