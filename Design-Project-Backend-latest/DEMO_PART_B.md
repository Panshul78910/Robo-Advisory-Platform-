# Demonstrating Part B

Seven items, seven demonstrations. Every command below was **run in PowerShell**
against this backend on 2026-10-12, after the move to local PostgreSQL 17, and
every number quoted is what it actually returned.

> **These are PowerShell commands, not bash.** In PowerShell `curl` is an alias
> for `Invoke-WebRequest`, so a copied `curl -s -H ...` fails with
> *"Missing an argument for parameter 'SessionVariable'"*. There is a real
> `curl.exe` in `C:\Windows\System32`, but bash line continuations (`\`) and
> `grep` still would not work, so the commands below are native PowerShell.
> If you prefer bash, run them in Git Bash instead and the usual `curl` syntax
> applies.

## Setup

**Run Part B with the Part D judge switched off.** The judge is a Part D
guardrail; nothing in Part B needs it, and leaving it on makes every request
6–35× slower (2.6s becomes 16–90s) while hiding the circuit breaker completely.
That is not hiding anything — Part D has its own passes where the judge is the
subject.

**Terminal 1** — the backend. Keep it visible; the demo reads its log:

```powershell
$env:JUDGE_ENABLED = "false"          # Part B speed; Part D demos this separately
$env:ALLOW_FAULT_INJECTION = "1"      # required by B.6, read once at boot
$env:LOG_FORMAT = "pretty"            # multi-line log, readable from the back of a room
$env:HF_HUB_DISABLE_PROGRESS_BARS = "1"   # suppress a one-off model-download bar
python -m scripts.serve --port 8000
```

`LOG_FORMAT=pretty` lays each record out over several lines and draws nested
lists -- retrieved chunks, context blocks -- as aligned tables. It is the same
records as `LOG_FORMAT=json`, through the same redaction, so nothing is shown
in one mode that is withheld in the other. If a marker asks to see the
structured log that B.7 claims, restart with `LOG_FORMAT=json` and ask one
question: the JSON line is the record, and pretty is a view of it.

**Ask one throwaway question before the demo starts.** The first retrieval of a
server's life loads the embedding model, which costs ~12s and prints a Hugging
Face notice that bypasses the log entirely. Warming it up means the first
question the room sees is answered in ~3s against a clean log.

**Terminal 2** — the commands. Paste this block once to connect:

```powershell
$base = "http://localhost:8000"
function Login($e) {
  (Invoke-RestMethod "$base/api/auth/login" -Method Post -ContentType "application/json" `
     -Body (@{email=$e; password="password123"} | ConvertTo-Json) -TimeoutSec 30).accessToken
}
$CLIENT = Login "c002@roboadvisor.local"
$ADMIN  = Login "admin@roboadvisor.local"
$H  = @{Authorization = "Bearer $CLIENT"}
$HA = @{Authorization = "Bearer $ADMIN"}
```

Two PowerShell details that matter live: always pass **`-UseBasicParsing`** to
`Invoke-WebRequest` (without it, PowerShell 5.1 tries to use the Internet
Explorer engine and can stall on a first-run prompt), and remember that
PowerShell **throws on any non-2xx response**, so anything demonstrating an
error needs `try/catch`.

Say one thing before starting: the **first chat request takes about 18 seconds**
while the 90 MB embedding model loads; every one after is ~2.6s. Nothing else
needs to be running — the database is a local Postgres service, and Ollama is
only for Part D.

---

## B.1 Micro-architecture and orchestration

**Claim:** three tiers, a fixed ten-stage pipeline, one correlation id spanning
the request.

```powershell
$r = Invoke-WebRequest "$base/api/chat?session_date=2026-03-02" -Method Post -UseBasicParsing `
       -Headers $H -ContentType "application/json" `
       -Body (@{message="Why was I assigned this risk category?"} | ConvertTo-Json) -TimeoutSec 200
"X-Correlation-Id : " + $r.Headers['X-Correlation-Id']
($r.Content | ConvertFrom-Json) | Select-Object decisionId, confidence, confidenceLabel
```

```
X-Correlation-Id : ec704dc877ed4496a89825459f26a749
decisionId       : D-1172     sources: 6
```

That id is the thread for the rest of the demo — it appears in the log, in the
audit record, and in any error the client sees. The client gets a short opaque
decision reference; the officer gets the full record behind it.

**Why this demonstrates B.1.** One request crossed all three tiers — the API
route, the orchestration pipeline, the data and model layers — and a *single*
identifier followed it the whole way, ending in a numbered decision. That is the
observable signature of an orchestrated pipeline: if these were independent
calls stitched together ad hoc, there would be nothing threading them, and no
single point at which the result became a recorded decision. The correlation id
is the architecture made visible.

## B.2 Local database integration and retrieval

**Claim:** live on a local PostgreSQL 17, with structured client facts fetched
by typed functions and injected as facts rather than prose.

```powershell
Invoke-RestMethod "$base/healthz"
$p = Invoke-RestMethod "$base/api/admin/debug/last-payload" -Headers $HA -TimeoutSec 30
$p.blockTokens
```

```
status ok | db True | llm_backend hpc
SYSTEM 462 | CLIENT_FACTS 426 | RETRIEVED_POLICY 605 | HISTORY 975 | USER_MESSAGE 22
_method tokenizer | _dropped_chunks 0
```

`CLIENT_FACTS: 426` is the point: the client's profile *and* their
per-asset-class holdings came out of Postgres into the prompt as a delimited,
trust-labelled block. Open `psql -d roboadvisor` beside it to show the same rows
at both ends.

Worth mentioning: this ran on Supabase until today and moved to local Postgres
by `pg_dump` and restore — 18 tables, row counts identical, pgvector 0.8.0 built
from source for PG17. The data layer is portable because nothing above it knows
which Postgres it is talking to.

**Why this demonstrates B.2.** `db: true` only proves a connection exists. The
number that proves *integration* is `CLIENT_FACTS: 426` — that is this client's
profile and holdings, read from Postgres by typed queries and measurably present
in the prompt as a labelled block. Showing the same rows in `psql` closes the
loop from table to prompt. And the fact that the whole system moved databases
today without a line changing above the repository layer is the strongest
evidence that persistence is properly isolated behind an interface.

## B.3 Vector store ingestion

**Claim:** heading-aware chunking that keeps tables whole with their captions,
embedded locally, versioned on every ingest.

```powershell
$s = Invoke-RestMethod "$base/api/admin/rag/stats" -Headers $HA -TimeoutSec 30
"$($s.versions[0].chunks) chunks in $($s.versions[0].label)"
```

```
159 chunks in live-887c1d77a2e9a8f3
```

Five documents, 159 chunks, all embedded:

```
  93  Fsg G04 Fair Dealing Guidelines 2024
  24  Cmg G02 Digital Advisory Guidelines
  18  Feat Principles 2018
  17  Company Suitability Policy
   7  Appendix G Platform Terms And Disclosures
```

Four of those are **real MAS and industry documents ingested from PDF**, not
authored fixtures — which is the point worth making: the pipeline parses
documents the team did not write to a convenient shape.

The version label is stamped with the embedder identity
(`local:sentence-transformers/all-MiniLM-L6-v2`), which matters because vectors
are only comparable to vectors from the same model — an index built by one
embedder and queried by another returns near-zero similarity for everything.
`app/rag/retrieve.py::warn_on_embedder_mismatch` checks the stamp against the
live embedder once per corpus version and logs an error if they differ, because
that failure is otherwise invisible: every similarity collapses toward zero and
the system abstains on every question while merely looking cautious.

Five versions exist; retrieval uses the newest. `make ingest` creates another.

**Why this demonstrates B.3.** Ingestion is not a file load; it is parse →
heading-aware chunk → embed → persist, and the stats endpoint shows the output
of all four as one addressable unit. The version label is the key part: each
ingest produces a new, labelled corpus version stamped with the embedder that
produced its vectors. That makes ingestion repeatable and auditable — you can
say which model embedded which chunks, which is precisely what you cannot do if
vectors are just written into a table.

**Be ready for this one.** *How do you know chunking did not cut a rule in
half?* Chunk size and overlap are configurable
(`CHUNK_TARGET_TOKENS`, `CHUNK_OVERLAP_RATIO`), splits are taken at headings
rather than at a fixed character count, and the overlap exists so a rule
spanning a boundary appears in both chunks. The retrieved `section` field in
every citation is the visible evidence that a chunk corresponds to a real
heading in the source document.

## B.4 RAG retrieval and store management

**Claim:** hybrid dense + keyword retrieval fused by reciprocal rank fusion,
with a calibrated relevance floor, and a managed store behind authenticated
endpoints.

### 1. What is in the store

```powershell
$s = Invoke-RestMethod "$base/api/admin/rag/stats" -Headers $HA -TimeoutSec 30
"active corpus: $($s.activeCorpusVersionId)"
$s.versions | Format-Table label, documents, chunks, clientVisibleChunks, unembedded
```

```
active corpus: 9e337198-...
label                        documents chunks clientVisibleChunks unembedded
live-887c1d77a2e9a8f3                5    159                 159          0
live-0bb7edb1f7458f2d                1     17                  17          0
bootstrap-0bb7edb1f7458f2d           1     17                  17          0
```

`unembedded 0` is the column to point at: every chunk has a vector, so nothing
is silently unsearchable. Re-ingesting creates a **new** corpus version rather
than overwriting, which is why older versions are still listed — citations
issued last week still resolve.

### 2. Semantic matching, proved by paraphrase

Paste the helper once:

```powershell
function Q($text, $k = 3, $aud = $null) {
  $u = "$base/api/admin/rag/query?query=" + [uri]::EscapeDataString($text) + "&top_k=$k"
  if ($aud) { $u += "&audience=$aud" }
  $r = Invoke-RestMethod $u -Headers $HA -TimeoutSec 60
  "== $text   topScore $($r.topScore)"
  $r.results | ForEach-Object { "   {0,-8} {1,-44} {2}" -f $_.score, $_.chunkRef, $_.section }
}
```

Two questions with **no content words in common**, both reaching the same
passage:

```powershell
Q "what happens if I want to complain about advice I was given"
Q "customer grievance escalation process"
```

```
== what happens if I want to complain about advice I was given   topScore 0.40446
   0.40446  FSG-G04_FAIR_DEALING_GUIDELINES_2024#90      5.2 Handling Complaints Independently
   0.38931  FSG-G04_FAIR_DEALING_GUIDELINES_2024#69      4.3 Providing Relevant Information
   0.37743  FSG-G04_FAIR_DEALING_GUIDELINES_2024#8       FAIR DEALING OUTCOME FIVE

== customer grievance escalation process   topScore 0.48677
   0.48677  COMPANY-SUITABILITY-POLICY#16                8. Review and escalation
   0.45655  FSG-G04_FAIR_DEALING_GUIDELINES_2024#90      5.2 Handling Complaints Independently
```

This is the single most convincing query to run live. *Grievance* appears
nowhere in the corpus and *complain* appears nowhere in the second question,
yet both retrieve **Handling Complaints Independently**. Keyword search cannot
do that; it is the embedding doing the work -- literally, for these two: the
keyword arm returns **zero** rows for both, so every row shown came from the
vector arm alone. If you want to prove that rather than assert it, the
`retrieval complete` log line reports `keyword_candidates 0` for exactly this
question.

Same effect on a different document:

```powershell
Q "robo advisor oversight obligations"
```

```
== robo advisor oversight obligations   topScore 0.51647
   0.51647  CMG-G02_DIGITAL_ADVISORY_GUIDELINES#21      FAA-N16
   0.50927  CMG-G02_DIGITAL_ADVISORY_GUIDELINES#9       GUIDELINES ON PROVISION OF DIGITAL ADVISORY
```

The document never says *robo advisor* — it says *digital advisory*.

### 3. Why the keyword arm is still there

```powershell
Q "CKA"
Q "satellite exposure"
```

```
== CKA   topScore 0.46352
   0.46352  COMPANY-SUITABILITY-POLICY#11                5. Customer Knowledge Assessment
   0.32677  COMPANY-SUITABILITY-POLICY#12                5.1 Satellite exposure limits

== satellite exposure   topScore 0.60617
   0.60617  COMPANY-SUITABILITY-POLICY#12                5.1 Satellite exposure limits
```

A three-letter acronym is exactly where dense retrieval is weakest and literal
matching is strongest — which is the argument for fusing two arms instead of
picking one. `satellite exposure` at **0.606** is the strongest hit in the
corpus, and it is the term clients actually ask about.

### 4. The floor, and the separation it sits in

```powershell
Q "risk tolerance assessment"
Q "how do I bake sourdough bread"
Q "zxqw plumbing walrus"
```

```
risk tolerance assessment     topScore 0.57987
how do I bake sourdough bread topScore 0.14363
zxqw plumbing walrus          topScore 0.13535
```

The floor sits at **0.34** (`RELEVANCE_FLOOR`), in the gap between genuine
questions (0.40-0.70) and nonsense (0.00-0.27). Say that it was **calibrated by
measuring both populations after the last ingestion**, not guessed: the previous
value of 0.22 stopped being safe when four new documents raised the nonsense
ceiling to 0.2683. That recalibration is the difference between a tuned system
and a magic number.

Note that this admin endpoint **deliberately does not apply the floor** — an
admin inspecting the store needs to see what was rejected and why, which is
precisely what the floor hides from a client. To show the floor actually firing,
ask the *client* chatbot the same nonsense question and watch Terminal 1:

```
02:21:40 INFO    app.rag.retrieve  retrieval below relevance floor  [502af83f]
    best_similarity  0.31344
    fused_top        0.5
    floor            0.34
```

### 5. A successful retrieval is logged too (execution-log evidence)

Ask the client chatbot a real policy question and watch Terminal 1:

```
02:22:49 INFO    app.rag.retrieve  retrieval complete  [a1e8d712]
    dense_candidates    20
    keyword_candidates  0
    fused_candidates    20
    kept                6
    top_k               6
    relevance_floor     0.34
    best_similarity     0.57703
    floor_applied       True
    chunks  (6)
        chunk_ref                                similarity  fused_score  section
        FSG-G04_FAIR_DEALING_GUIDELINES_2024#90     0.57703          0.5  5.2 Handling Complaints Independently and E...
        FSG-G04_FAIR_DEALING_GUIDELINES_2024#91     0.57443      0.49194  5.3 Resolving Complaints Promptly
        FSG-G04_FAIR_DEALING_GUIDELINES_2024#88     0.56283      0.48413  5.2 Handling Complaints Independently and E...
        FSG-G04_FAIR_DEALING_GUIDELINES_2024#89     0.50266      0.47656  5.2 Handling Complaints Independently and E...
        FSG-G04_FAIR_DEALING_GUIDELINES_2024#71     0.47065      0.46923  4.3 Providing Relevant Information
        FSG-G04_FAIR_DEALING_GUIDELINES_2024#16     0.45488      0.46212  1.3 Aligning Organisational Policies and Pr...
```

Both scores are logged per chunk on purpose. `similarity` is real cosine
distance — that is what semantic means. `fused_score` is the RRF rank blend.
Logging only the fused score would mislead: RRF scores *positions*, so the dense
arm's best hit always lands at rank 1 and scores a near-constant however
irrelevant the corpus is. Gating relevance on that number would mean the floor
could never fire.

### 6. The audience filter is real, not decorative

```powershell
Q "suitability assessment" 3 "client"
Q "suitability assessment" 3 "admin"
```

```
== suitability assessment  (audience=client)   topScore 0.38189   3 results
== suitability assessment  (audience=admin)    topScore            0 results
```

No admin-only material has been ingested, so the `admin` filter correctly
returns nothing. That is the point: the filter is applied at query time rather
than assumed, and it is the **second** independent barrier around confidential
material — the first excludes it at ingestion. A client request never widens
`audience` from `client`.

### 7. Managing the store

```powershell
# idempotent re-index: safe to run live
$body = @{source="docs/corpus"; corpusLabel="live"; audience="client"} | ConvertTo-Json
Invoke-RestMethod "$base/api/admin/rag/ingest" -Method Post -Headers $HA -ContentType "application/json" -Body $body -TimeoutSec 600
```

```
detail : Corpus live-887c1d77a2e9a8f3 indexed.
data   : {documents=0; chunks=159; unchanged=True; version=live-887c1d77a2e9a8f3}
```

`unchanged: True` is worth explaining: the version label is
`{label}-{sha256 of the file set}`, so re-ingesting an unchanged corpus matches
an existing label and short-circuits instead of re-embedding 159 chunks and
creating a duplicate version. Change one file and the digest changes, producing
a new version — content-addressed, so it cannot drift.

`POST /api/admin/rag/reset` clears every corpus version. **Do not run it during
the demo** unless you are deliberately showing D.1(1) (answering without
grounding), and re-ingest afterwards.

**Be ready for this one.** *Your log says `keyword_candidates 0` -- is the
keyword arm doing anything?* On that question, no, and the honest reason is
worth knowing: `websearch_to_tsquery` **ANDs** every term, so
`What does the policy say about handling client complaints?` becomes
`polici & say & handl & client & complaint` and no single chunk contains all
five. Short, termy queries are where it fires -- measured against this corpus:

```
keyword hits   query
          4    CKA
          2    satellite exposure
          6    What is rebalancing?
          1    risk tolerance assessment
          0    What does the policy say about handling client complaints?
          0    customer grievance escalation process
```

So the fusion is real but asymmetric: the keyword arm carries the rare literal
tokens a client asks about by name, and the dense arm carries natural-language
questions. That division is the reason for having both, and it is also the
honest limit of the current implementation -- an OR fallback when the AND query
returns nothing would widen the keyword arm, and has not been done because it
changes graded retrieval behaviour and needs the red-team set re-run.

**Why this demonstrates B.4.** A retrieval system that returns rows for any
input has not been shown to work — ranking always returns *something*. Three
things prove it here: paraphrases with no shared vocabulary reach the same
passage, so the matching is semantic rather than lexical; the two populations
**separate** (0.40-0.70 against 0.00-0.27) with a floor calibrated into the gap,
so the measurement becomes a decision; and the store is **managed** through
authenticated, logged endpoints — inspectable, re-indexable and resettable
during the demonstration itself, rather than through a terminal script nobody
can audit.

## B.5 Context assembly and window management

**Claim:** five delimited blocks with explicit trust labels, assembled under a
token budget with a fixed degradation order.

```powershell
$p = Invoke-RestMethod "$base/api/admin/debug/last-payload" -Headers $HA -TimeoutSec 30
"totalTokens $($p.totalTokens) / $($p.contextLimit)"
$p.blockTokens
```

```
totalTokens 2567 / 8192
_method tokenizer | _dropped_chunks 0
```

`_method: tokenizer` means these are **exact** counts from the served model's own
tokenizer, not a character heuristic — the heuristic over-counts by about 1.9×,
silently costing retrieved chunks on every request. `_dropped_chunks: 0` is the
budget reporting it did not have to degrade; under pressure it drops retrieved
chunks first and never truncates the system prompt or the user's message.

If asked how exact counting works for a course-hosted model: the endpoint's
model names are not Hugging Face repo ids, so they are mapped to the matching
ungated tokenizer repo. Qwen's are Apache-2.0 and download freely; Gemma's are
gated, so `gemma-3-12b` falls back to the conservative heuristic rather than
guessing.

The sign-off asks for **execution logs**, not only an endpoint, so the same
assembly is logged on every request. Terminal 1, with `LOG_FORMAT=pretty`:

```
02:22:51 INFO    app.orchestration.context  context assembled  [a1e8d712]
    model                    qwen3.6-35b-a3b-fp8
    intent                   policy_question
    total_tokens             4047
    context_limit            8192
    available_for_prompt     7208
    reserved_for_completion  800
    counting_method          tokenizer
    retrieved_kept           6
    retrieved_dropped        0
    history_turns_kept       4
    history_turns_dropped    8
    fact_fields_dropped      0
    blocks  (5)
        name              trust            tokens
        SYSTEM            instruction         521
        CLIENT_FACTS      data                426
        RETRIEVED_POLICY  data               1932
        HISTORY           untrusted-input    1146
        USER_MESSAGE      untrusted-input      22
```

This is the whole claim in one record. The `trust` column is the point to dwell
on: `instruction` for the system prompt, `data` for facts and retrieved policy,
`untrusted-input` for history and the user's own message. Trust is **declared
structurally**, not inferred from content, which is what makes instruction-like
text arriving inside a retrieved passage or a profile field land in a block the
model has been told is data (D.5).

`history_turns_dropped 8` is the degradation policy doing its job in public: a
twelve-turn conversation did not fit, so the eight oldest turns were dropped --
oldest first, never the system prompt, and never summarised, because compacting
history with an LLM call would spend the scarce shared endpoint to save tokens.

**Why this demonstrates B.5.** The prompt is not a concatenated string: it is
five named blocks whose individual costs you can read off, summed against a
declared ceiling. That is what makes the window *managed* rather than hoped for.
`_method: tokenizer` shows the budget is measured with the model's own tokenizer
instead of estimated, and `_dropped_chunks` is the degradation policy reporting
itself — zero here because there was headroom, and the first thing it would
drop under pressure is retrieved context, never the system prompt or the user's
question.

## B.6 Exception handling and resilience

**Claim:** separate timeouts, bounded backoff with jitter, a circuit breaker,
and a distinct client-facing error per failure mode.

```powershell
foreach ($f in @("rate_limited","timeout","server_error","unreachable")) {
  try {
    Invoke-WebRequest "$base/api/chat" -Method Post -UseBasicParsing `
      -Headers (@{Authorization="Bearer $CLIENT"; "X-Fault-Inject"=$f}) `
      -ContentType "application/json" `
      -Body (@{message="What is a balanced portfolio?"} | ConvertTo-Json) -TimeoutSec 120 | Out-Null
    "{0,-14} -> 200" -f $f
  } catch {
    $resp = $_.Exception.Response
    $ra = $resp.Headers['Retry-After']
    "{0,-14} -> HTTP {1}{2}" -f $f, [int]$resp.StatusCode, $(if ($ra) {"  Retry-After: $ra"} else {""})
  }
}
```

```
rate_limited   -> HTTP 429  Retry-After: 2
timeout        -> HTTP 504
server_error   -> HTTP 503
unreachable    -> HTTP 503
```

**Run this table once, on a freshly started backend.** The breaker opens after
five failures and then returns `llm_circuit_open` (503) for *everything*, which
masks the per-fault codes — with it latched, `timeout` reports 503 instead of
504, which looks like a bug and is not. After the 30s cooldown it returns
*half-open*: one trial is allowed and the next failure re-opens it immediately,
so you get one clean fault per cooldown. **Restart Terminal 1 to show the table
again.**

Then trip the breaker, watching the time column:

```powershell
1..7 | ForEach-Object {
  $sw = [Diagnostics.Stopwatch]::StartNew(); $code = 0
  try {
    $resp = Invoke-WebRequest "$base/api/chat" -Method Post -UseBasicParsing `
      -Headers (@{Authorization="Bearer $CLIENT"; "X-Fault-Inject"="server_error"}) `
      -ContentType "application/json" -Body (@{message="t"} | ConvertTo-Json) -TimeoutSec 120
    $code = [int]$resp.StatusCode
  } catch { $code = [int]$_.Exception.Response.StatusCode }
  $sw.Stop(); "attempt {0}: HTTP {1} in {2:N3}s" -f $_, $code, $sw.Elapsed.TotalSeconds
}
```

```
attempt 1: HTTP 503 in 0.512s     <- retrying with backoff
attempt 5: HTTP 503 in 0.574s
attempt 6: HTTP 503 in 0.104s     <- breaker open, failing fast
attempt 7: HTTP 503 in 0.071s
```

Latency drops roughly **8×** once the breaker opens, because it stops retrying a
dead endpoint. Terminal 1 shows `llm call failed, retrying` then
`llm circuit opened`. (If every attempt is already ~0.06s, the breaker was
latched from the fault table above — restart Terminal 1 to see the transition.)

**Finish on the strongest part.** With the circuit still open:

```powershell
foreach ($ep in @("holdings","eligible-portfolios","performance","audit")) {
  try {
    Invoke-RestMethod "$base/api/clients/me/$ep`?session_date=2026-03-02" -Headers $H -TimeoutSec 40 | Out-Null
    "{0,-22} -> 200" -f $ep
  } catch { "{0,-22} -> HTTP {1}" -f $ep, [int]$_.Exception.Response.StatusCode }
}
```

All four return **200**. The LLM is a component, not a dependency: suitability,
pricing, holdings and the audit trail do not need it — exactly what the error
message promises the client.

**Why this demonstrates B.6.** Three separate things are visible here, and each
proves a different half of the claim. **Distinct status codes** show failures are
*classified* — rate limiting, timeout and unavailability are different
conditions with different client guidance, not one generic 500. The **latency
collapsing 8×** proves the breaker is real state and not a message: the system
changed its own behaviour after five failures and stopped retrying something it
had learned was dead. And the **four endpoints still returning 200** prove the
failure was contained to one component rather than taking the product down —
which is the actual point of the resilience work.

## B.7 Session state and logging

**Claim:** session state is checkpointed in the database, not process memory,
and the operational log is separate from the audit log.

```powershell
Invoke-RestMethod "$base/api/session/date" -Method Post -Headers $H -ContentType "application/json" `
  -Body (@{sessionDate="2026-03-02"} | ConvertTo-Json) -TimeoutSec 30 | Out-Null
Invoke-RestMethod "$base/api/session/date" -Headers $H -TimeoutSec 30
```

```
sessionDate 2026-03-02   bounds 2026-01-05 .. 2026-06-19
```

It survives a restart, because it lives in `conversation.state` rather than in
the worker. **Restarting Terminal 1 and re-reading it is a strong thing to
show.**

Then the two streams. Terminal 1 now logs one line per request:

```
INFO:  127.0.0.1:63717 - "GET /api/clients/me/eligible-portfolios HTTP/1.1" 200 OK
INFO:  127.0.0.1:63720 - "GET /api/clients/me/nope HTTP/1.1" 404 Not Found
```

And the audit trail, with full provenance:

```powershell
Invoke-RestMethod "$base/api/admin/audit?limit=3" -Headers $HA -TimeoutSec 30 |
  Select-Object decisionId, decisionType, status, modelVersion, policyVersion
```

```
D-1172 | risk_assessment | pending_review | model qwen3.6-35b-a3b-fp8
D-1171 | risk_assessment | pending_review | model qwen3.6-35b-a3b-fp8
```

The distinction is the point. Stream 1 is operational and disposable. Stream 2
is an append-only business record, written inside the decision's own
transaction, storing **copies** of the retrieved passages rather than references
— so the record of why a decision was made cannot drift when the corpus is
re-ingested.

**Why this demonstrates B.7.** Surviving a restart *is* the requirement: if the
session date came back after the worker was killed and replaced, it was never in
process memory, which is the only way a conversation can outlive a deployment or
move between workers. For logging, the demonstration is that the two streams are
**separate but correlatable** — an operational line on stdout and an append-only
decision row in the database, found by the same correlation id. Separate,
because one is disposable and the other is a business record; correlatable,
because an audit you cannot tie back to what the system was doing is not much of
an audit.

---

## If you are asked

**"Does this run against the HPC endpoint?"** Yes — `LLM_BACKEND=hpc`, model
`qwen3.6-35b-a3b-fp8`. The provider is switchable (`hpc` / `openai` / `stub`);
all three speak the same protocol through one client and share resilience
settings, so graded behaviour does not change with the backend.

**"Why is thinking disabled on the model?"** It is a reasoning model: it spends
completion tokens on a hidden chain of thought before answering. With a modest
`max_tokens` the budget was gone before any answer was written — HTTP 200,
`finish_reason: length`, empty content. Disabling thinking returns a full answer
in 800 tokens using none on reasoning.

**"How do you know the resilience code works, not just the error messages?"**
The injected fault is driven through the same retry-and-breaker path a real
provider uses, rather than raised at the caller — which is why the breaker
actually opens during the demo instead of only in a unit test.

**"Is any of this tested?"** `make test` — **393 passing**, including 27
integration tests against a real PostgreSQL that could not run at all before the
migration. Plus `make lint` for ruff and the import-linter contract keeping risk
weights unreachable from the orchestration and LLM layers.
