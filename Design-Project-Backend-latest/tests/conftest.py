from __future__ import annotations

import os

import pytest

from app.core.eventloop import use_compatible_event_loop

# Windows' default loop is incompatible with psycopg's async mode.
use_compatible_event_loop()

# Tests must never touch the shared HPC endpoint or download an embedding model.
os.environ.setdefault("LLM_BACKEND", "stub")
os.environ.setdefault("EMBEDDING_BACKEND", "hash")
os.environ.setdefault("HPC_MODEL_NAME", "")

# The Part D judge must be off by default, and *overridden* rather than defaulted:
# `JUDGE_ENABLED=true` in .env is the normal state once Part D is set up, and it
# would otherwise leak in here, sending every screened message to a local Ollama
# the suite does not require. Worse, the judge fails closed, so an absent Ollama
# turns into "everything is blocked" and a spray of unrelated failures. Tests that
# exercise the judge opt in explicitly by setting it themselves.
os.environ["JUDGE_ENABLED"] = "false"

# Same reasoning, and the same trap: refusal rewriting calls a hosted endpoint on
# every blocked request, so leaving .env's value in place sends the suite out to
# the network -- slow, flaky, and spending a rate-limited quota to produce text
# no assertion wants. It also makes refusal text non-deterministic, which is
# exactly what the tests that read it are checking. Opt in explicitly.
os.environ["REFUSAL_REWRITE_ENABLED"] = "false"
# The vagueness check is a third hosted call on the same reasoning: it fires on
# any message the deterministic layer cannot place, which describes a good number
# of fixtures, and a "please rephrase" in place of the expected answer would fail
# them for a reason that has nothing to do with what they test.
os.environ["VAGUENESS_CHECK_ENABLED"] = "false"
# Hosted judges likewise: the local models are what the suite is pinned against.
os.environ["HOSTED_JUDGE_ENABLED"] = "false"

# Integration tests write real rows and roll back, but a rollback is no defence
# against running them somewhere that matters. Pin the target to local and
# *override* rather than default: a stray `DB_TARGET=supabase` in the shell or
# .env would otherwise point the suite at the shared project.
if os.environ.get("ALLOW_TESTS_AGAINST_REMOTE_DB") != "1":
    os.environ["DB_TARGET"] = "local"


def _test_database_url() -> str:
    """The configured database with `_test` appended to its name.

    Pinning the target to `local` stopped the suite reaching Supabase, but once a
    real local Postgres holds real data, "local" is exactly the database that
    must not be touched. The integration tests roll back their writes, so they
    do not corrupt it -- but they still *read* it, and a client who already holds
    a portfolio fails a test written for a freshly seeded one. Worse, the failure
    looks like a product bug.

    A separate database removes both problems. If it does not exist the
    integration tests skip, exactly as they did when there was no local Postgres
    at all.
    """
    from urllib.parse import urlsplit, urlunsplit

    from app.core.config import get_settings

    base = get_settings().database_url
    get_settings.cache_clear()
    parts = urlsplit(base)
    name = parts.path.lstrip("/") or "postgres"
    if name.endswith("_test"):
        return base
    return urlunsplit(parts._replace(path=f"/{name}_test"))


# Set both: DATABASE_URL is the explicit override and DATABASE_URL_LOCAL is what
# DB_TARGET=local resolves to. They must agree or the config refuses to start.
_TEST_DB_URL = _test_database_url()
os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["DATABASE_URL_LOCAL"] = _TEST_DB_URL


def _database_reachable() -> bool:
    try:
        import psycopg

        from app.core.config import get_settings

        with (
            psycopg.connect(get_settings().database_url, connect_timeout=2) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("select 1 from app_user limit 1")
        return True
    except Exception:
        return False


DB_AVAILABLE = _database_reachable()

requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="no migrated Postgres at DATABASE_URL; run `make reset` first",
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in str(item.fspath) and not DB_AVAILABLE:
            item.add_marker(
                pytest.mark.skip(reason="no migrated Postgres at DATABASE_URL (`make reset`)")
            )
