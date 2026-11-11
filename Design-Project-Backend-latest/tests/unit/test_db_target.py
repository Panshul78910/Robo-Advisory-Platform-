"""The local/Supabase switch, and the guards around it.

Pointing the wrong process at the shared project is the expensive mistake here,
so every branch of the resolution is pinned.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.db.session import TRANSACTION_POOLER_PORT, _async_url, _connect_args

LOCAL = "postgresql://postgres:postgres@127.0.0.1:54329/postgres"
POOLER = "postgresql://postgres.ref:pw@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres"
DIRECT = "postgresql://postgres:pw@db.ref.supabase.co:5432/postgres"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Resolution must be tested from explicit values alone.

    The suite pins `DB_TARGET=local` in tests/conftest.py so nothing can reach
    the shared project by accident; that pin would otherwise leak in here and
    make every case look like a conflict.
    """
    for name in ("DB_TARGET", "DATABASE_URL", "DATABASE_URL_LOCAL", "DATABASE_URL_SUPABASE"):
        monkeypatch.delenv(name, raising=False)


def settings(**env) -> Settings:
    """Build Settings from explicit values only, ignoring any real .env."""
    return Settings(_env_file=None, **env)


class TestResolution:
    def test_local_is_the_default(self):
        assert settings().database_url == LOCAL

    def test_supabase_target_selects_the_supabase_url(self):
        s = settings(db_target="supabase", database_url_supabase=POOLER)
        assert s.database_url == POOLER
        assert s.is_remote_db

    def test_local_target_ignores_a_configured_supabase_url(self):
        """Both configured at once is the normal state; only the switch decides."""
        s = settings(db_target="local", database_url_supabase=POOLER)
        assert s.database_url == LOCAL
        assert not s.is_remote_db

    def test_explicit_database_url_wins_when_no_target_is_given(self):
        assert settings(database_url=DIRECT).database_url == DIRECT


class TestGuards:
    def test_supabase_target_without_a_url_is_refused(self):
        with pytest.raises(ValueError, match="DATABASE_URL_SUPABASE is empty"):
            settings(db_target="supabase")

    def test_the_error_says_it_is_not_an_api_key(self):
        """The likeliest mistake is pasting sb_secret_... here."""
        with pytest.raises(ValueError, match="not an API key"):
            settings(db_target="supabase")

    def test_a_shadowing_database_url_is_refused_not_silently_obeyed(self):
        with pytest.raises(ValueError, match="disagree"):
            settings(db_target="supabase", database_url_supabase=POOLER, database_url=LOCAL)

    def test_agreeing_values_are_allowed(self):
        """Belt and braces is fine; only a genuine disagreement is an error."""
        s = settings(db_target="local", database_url=LOCAL)
        assert s.database_url == LOCAL


class TestRemoteDetection:
    @pytest.mark.parametrize("url,remote", [(LOCAL, False), (POOLER, True), (DIRECT, True)])
    def test_remote_is_identified(self, url, remote):
        assert settings(database_url=url).is_remote_db is remote


class TestPoolerCompatibility:
    def test_transaction_pooler_disables_prepared_statements(self):
        """Supavisor reuses backends, so a cached prepared statement collides."""
        assert _connect_args(POOLER) == {"prepare_threshold": None}

    def test_session_and_direct_connections_keep_them(self):
        session_pooler = POOLER.replace(f":{TRANSACTION_POOLER_PORT}", ":5432")
        assert _connect_args(session_pooler) == {}
        assert _connect_args(DIRECT) == {}
        assert _connect_args(LOCAL) == {}

    def test_driver_is_rewritten_for_async_psycopg(self):
        assert _async_url(POOLER).startswith("postgresql+psycopg://")

    def test_an_already_qualified_driver_is_left_alone(self):
        assert _async_url("postgresql+psycopg://a@h/db") == "postgresql+psycopg://a@h/db"


class TestMigrationSafety:
    @pytest.mark.parametrize("url", [POOLER, DIRECT])
    def test_reset_refuses_a_remote_target(self, url):
        from scripts.migrate import _guard_destructive

        with pytest.raises(SystemExit, match="refusing to --reset"):
            _guard_destructive(url, force=False)

    def test_reset_allows_local(self):
        from scripts.migrate import _guard_destructive

        _guard_destructive(LOCAL, force=False)  # must not raise

    def test_an_explicit_force_flag_permits_it(self):
        from scripts.migrate import _guard_destructive

        _guard_destructive(POOLER, force=True)  # must not raise
