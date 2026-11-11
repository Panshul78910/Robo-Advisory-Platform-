# Handoff for the backend owner

Found while connecting the frontend to the backend and replaying every request
the UI makes against a local Postgres with `LLM_BACKEND=stub` and
`EMBEDDING_BACKEND=hash`. All other calls matched the DTOs.

## Bugs / gaps that block checklist sign-offs

1. **Refused invest orders are never saved (A.3 items 3 and 4).**
   `POST /clients/me/invest` calls `audit.record_decision(..., escalate=...)` and
   then `raise SuitabilityRefused`. `get_connection()` uses `engine.begin()`, so the
   exception rolls the transaction back and the decision + escalation row vanish
   (the decision number is consumed: D-1002 is missing from the sequence, and
   `/admin/escalations` stays empty). Fix: return
   `JSONResponse(status_code=403, content=...)` with the same body instead of
   raising, so the transaction commits. Check the same pattern anywhere else a
   decision is recorded before an exception.
2. **No way to create a client account (A.1 sign-off 1).** `auth.py` has only
   `/auth/login` and `/auth/me`. Needs `POST /api/auth/register` (email, password,
   full name) that inserts `app_user` + `app_credential` + `client`, with the
   $50,000 starting balance. The frontend then needs a small sign-up form.
3. **`POST /clients/me/cka` just flips a flag.** Any logged-in client can call it
   directly and unlock P6–P8 and satellite exposure, bypassing the knowledge check
   (the frontend quiz is only a courtesy). Grade server-side: accept the answers,
   check them in Python, and only then set `cka_passed`.
4. **`greenlet` is missing from the dependencies** on Linux / Python 3.12:
   `pip install -e ".[dev]"` then fails with "SQLAlchemy asyncio requires greenlet".
   Use `sqlalchemy[asyncio]` in `pyproject.toml`.
5. **Chat grounding needs real embeddings.** With the `hash` embedder, "Why was I
   assigned this risk category?" abstained (correctly, since hash isn't semantic).
   Install the `rag` extra and use `EMBEDDING_BACKEND=local` before demoing C.2/C.3.
6. **Prices are generated placeholders**, not the instructor's Appendix C file, and
   no real LLM has been called yet (`docs/hpc_probe.md` is still a template).

## Part D — where each defence goes

`app/orchestration/guardrails.py` is empty on purpose: add callables to
`INGRESS_CHECKS` (before the model) and `EGRESS_CHECKS` (after). Suggested mapping:

| Item | Ingress / egress idea |
|---|---|
| D.1 hallucination | (1) RAG grounding is live; (2) abstention + enforced-citation rules in `prompts/system.md`; (3) egress check that every cited chunk ref is in `allowed_chunk_refs` and every number in the answer matches the DB facts (deterministic) |
| D.2 undesirable content | ingress topic/toxicity classifier; out-of-scope already routed by `intent.py` — add toxic/hateful and illegal-request categories |
| D.3 helpful vs harmless | tune thresholds with paired examples (a leak that got through, an over-refusal of a legit question); record both in the template |
| D.4 data leakage | egress scan for the formula constants, system-prompt fragments, other clients' codes, NRIC/email patterns; multi-turn: keep checking the whole conversation, not just the last message |
| D.5 adversarial prompts | direct and indirect injection (uploaded documents are not read into prompts today — keep it that way), jailbreak patterns, prompt-leak requests; keep trust-labelled blocks in `context.py` |

Evidence for each item goes in `docs/D_EVIDENCE_TEMPLATE.md` (copy per attack).
The frontend gives the demo surface: the chat shows confidence, cautions, refusals
and sources; the officer console's **System & vector store** tab shows the exact
payload last sent to the model; **Audit log** shows the decision trail.
