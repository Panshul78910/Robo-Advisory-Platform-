# Implementation Status

What this repository actually does, mapped to `BACKEND_SPEC.md` and to the
SC3098 project checklist.

**As of 7 October 2026.** 325 tests pass, 33 skip. `make lint` clean: ruff,
formatting, the clock check, and the import-linter contract.

> **How to read this.** "Implemented" means the code exists and unit tests pass.
> "Verified live" means it was exercised against the real Supabase database.
> Where something is a placeholder awaiting a course appendix, it says so.
>
> **`TESTING.md` is the companion to this file** and the more honest of the two:
> it catalogues what has *not* been proven. In short — the 33 integration tests
> have never run, and 6 of 34 API operations have ever been called live.

---

## 1. At a glance

| | |
|---|---|
| Spec tasks 1–15 | all implemented |
| API operations | 34, across 30 paths, every one with a response model |
| Application code | ~8,300 lines |
| Tests | ~3,100 lines — 325 passing, 33 skipped |
| Migrations | 9 files, 391 lines of SQL, all applied to Supabase |
| Database | live on Supabase: 5 clients, 10 instruments, 8 portfolios, 1,200 price rows |
| LLM | three backends: `stub` (working), `hpc` (unreachable), `openai` (no key set) |
| Repository | own git root, 1 commit, **12 files uncommitted** (the LLM backend switch) |

### What runs right now, verified against the live database

```
healthz            {'status': 'ok', 'db': True, 'llm_backend': 'stub'}
login c003         200  Chloe Lim
profile            C003 | missing fields: []
wrong password     401 unauthorized
client → admin     403 forbidden
admin sees         5 clients: C001…C005
```

### What is not done

- **No real model has ever been called.** The HPC endpoint is unreachable (TCP
  connects, then every byte is reset) and no OpenAI key is configured. So
  `docs/hpc_probe.md` is an unfilled template and `HPC_CONTEXT_LIMIT` remains a
  guess. Everything LLM-facing has only ever run against the stub.
- **The RAG corpus is not indexed on Supabase.** The pipeline works, but
  populating it needs either the `rag` extra (~2.5 GB of torch) or
  `EMBEDDING_BACKEND=hash` for non-semantic plumbing.
- **No client has completed risk profiling**, so `eligiblePortfolios` is empty
  for everyone. One profiling conversation fixes that.
- **Guardrail implementations are deliberately absent** — Part D work. The
  pipeline hooks exist and are wired; see §6.
- **The 33 integration tests have never executed.** They need a Postgres; Docker
  was never started in this environment. These are the tests that would prove
  Tasks 7 and 14 end to end.
- **26 of 34 API operations have never been called**, including every
  transactional endpoint. See `TESTING.md` §1.2 for the list.

---

## 2. The organising principle

> The LLM handles natural language. Deterministic Python handles anything that
> must be reproducible, auditable or policy-sensitive.

Risk scoring, suitability rules, trade arithmetic and valuations are ordinary
Python. The model is asked only to phrase things.

The practical consequence: **the deterministic core has no LLM dependency at
all.** Holdings, performance, invest, rebalance, revert and the audit log all
work with the model unreachable. That is what makes the B.6 degradation ladder
possible, and it is why 325 tests run in under two seconds without a single
model call.

---

## 3. Spec tasks 1–15

| Task | Status | Where | Evidence |
|---|---|---|---|
| 1 Skeleton & local stack | done | `Makefile`, `docker-compose.yml`, `/healthz` | healthz returns `db: true` |
| 2 Migrations | done | `supabase/migrations/` (9 files) | all 9 recorded in `schema_migrations` on Supabase |
| 3 Core primitives | done | `app/core/` | `test_money.py` (14), clock check in `make lint` |
| 4 Principal-scoped repositories | done | `app/db/repositories/` | `test_scoping.py` (7) + `test_cross_client_isolation.py` (8) |
| 5 Trading arithmetic | done | `app/domain/trading/arithmetic.py` | `test_arithmetic.py` (15), to the cent |
| 6 Seed & synthetic prices | done | `scripts/seed.py`, `gen_prices.py`, `load_prices.py` | live on Supabase |
| 7 Execution service | done | `app/domain/trading/execution.py` | `test_revert.py` (6), `test_idempotency.py` (5) |
| 8 Suitability engine | done | `app/domain/suitability/` + `policy/suitability.yaml` | `test_suitability.py` (19) |
| 9 Risk scorer | done | `app/domain/risk/` | `test_determinism.py` (9), import-linter contract |
| 10 LLM client & resilience | done | `app/llm/` | `test_llm_resilience.py` (21), `test_llm_backend_switch.py` (17) |
| 11 Context assembly | done | `app/orchestration/context.py` | `test_context_budget.py` (17), `test_no_formula_in_payload.py` (65) |
| 12 RAG pipeline | done | `app/rag/` | `test_chunker.py` (10); not yet indexed on Supabase |
| 13 Orchestration pipeline | done | `app/orchestration/pipeline.py` | `test_disclosure.py` (15), `test_confidence.py` (21) |
| 14 Audit service | done | `app/audit/service.py` | `test_provenance.py` (8) |
| 15 API surface | done | `app/api/` | 34 operations, OpenAPI at `/openapi.json` |

### Spec order-of-work checkpoint

The spec requires stopping after Task 7 to confirm the deterministic core has
zero LLM dependencies. It does: `app/domain/`, `app/db/` and `app/audit/` import
nothing from `app/llm/` or `app/orchestration/`, and the holdings, performance,
invest, rebalance, revert and audit endpoints run with `LLM_BACKEND` unset.

---

## 4. The ten invariants

These are graded properties, not style preferences. Each is enforced
mechanically rather than by convention.

| # | Invariant | Enforced by | Test |
|---|---|---|---|
| 1 | FastAPI is the only database client | no key ever reaches the browser | — |
| 2 | Decision & position data append-only | `revoke` **and** a rejecting trigger (`007`) | `test_provenance.py::test_updating_a_decision_is_rejected` |
| 3 | `Decimal` for money, units, prices | `app/core/money.py` raises `TypeError` on a float | `test_money.py` |
| 4 | Only `app/core/clock.py` reads the wall clock | `scripts/check_clock.py` in `make lint` | — |
| 5 | No repository method takes a `client_id` | signature inspection | `test_scoping.py` |
| 6 | The model never generates SQL | all queries hand-written, parameterised | — |
| 7 | One context builder for every payload | single `ContextBuilder` | `test_context_budget.py` |
| 8 | Scoring weights never reach a prompt | import-linter contract | `test_no_formula_in_payload.py` (65) |
| 9 | RLS on with no policies | `007_lockdown.sql` | live: 28/28 tables |
| 10 | Decision + action in one transaction | `app/audit/service.py` never opens its own | `test_revert.py` |

**Invariants 5 and 8 are the two worth understanding**, because they make the
corresponding attacks structurally impossible rather than merely discouraged.

- **Cross-client access (A.1, D.4).** A repository binds its client id at
  construction, from the verified JWT. No public method accepts one. The model
  cannot express a different client id because there is no parameter to put it
  in — persuasion is irrelevant.
- **Formula confidentiality (A.2, D.4).** Three independent barriers: the formula
  is never ingested into the corpus; `weights.py` is unreachable from
  `app.orchestration.*` and `app.llm.*`; and a test asserts no assembled payload
  contains any of its constants. **Both guards were verified to fail on a
  deliberately introduced violation**, then reverted.

---

## 5. Checklist coverage

### Part A — Domain-specific

| Item | Status | Notes |
|---|---|---|
| **A.1** Account, profile, access control | implemented | 5 synthetic clients + admin seeded and live. Admin sees all five (verified). Document upload stored, deliberately not parsed — reading uploads into prompt context is the D.5 indirect-injection vector. |
| **A.2** Conversational risk profiling | implemented | Slot-filling conversation; answers normalised deterministically so phrasing cannot move the score. Refuses to score an incoherent profile (client C004 is deliberately contradictory). Formula never disclosed. |
| **A.3** Suitability rules & gating | implemented | Category caps, four overrides, CKA gating, per-category satellite caps. Refusal produces an escalation record. **Appendix E is a YAML edit, not a code change.** |
| **A.4** Grounded explanation & disclosure | implemented | Disclosure appended structurally in pipeline stage 8 — 15 tests confirm it survives every suppression attempt. Out-of-scope requests declined. |
| **A.5** Simulated investment & holdings | implemented | Session-date pricing throughout; no live market data anywhere. Arithmetic verified to the cent. |
| **A.6** On-demand rebalancing | implemented | Round-trip test asserts post-rebalance weights match target within 0.01%. Different session dates produce different trades. |
| **A.7** Rebalance revert | implemented | Appends an inverse snapshot; never updates or deletes. Integration test asserts exact restoration. |

### Part B — Software / Architecture

| Item | Status | Notes |
|---|---|---|
| **B.1** Micro-architecture & orchestration | implemented | Three tiers; ten-stage pipeline with a fixed order. Correlation id spans all layers. |
| **B.2** Local DB integration & retrieval | implemented | Live on Supabase. Typed retrieval functions; structured facts injected as a labelled data block, never as instruction. |
| **B.3** Vector store ingestion | implemented | Heading-aware chunking; **tables kept whole with their captions**, even oversized. Idempotent by content hash. |
| **B.4** RAG retrieval & store management | implemented | Hybrid dense + keyword, fused by reciprocal rank fusion. Relevance floor turns an ungroundable question into an abstention. Admin endpoints expose scores. |
| **B.5** Context assembly & window management | implemented | Five delimited blocks with explicit trust labels. Budget-and-priority degradation; system prompt and user message never truncated. |
| **B.6** Exception handling & resilience | implemented | Separate connect/read timeouts, bounded backoff with jitter, `Retry-After` honoured, circuit breaker, fault injection. Each failure mode has its own HTTP status and message. |
| **B.7** Session state & logging | implemented | State checkpointed in `conversation.state`, not process memory. Two separate log streams — engineering (JSON, redacted) and audit (append-only, in-transaction). |

### Part C — Explainability / Human oversight

| Item | Status | Notes |
|---|---|---|
| **C.1** Dashboard of holdings & performance | implemented | Per-instrument storage; asset-class view is a DTO-layer aggregation only. Rebalance preview shows drift and proposed trades before execution. |
| **C.2** Source attribution & traceability | implemented | `passage_text` is **copied** into `decision_source`, not referenced — a citation shown in week 8 still expands after a week 10 re-ingest. |
| **C.3** Risk classification & explanation | implemented | Explains via ranked contributing factors, naming factors but never weights. |
| **C.4** Confidence & uncertainty | implemented | Four observable signals: retrieval quality, scope, profile completeness, ambiguity. Never self-reported. Confidence is `null` when abstained, never `0`. |
| **C.5** Client-in-the-loop review & override | implemented | Low-confidence responses are *routed* to review, not merely flagged. `decision_override` preserves the pre-override AI output alongside the human decision. |
| **C.6** Decision audit log & provenance | implemented | One table, one schema, every feature. Full trail retrievable: input, output, confidence, model and prompt version, sources with text, override. |

### Part D — AI Safety Testing

**Not implemented, by design.** The spec places guardrail implementations out of
scope; Part D supplies them in weeks 10–12.

What exists now is the *scaffolding*, so a defence lands at a known seam instead
of being retrofitted into scattered call sites:

- `app/orchestration/guardrails.py` — ingress and egress registration points, both
  already iterated by the pipeline, with every verdict recorded.
- Structural defences that are **not** guardrails and are already live:
  trust-labelled payload blocks, the formula's absence from the corpus,
  audience filtering at query time, principal-bound repositories, and the
  structurally appended disclosure.

---

## 6. Deliberate design decisions

**Trade arithmetic settles to the cent.** Units are truncated, never rounded up,
so a purchase cannot overspend; the residual lands in cash. Because cash settles
at 2dp while units carry 6dp, value is conserved to within about half a cent per
trade — settlement rounding, not leakage, and the tests assert the bound
explicitly rather than pretending exact equality holds.

**Tables are never split during chunking.** The policy corpus is substantially
tabular. A chunk holding three category names and none of their caps retrieves
well on keywords but grounds an explanation in a fragment, which is worse than
returning nothing.

**Retrieval is hybrid.** Dense embeddings are weak on rare literal tokens, and
this corpus is full of them — *Capital Preservation*, *CKA*, *satellite
exposure*. A keyword arm runs alongside the vector arm.

**Confidence is measured, not self-reported.** A model's opinion of its own
certainty is not a measurement.

**Window management is budget-and-priority.** Under pressure the builder sheds
low-scoring chunks, then old history verbatim (never summarised — that would
spend the scarce shared endpoint to save tokens), then low-priority facts.
Truncating from the end would discard the guardrails in exactly the long
adversarial conversation where they matter most.

**Supabase is a Postgres, nothing more.** This service speaks the Postgres wire
protocol directly and never calls the Supabase REST API, so it needs no project
key — neither `sb_publishable_…`/`sb_secret_…` nor the deprecated
`anon`/`service_role` JWTs. Migration `008` moved credentials out of
Supabase's `auth.users` into `app_credential`, a table this project owns.

**Three LLM backends, one client.** `LLM_BACKEND` selects `stub`, `hpc` or
`openai`. The shared course endpoint and OpenAI both speak chat-completions, so
rather than a second client there is one generalised
[`OpenAICompatibleClient`](app/llm/openai_compatible.py) with
[`providers.py`](app/llm/providers.py) supplying what differs — URL, key, model.

That matters for grading rather than tidiness: the B.6 resilience behaviour
(timeouts, bounded retries, honoured `Retry-After`, circuit breaker) is literally
the same code on both, so it cannot drift between them. A test asserts the two
configs share every resilience field. `OPENAI_BASE_URL` is overridable, so Azure
OpenAI or a local proxy works through the same path.

Misconfiguration **fails loudly rather than silently falling back to the stub** —
that fallback would be the worst outcome, since a demo would look fine while
answering from canned text.

**Configuration faults are not request faults.** `ValidationError` deliberately
suppresses its detail so a request error cannot leak internals. Using it for
configuration left operators reading only *"The request could not be processed as
submitted"* when a backend was misconfigured.
[`ConfigurationError`](app/core/errors.py) now carries the specific reason
(`"LLM_BACKEND=openai but OPENAI_API_KEY is not set"`) into logs and startup,
while the client still sees a generic 503.

---

## 7. Bugs found by running against the real target

Four faults survived 283 passing tests and appeared only when the app was
pointed at real Supabase from Windows. All four are fixed and now guarded.

| Bug | Why the tests missed it | Guard |
|---|---|---|
| Windows `ProactorEventLoop` is incompatible with psycopg async — **the entire API was dead on Windows** | only manifests on Windows | AST check that `app/main.py` selects the loop before its imports |
| Raw `psycopg.connect` bypassed the pooler's prepared-statement fix | only on the transaction pooler, and only via `executemany` — single statements survive until the fifth execution | AST scan: only `app/db/session.py` may open a raw connection |
| SQLAlchemy could not decode any Postgres enum | only on the first *read* of an enum column; writes were fine | `test_enum_parity.py` parses the migration and compares |
| Decimals serialised as JSON strings (`"0"` not `0`) | never raises — JavaScript silently concatenates instead of adding | `test_dto_serialisation.py` scans every DTO class |

The first two guards were **verified by reintroducing each bug** and confirming
the test fails. A guard nobody has seen fail is decoration.

The lesson worth carrying: three of the four appeared only because we pointed at
the real target early. A green local suite proved less than one live request.

---

## 8. Safety rails around the shared database

Pointing the wrong process at a shared database is the expensive mistake, so
three guards exist:

- **`migrate --reset` refuses a non-local host.** It drops the entire `public`
  schema. `--force-remote-reset` overrides, deliberately verbose.
- **The test suite is pinned to local**, overriding the environment rather than
  defaulting, so a stray `DB_TARGET=supabase` cannot redirect it.
  `ALLOW_TESTS_AGAINST_REMOTE_DB=1` opts out.
- **A `DATABASE_URL` disagreeing with `DB_TARGET` is a startup error**, not a
  silent override.

`make check` reports configuration and schema without printing any secret —
names, hosts, ports and string lengths only. Safe to paste.

---

## 9. Test inventory

325 passing, 33 skipped. No test makes a live model call of any kind: the suite
forces `LLM_BACKEND=stub`, overriding the environment, so no run can reach the
HPC endpoint or bill an OpenAI account.

| Suite | Tests | Covers |
|---|---|---|
| `test_no_formula_in_payload` | 65 | invariant 8, across every intent × message combination |
| `test_profiling` | 28 | A.2 slot parsing, coherence refusal, phrasing-independence |
| `test_llm_resilience` | 21 | B.6 error taxonomy, retry, circuit breaker |
| `test_confidence` | 21 | C.4 signals, abstention, intent routing |
| `test_dto_serialisation` | 20 | Decimal-as-number across every DTO |
| `test_suitability` | 19 | A.3 caps, overrides, CKA, satellite limits |
| `test_db_target` | 19 | local/Supabase switch and its guards |
| `test_context_budget` | 17 | B.5 degradation order, never-truncated blocks |
| `test_llm_backend_switch` | 17 | stub/hpc/openai selection, loud misconfiguration |
| `test_llm_env_aliases` | 15 | course `LLM_*` names, URL normalisation |
| `test_disclosure` | 15 | A.4 non-suppressibility |
| `test_arithmetic` | 15 | A.5–A.7 to the cent |
| `test_money` | 14 | invariant 3 |
| `test_chunker` | 10 | B.3 table preservation |
| `test_determinism` | 9 | A.2 reproducibility under shuffled input |
| `test_hpc_url` | 8 | endpoint URL forms |
| `test_scoping` | 7 | invariant 5, by signature inspection |
| `test_runtime_guards` | 7 | the Windows and pooler faults |
| `test_enum_parity` | 4 | models vs migration |
| **integration** (33, skipped) | | revert exactness, idempotency, provenance, cross-client isolation |

The integration suite skips automatically when `DATABASE_URL` is unreachable, so
`make test` is useful without Docker. **These are the tests that would prove
Tasks 7 and 14 end to end, and they have never run.** Starting Docker and running
`make reset` is the highest-value next step.

---

## 10. Placeholders awaiting course appendices

Each is isolated so the real thing is a data edit, not a code change.

| Appendix | Placeholder | Swap effort |
|---|---|---|
| **B** risk formula | `app/domain/risk/weights.py` — constants only | edit that file; `app/domain/risk/scorer.py` untouched |
| **C** price file | `scripts/gen_prices.py` synthetic random walk | replace the file; only `parse_price_file` may need changing |
| **D** client profiles | `scripts/_fixtures.py` — 5 varied clients, one contradictory | edit the data |
| **E** suitability policy | `policy/suitability.yaml` | edit the YAML |
| **G** disclosure block | `policy/disclosure.md` | edit the Markdown |

The RAG corpus currently holds one placeholder policy document
(`docs/corpus/company-suitability-policy.md`) containing four tables, so the
chunker's table-preservation behaviour is exercised on realistic material.

---

## 11. Next steps, in order of value

**`TESTING.md` is the detailed version of this list**, with a tier per blocker
and a checklist-demonstration matrix. In summary:

1. **Start Docker, `make reset`, `make test`.** 33 integration tests that prove
   revert exactness, idempotency and provenance have never executed. No decision
   required from anyone.
2. **Smoke all 34 endpoints.** Only 6 have ever been called live. One call each
   is the cheapest way to find the next bug of the kind that has already bitten
   four times.
3. **Complete one profiling conversation** so a client has a risk category and
   the dashboard has something to show.
4. **Index the RAG corpus** on Supabase, so citations are real.
5. **Reach a real model** — HPC via VPN, or an OpenAI key — then `make probe` to
   replace the token-count heuristic and the guessed context limit.
6. **Commit the 12 pending files.** A month of work (the LLM backend switch) is
   currently untracked.
7. **Replace placeholders** as the appendices arrive.
8. **Part D guardrails**, into the hooks that already exist.

Steps 1–3 need nothing from anyone and would move more items from "implemented"
to "demonstrated" than everything else combined.
