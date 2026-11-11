# Override to run a second instance alongside one already on 8000:
#     make serve PORT=8001
PORT ?= 8000

PY := .venv/Scripts/python.exe
LINT_IMPORTS := .venv/Scripts/lint-imports.exe
ifeq ($(wildcard $(PY)),)
PY := python
LINT_IMPORTS := lint-imports
endif

.PHONY: up serve db reset demo test lint fmt check probe ingest

## Start the database and serve the API with reload.
up: db
	$(PY) -m scripts.serve --reload --port $(PORT)

## Serve without consulting Docker at all. Same server as `up`; skips the `db`
## dependency, so it works on a machine with no Docker daemon installed. The
## database must already be reachable -- a Supabase DATABASE_URL, or a Postgres
## you started yourself.
serve:
	$(PY) -m scripts.serve --port $(PORT)

## Start the bundled Postgres container, but only when nothing is answering at
## the configured DATABASE_URL already. Reachability is the right question, not
## "is the host local": a native Postgres on 127.0.0.1 is local *and* already
## running, and the old is_remote_db check would have started a second, unused
## container next to it. A Supabase target is simply reachable, so it skips too,
## and `make up` no longer needs a Docker daemon to exist at all.
db:
	@$(PY) -c "from app.db.session import connect_sync; connect_sync().close()" >/dev/null 2>&1 && echo "  database already reachable -- not starting a container" || docker compose up -d --wait db

## Drop, re-migrate, re-seed and re-index everything.
reset: db
	$(PY) -m scripts.migrate --reset
	$(PY) -m scripts.seed
	$(PY) -m scripts.gen_prices
	$(PY) -m scripts.load_prices
# Between seed and ingest on purpose: it reads the portfolios seed has just
# written, and writes a corpus page ingest is about to index. Appendix E
# numbers the portfolios 1-8 and never says "P1", so without this the corpus
# holds no mapping from the codes the client actually uses.
	$(PY) -m scripts.gen_portfolio_reference
	$(PY) -m app.rag.ingest --source docs/corpus --corpus-label bootstrap --audience client --reset

## Drive the seeded clients through assessment and investing, so a reset
## database shows holdings instead of five clients who never invested.
##
## NOT part of `reset`: it goes through the HTTP API so every write is audited
## like a browser's, which means the server has to be up -- and `reset` is what
## you run before starting it. Run `make reset`, start `make serve`, then this.
demo:
	$(PY) -m scripts.demo_activity

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check app scripts tests
	$(PY) -m ruff format --check app scripts tests
	$(PY) scripts/check_clock.py
	$(LINT_IMPORTS)

fmt:
	$(PY) -m ruff format app scripts tests
	$(PY) -m ruff check --fix app scripts tests

## Report configuration and schema without printing any secret. Safe to paste.
check:
	$(PY) -m scripts.check_env --db

probe:
	$(PY) -m scripts.probe_hpc

ingest:
	$(PY) -m app.rag.ingest --source docs/corpus --corpus-label bootstrap --audience client
