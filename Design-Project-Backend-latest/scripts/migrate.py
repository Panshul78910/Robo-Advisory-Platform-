"""Apply supabase/migrations/*.sql in order.

Teams with the Supabase CLI can use `supabase db reset` instead -- the files are
plain SQL and the two paths are interchangeable. This runner exists so the
project is usable with nothing but Docker and Postgres.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from urllib.parse import urlparse

from app.core.config import get_settings
from app.db.session import connect_sync

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "supabase" / "migrations"
SEED_EMAIL_DOMAIN = "@roboadvisor.local"


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "db", "postgres"}


def is_local(database_url: str) -> bool:
    return (urlparse(database_url).hostname or "") in LOCAL_HOSTS


def _guard_destructive(database_url: str, *, force: bool) -> None:
    """`--reset` drops the whole public schema. Never let that happen to a
    remote database by accident.

    `make reset` is run casually and often. Pointed at a hosted project it would
    destroy every table in it, so a remote target has to be confirmed explicitly
    rather than merely configured.
    """
    if is_local(database_url) or force:
        return
    host = urlparse(database_url).hostname
    raise SystemExit(
        f"refusing to --reset a non-local database ({host}).\n"
        "This drops the entire public schema, including anything this project "
        "did not create.\n"
        "If that is genuinely what you want, re-run with --force-remote-reset."
    )


def _reset(cur) -> None:
    # Accounts live in public.app_user / app_credential (see 008), so dropping
    # the schema removes them too -- nothing in `auth` belongs to this project.
    cur.execute("drop schema if exists public cascade")
    cur.execute("create schema public")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop and rebuild the public schema")
    ap.add_argument(
        "--force-remote-reset",
        action="store_true",
        help="permit --reset against a non-local host (destroys its public schema)",
    )
    args = ap.parse_args()

    database_url = get_settings().database_url
    if args.reset:
        _guard_destructive(database_url, force=args.force_remote_reset)

    files = sorted(MIGRATIONS.glob("*.sql"))
    if not files:
        print(f"no migrations found in {MIGRATIONS}", file=sys.stderr)
        return 1

    with connect_sync(autocommit=False) as conn:
        with conn.cursor() as cur:
            # 000 creates the auth shim; it must run before any reset that
            # references auth.users.
            cur.execute((MIGRATIONS / "000_auth_shim.sql").read_text(encoding="utf-8"))
            if args.reset:
                _reset(cur)
            cur.execute(
                "create table if not exists schema_migrations ("
                " filename text primary key,"
                " applied_at timestamptz not null default now())"
            )
            cur.execute("select filename from schema_migrations")
            applied = {r[0] for r in cur.fetchall()}

            for path in files:
                if path.name in applied:
                    continue
                cur.execute(path.read_text(encoding="utf-8"))
                cur.execute("insert into schema_migrations (filename) values (%s)", (path.name,))
                print(f"applied {path.name}")
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
