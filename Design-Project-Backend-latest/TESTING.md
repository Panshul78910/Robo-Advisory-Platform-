# Test Plan

What has been proven, what has not, and how to close the gap.

**As of 7 October 2026.** 325 tests pass in under two seconds. That number is
reassuring in a way the evidence does not fully support, and this document exists
to say where the gap is.

> **The headline.** Every unit test passes, but the 33 integration tests have
> **never executed**, and **6 of 34 API operations** have ever been exercised
> against a live database. Four real bugs have already been found by running
> against the real target rather than by the suite — see `IMPLEMENTATION.md` §7.
> Expect more.

---

## Why the passing suite overstates confidence

The 325 passing tests are genuine and worth having. They cover trade arithmetic
to the cent, scorer determinism, suitability rules, the context budget, the
confidence policy and the no-formula invariant — the things that are hard to get
right and easy to break silently.

What they cannot cover is anything requiring a database or a model:

- Every repository query is exercised for its *signature*, not its SQL.
- Every transactional path (invest, rebalance, revert) is tested as pure
  arithmetic, never as a committed transaction.
- The LLM path is tested only against the deterministic stub.

That is a reasonable design — it is what makes the suite fast and free — but it
means "325 passing" answers a narrower question than it appears to.

The four bugs in `IMPLEMENTATION.md` §7 are the proof: all four survived 283
passing tests and appeared on the first real request.

---

## Tier 1 — unblocked, needs only a database

Nothing here waits on a decision. A local Postgres is enough.

```bash
docker compose up -d db
make reset        # migrate, seed, prices, corpus
make test         # the 33 skipped tests now run
```

`ALLOW_TESTS_AGAINST_REMOTE_DB=1` exists but points the suite at the shared
Supabase project. The tests roll back, but "roll back" is doing real work in that
sentence, and teammates share that project. Prefer local.

### 1.1 The 33 integration tests, never run

| Suite | Tests | What it would prove |
|---|---|---|
| `tests/integration/test_revert.py` | 6 | A.7 to the unit: post-revert holdings equal the pre-rebalance snapshot exactly; three events and three decisions recorded; **the append-only trigger actually fires** on an UPDATE attempt |
| `tests/integration/test_idempotency.py` | 5 | B.6: a retried POST returns the first result rather than trading twice, across invest, rebalance and revert; a reused key for a *different* request is rejected |
| `tests/integration/test_provenance.py` | 8 | C.6: the full trail — input, output, confidence, model and prompt version, sources **with passage text**, and the override preserving the pre-override AI output |
| `tests/integration/test_cross_client_isolation.py` | 8 | A.1(4) / D.4 against real rows: profile, holdings, audit, conversations and decision sources all scoped; role separation enforced |

`tests/unit/test_scoping.py` proves no repository *signature* accepts a client
id. These prove the queries actually filter. Both halves are needed; only one has
ever run.

### 1.2 API surface: 6 of 34 operations exercised

Exercised live against Supabase: `GET /healthz`, `POST /api/auth/login` (success
and failure), `GET /api/auth/me`, `GET /api/clients/me/profile`,
`GET /api/clients/me/eligible-portfolios`, `GET /api/admin/clients`.

**Never called, in any environment:**

*Transactional — highest risk, since these write money-shaped data*

```
POST  /api/clients/me/invest
POST  /api/clients/me/rebalance
GET   /api/clients/me/rebalance/preview
POST  /api/clients/me/rebalance/revert
GET   /api/clients/me/events
```

*Client-facing core*

```
PUT   /api/clients/me/profile          GET   /api/clients/me/holdings
POST  /api/clients/me/cka              GET   /api/clients/me/performance
GET   /api/clients/me/documents        POST  /api/clients/me/documents
GET   /api/clients/me/audit            GET   /api/clients/me/audit/{decision_no}
GET   /api/session/date                POST  /api/session/date
```

*Conversational*

```
POST  /api/chat
POST  /api/profiling/start
POST  /api/profiling/message
```

*Admin — all eleven*

```
GET   /api/admin/audit                 GET   /api/admin/escalations
GET   /api/admin/clients/{client_id}   POST  /api/admin/decisions/{id}/review
GET   /api/admin/debug/last-payload    GET   /api/admin/rag/stats
GET   /api/admin/rag/query             POST  /api/admin/rag/query
POST  /api/admin/rag/ingest            POST  /api/admin/rag/reset
```

A smoke pass over all 34 — one call each, asserting status and shape — is cheap,
and would likely surface the same class of fault the four known bugs came from.

---

## Tier 2 — blocked on a credential or a decision

### 2.1 The real LLM path has never executed

`OpenAICompatibleClient.complete()` has never made a successful call against
anything. The stub exercises the pipeline; it does not exercise HTTP.

- **HPC endpoint unreachable.** DNS resolves, TCP connects, then every byte is
  reset — TLS, plain HTTP and raw socket alike. Needs VPN, or the service to be up.
- **No OpenAI key configured.** The switch is implemented and unit-tested, but no
  live call has been made. Setting `OPENAI_API_KEY` and `LLM_BACKEND=openai`
  unblocks this immediately, at the cost of real spend.

Untested as a consequence:

| Behaviour | Why it matters |
|---|---|
| Real token counting | `app/orchestration/tokens.py` falls back to a heuristic (3.2 chars/token, +10% margin). Erring high costs a retrieved chunk; a real tokenizer would be exact. |
| The actual context limit | `HPC_CONTEXT_LIMIT=8192` is a **guess**. `make probe` measures it; `docs/hpc_probe.md` is still an unfilled template. |
| Separate system role | Gemma has no native system turn, and many serving stacks silently merge it into the first user message. The trust-labelled blocks survive either way, but this is unverified. |
| `Retry-After` on a real 429 | Honoured in code and unit-tested with a synthetic header. A real one has never been seen. |
| Streaming | `Payload.to_messages()` supports it and the pipeline buffers before egress checks. Never exercised. |

### 2.2 RAG has never been indexed against a real database

The chunker is well covered (10 tests, including whole-table preservation on the
placeholder policy document). What has not run:

- Ingestion into Supabase — `document`, `chunk` and `corpus_version` are empty.
- Retrieval against **real embeddings**. Only the `hash` backend has been used,
  which exercises the plumbing but is not semantic, so recall has never been
  observed.
- The relevance floor against real scores. The abstention behaviour C.4 rests on
  is therefore unverified in practice.

Needs the `rag` extra (`pip install -e ".[dev,rag]"`, ~2.5 GB for torch), or
`EMBEDDING_BACKEND=hash` for a non-semantic dry run.

---

## Tier 3 — unit-tested, never observed end to end

Each has solid unit coverage. None has been watched happen.

| Behaviour | Unit coverage | What an end-to-end run would add |
|---|---|---|
| Suitability refusal → escalation | `test_suitability.py` (19) | That the `escalation` row is actually written, in the same transaction, and appears in the admin queue |
| Disclosure non-suppressibility | `test_disclosure.py` (15) | That it survives a real generation, not only the stub's |
| Low confidence → review routing | `test_confidence.py` (21) | That `status='pending_review'` persists and the client is shown the review controls |
| Audit trail with real sources | `test_provenance.py` (skipped) | `decision_source` rows carrying real retrieved passage text |
| Profiling → risk category | `test_profiling.py` (28) | A client actually reaching a category, which would populate `eligiblePortfolios` — currently empty for everyone |

That last one is the cheapest high-value item: one profiling conversation turns
the dashboard from empty into demonstrable.

---

## Tier 4 — no coverage at all

Stated plainly rather than left to be assumed.

- **Concurrency.** `lock_latest_snapshot` takes `SELECT … FOR UPDATE` on the
  client row so two simultaneous rebalances serialise. Never tested under actual
  concurrency.
- **Latency under the pooler.** Transaction-mode pooling adds a hop and forbids
  prepared statements. No measurement exists.
- **`make up` under uvicorn.** Every live call so far used `TestClient`. The
  Windows event-loop fix is guarded by an AST check, not by actually serving.
- **Frontend integration.** Response shapes match the documented contract, but no
  frontend has consumed them. The `Decimal`-as-string bug is exactly the kind
  this would catch.
- **Document upload path.** Storage is implemented; no file has been uploaded.

---

## Checklist demonstration matrix

What to run in front of a supervisor for each sign-off item.

| Item | Demonstrate with | Ready? |
|---|---|---|
| A.1 profile, access control | Log in as two clients, show isolation; admin sees all five | partly — isolation unverified live |
| A.2 conversational profiling | `POST /api/profiling/start` → `/message`; repeat for an identical result | needs a run |
| A.3 suitability & gating | Request a portfolio above category; show refusal + escalation row | needs a run |
| A.4 grounded explanation & disclosure | `POST /api/chat`; then ask for the disclosure to be removed | needs RAG indexed |
| A.5 invest & holdings | Set session date, invest, check arithmetic against the price file | needs a run |
| A.6 rebalance | Rebalance at a later date; verify weights return to target | needs a run |
| A.7 revert | Revert; holdings match exactly; two audit records | **integration test covers this, once run** |
| B.1 tiers & orchestration | A request with the correlation id visible across all layers | needs `make up` |
| B.2 DB retrieval into prompt | `GET /api/admin/debug/last-payload`, showing the client-facts block | needs a chat call |
| B.3 ingestion | `POST /api/admin/rag/ingest`; show chunk count and a preserved table | needs RAG indexed |
| B.4 retrieval & store admin | `GET /api/admin/rag/query` with scores | needs RAG indexed |
| B.5 context assembly | `debug/last-payload`: five blocks, per-block tokens, dropped chunks | needs a chat call |
| B.6 resilience | `X-Fault-Inject` with each fault; show the distinct states | needs `ALLOW_FAULT_INJECTION=true` |
| B.7 session state & logging | A multi-turn chat surviving a restart; JSON logs | needs a run |
| C.1 dashboard | `GET /api/clients/me/holdings` plus the rebalance preview | needs a run |
| C.2 citations | Expand a citation to its exact passage | needs RAG indexed |
| C.3 risk explanation | "Why this category?" — grounded, no formula | needs a run |
| C.4 confidence | A low-confidence query showing caution and abstention | needs a run |
| C.5 review & override | `POST /api/admin/decisions/{id}/review`; final result reflects the human | needs a run |
| C.6 audit | `GET /api/admin/audit` full provenance for one decision | **integration test covers this, once run** |

Part D is deliberately out of scope until weeks 10–12; the guardrail hooks exist
and are wired, with no implementations behind them.

---

## Recommended order

1. **Start Docker, `make reset`, `make test`.** Unblocks 33 tests and the A.7 and
   C.6 demonstrations in one step. No decisions required.
2. **Smoke all 34 endpoints.** One call each — the cheapest way to find the next
   bug of the kind that has already bitten four times.
3. **Run one profiling conversation.** Populates a risk category and makes the
   dashboard demonstrable.
4. **Index the RAG corpus.** Unblocks A.4, B.3, B.4, C.2 and C.3.
5. **Reach a real model** — HPC via VPN, or an OpenAI key — then `make probe` and
   replace the guessed context limit with a measured one.
6. **Serve under `make up`** and point the frontend at it.
7. **Part D guardrails**, into the hooks that already exist.

Steps 1–3 need nothing from anyone, and would move more items from "implemented"
to "demonstrated" than everything else combined.
