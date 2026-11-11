# Rebuilding your database (no `make` needed)

If your `instrument` table contains `CASHF`, `SGSB`, `GLBD`, `CORPB`, `WEQ`,
`SGEQ`, `EMEQ`, `TECH`, `REIT` or `GOLD`, your database is the old placeholder
universe. Those ten tickers were invented on 2026-09-08 to stand in until the
instructor's files arrived; Appendix C section C.4 defines seven real ETFs
instead, and none of the ten appears anywhere in the corpus. The visible symptom
was the assistant answering *"I do not have access to that information"* when
asked what P1 holds.

This rebuilds your database to match. Every command is plain Python — `make` is
only a shortcut and is not required.

Check what you have:

```bash
python -c "from app.db.session import connect_sync; c=connect_sync(); cur=c.cursor(); cur.execute('select symbol from instrument order by symbol'); print([r[0] for r in cur.fetchall()])"
```

You want exactly `['AGG', 'BIL', 'SMH', 'SPY', 'XLE', 'XLK', 'XLV']`.

---

## 0. Before you start

**Pull first.** The fix is reference data in `scripts/_fixtures.py` plus two new
scripts; without the latest `main` the commands below will re-seed the same ten
bogus tickers.

```bash
git pull
pip install -e ".[dev,rag]"
```

The `rag` extra matters: step 6 below needs `sentence-transformers` and
`pymupdf` to embed the corpus and read the PDFs. Plain
`pip install -r requirements.txt` installs only `.[dev]` and the ingest will
fail on the import. The extra pulls in torch, so it is ~2.5 GB — if you already
have it, you can skip this.

**Check the corpus arrived.** The regulatory PDFs are committed, so `git pull`
brings them with the code:

```bash
ls docs/corpus/
```

You should see 8 files — 1 `.md`, 1 `.docx` and 6 `.pdf`. A missing PDF does not
error at ingest; it quietly indexes a fraction of the chunks, and retrieval then
behaves differently from everyone else's. If any are absent, your `git pull`
didn't complete.

**You need a `.env`** with a working `DATABASE_URL`. No LLM API key is needed
for any step here; all of them are deterministic.

Confirm the database is reachable:

```bash
python -m scripts.check_env --db
```

If you use the bundled Postgres rather than your own:

```bash
docker compose up -d --wait db
```

---

## 1. Rebuild

> **This drops and recreates the public schema.** Everything in your local
> database goes: clients, holdings, trades, the decision log. That is the point
> — holdings denominated in the retired tickers cannot be migrated, because
> `trade`, `holdings_snapshot`, `portfolio_event`, `risk_assessment` and
> `decision` are append-only by design (a `BEFORE DELETE` trigger refuses).
> If you have a local audit trail you need, dump it first.

Run these six in order, from the repo root:

```bash
python -m scripts.migrate --reset
python -m scripts.seed
python -m scripts.gen_prices
python -m scripts.load_prices
python -m scripts.gen_portfolio_reference
python -m app.rag.ingest --source docs/corpus --corpus-label bootstrap --audience client --reset
```

What each one does, and why the order matters:

| # | command | effect |
|---|---|---|
| 1 | `scripts.migrate --reset` | drops and rebuilds the schema, triggers included |
| 2 | `scripts.seed` | the 7 Appendix C ETFs, the 8 Appendix E portfolios and their weights, the admin and the 5 client profiles |
| 3 | `scripts.gen_prices` | 120 business days of synthetic prices for the 7 tickers, from a **fixed seed**, so everyone gets identical prices |
| 4 | `scripts.load_prices` | loads that CSV into `instrument_price` |
| 5 | `scripts.gen_portfolio_reference` | writes `docs/corpus/platform-portfolio-reference.md` **from the database** — must run after 2 and before 6 |
| 6 | `app.rag.ingest ... --reset` | clears and rebuilds the vector index |

Step 5 is why "Does P1 contain any equities?" works at all. Appendix E numbers
the portfolios 1–8 and never writes "P1", so no chunk in the corpus connected
the code the client types to the weights. The page is generated, never
hand-edited — it is also gitignored, so you must run this yourself.

Also ingest the admin-only corpus if you work on the admin console:

```bash
python -m app.rag.ingest --source docs/corpus_admin --corpus-label admin --audience admin
```

---

## 2. Create the holdings

A freshly reset database has five client profiles and no investments, because
holdings are created by *using* the product. One script does it the same way
every time, over the real HTTP API so every write is audited exactly as a
browser's would be.

**Start the server in one terminal:**

```bash
python -m scripts.serve --port 8000
```

**Then, in another:**

```bash
python -m scripts.demo_activity
```

Expected output:

```
  C001 moderately_conservative -> P1 (3 trades: SPY, AGG, BIL)
  C002 very_aggressive -> P8 (1 trades: SPY)
  C003 growth -> P3 (3 trades: SPY, AGG, BIL)
  C004 not scored, not invested -- profile is incoherent (short_horizon_aggressive_objective, ...)
  C005 balanced -> P2 (3 trades: SPY, AGG, BIL)
```

**C004 not investing is correct, not a failure.** Siti Nurhaliza's profile
(Appendix D User 4) contradicts itself — a 0-year horizon with an aggressive
growth objective — so the scorer declines to assign a category and asks
clarifying questions instead. Leave her as she is; she is the demo case for
that behaviour.

The script asks the suitability engine which portfolio each client may hold
rather than hard-coding one, so the result cannot drift from
`policy/suitability.yaml`. It is safe to re-run: a client who already has an
invest event is skipped.

Prices come from a fixed seed and the session date is pinned to 2026-06-19
(the last priced day), so your unit counts will match everyone else's exactly.

Useful flags:

```bash
python -m scripts.demo_activity --dry-run                      # report, write nothing
python -m scripts.demo_activity --base-url http://127.0.0.1:8001
python -m scripts.demo_activity --session-date 2026-06-19
```

---

## 3. Verify

```bash
python -m scripts.gen_portfolio_reference --check   # must say "up to date"
python -m pytest -q                                 # must be green
```

And in the app: log in as `c002@roboadvisor.local` (password `password123`,
local seed only) and ask **"Does P1 contain any equities?"** The answer should
name P1 as Capital Preservation and give 10% SPY. If it says it has no access to
that information, step 5 or step 6 did not run.

Client logins are `c001@` … `c005@roboadvisor.local`; the compliance console is
`admin@roboadvisor.local`. Same password for all.

---

## Why not the migration script?

`scripts/fix_instrument_universe.py` repairs the instrument universe **in
place**, preserving the audit trail:

```bash
python -m scripts.fix_instrument_universe            # report only
python -m scripts.fix_instrument_universe --apply    # make the changes
python -m scripts.gen_prices && python -m scripts.load_prices
```

Use it when you have a decision log worth keeping. It backs up to `backups/`
first, verifies the result against Appendices C and E, and rolls back if
anything fails to match.

It cannot give you a clean demo database, though. The append-only triggers mean
the retired `instrument` rows have to stay — they are the denomination of trades
and holdings that by design cannot be deleted. After it runs, no *model
portfolio* references a retired ticker, but clients may still *hold* them. For
a demo, the full reset in step 1 is the right tool.

---

## If something goes wrong

**`No price on <date> for N instrument(s)`** — prices are missing for something a
client holds. Re-run `gen_prices` then `load_prices`. If you reached this by
running `fix_instrument_universe` on a database with existing holdings, do the
full reset instead.

**`is out of date -- run python -m scripts.gen_portfolio_reference`** — you
re-seeded without regenerating the page. Run step 5, then step 6.

**`Select a session date before viewing prices or holdings.`** — expected before
a date is chosen. `scripts.demo_activity` sets it; in the browser, pick one on
the dashboard.

**`cannot reach http://127.0.0.1:8000`** — the server is not up. Start
`python -m scripts.serve --port 8000` first, in its own terminal.

**`table <name> is append-only`** — you tried to delete audited history. That is
the control working. Use `scripts.migrate --reset`, which drops the tables
rather than fighting the triggers.

---

On Windows, if `python` resolves to the wrong interpreter, use the venv directly:
`.venv\Scripts\python.exe -m scripts.seed`. On macOS and Linux:
`.venv/bin/python -m scripts.seed`. With `make` available, steps 1 and 2 are
`make reset`, then `make serve` and `make demo`.
