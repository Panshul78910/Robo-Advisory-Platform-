"""Diagnose configuration without exposing any secret.

Run this yourself and paste the output anywhere -- it prints only derived facts
(which key is set, scheme, host, port, string length) and never a value. Nothing
here reveals a password, an API key or a token.

    python -m scripts.check_env           # configuration only
    python -m scripts.check_env --db      # also verify the database schema (read-only)
"""

from __future__ import annotations

import argparse
from urllib.parse import urlparse

EXPECTED_MIGRATIONS = 10
CORE_TABLES = (
    "app_user",
    "app_credential",
    "client",
    "client_profile",
    "risk_assessment",
    "instrument",
    "model_portfolio",
    "instrument_price",
    "portfolio_event",
    "trade",
    "holdings_snapshot",
    "decision",
    "decision_source",
    "escalation",
    "conversation",
    "chunk",
    "llm_call",
)
APPEND_ONLY = (
    "decision",
    "decision_source",
    "portfolio_event",
    "trade",
    "holdings_snapshot",
    "holdings_snapshot_line",
    "risk_assessment",
)


#: Accepted spellings, in the order the settings resolve them. The course
#: publishes the LLM_* names; the HPC_* ones predate them and still work.
ENDPOINT_NAMES = ("LLM_ENDPOINT", "HPC_BASE_URL")
KEY_NAMES = ("LLM_API_KEY", "HPC_API_KEY")
MODEL_NAMES = ("LLM_MODELS", "HPC_MODEL_NAME")


def _configured_keys() -> set[str]:
    """Names present in the environment or .env. Names only -- never values."""
    import os

    present = {k for k, v in os.environ.items() if v}
    try:
        from dotenv import dotenv_values

        present |= {k for k, v in dotenv_values(".env").items() if v}
    except Exception:
        pass
    return present


def _source(names: tuple[str, ...]) -> str:
    """Report which spelling supplied the value, and flag a shadowed duplicate."""
    present = _configured_keys()
    found = [n for n in names if n in present]
    if not found:
        return "(none set)"
    if len(found) == 1:
        return f"<- {found[0]}"
    return f"<- {found[0]}  (also set, ignored: {', '.join(found[1:])})"


def _describe(url: str) -> str:
    if not url:
        return "(not set)"
    parsed = urlparse(url)
    if not parsed.scheme:
        return f"NOT A URL -- {len(url)} chars, no scheme"
    host = parsed.hostname or "(no host)"
    port = parsed.port or "(no port)"
    creds = []
    if parsed.username:
        creds.append("user")
    if parsed.password:
        creds.append("password")
    return f"{parsed.scheme}://{host}:{port} [{', '.join(creds) or 'no credentials'}]"


def check_config() -> int:
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception as exc:
        print("CONFIG FAILED TO LOAD\n")
        message = str(exc)
        for line in message.splitlines():
            if "input_value" not in line:  # pydantic echoes the values back
                print(f"  {line}")
        return 1

    print("Database")
    print(f"  DB_TARGET             {settings.db_target}")
    print(f"  resolved              {_describe(settings.database_url)}")
    print(f"  remote                {settings.is_remote_db}")
    port = urlparse(settings.database_url).port
    if port == 6543:
        print("  pooler                transaction mode, prepared statements disabled")
    elif settings.is_remote_db:
        print("  pooler                not the transaction pooler")

    print("\nLLM")
    print(f"  LLM_BACKEND           {settings.llm_backend}")
    print(f"  endpoint              {_describe(settings.hpc_base_url)}  {_source(ENDPOINT_NAMES)}")
    print(
        f"  model                 {settings.hpc_model_name or '(not set)'}  {_source(MODEL_NAMES)}"
    )
    api_key = f"set, {len(settings.hpc_api_key)} chars" if settings.hpc_api_key else "(not set)"
    print(f"  api key               {api_key}  {_source(KEY_NAMES)}")
    print(f"  context limit         {settings.hpc_context_limit}")

    # Only the active backend's settings matter; showing the rest invites the
    # reader to check a value the process is not using.
    if settings.llm_backend == "openai":
        key = settings.openai_api_key
        print("\n  active backend is OpenAI, which uses these instead:")
        print(f"    model               {settings.openai_model}")
        print(f"    base url            {settings.openai_base_url}")
        print(
            "    api key             "
            + (f"set, {len(key)} chars" if key else "NOT SET -- startup will refuse")
        )
    elif settings.llm_backend == "stub":
        print("\n  active backend is the stub: offline, no endpoint or key used.")

    print("\nOther")
    print(f"  EMBEDDING_BACKEND     {settings.embedding_backend}")
    print(f"  JWT_SECRET            {len(settings.jwt_secret)} chars")
    if settings.jwt_secret.startswith("dev-only-insecure"):
        print("                        ^ still the placeholder from .env.example")
    return 0


#: Substring -> what it actually means, and what to do about it. Ordered, so a
#: more specific match is reported before a general one.
CONNECT_HINTS: tuple[tuple[str, str], ...] = (
    (
        "tenant or user not found",
        "the pooler needs the username `postgres.<project-ref>`, not plain `postgres`.\n"
        "    Copy the URI again from Project Settings -> Database -> Connection string,\n"
        "    and make sure the 'Transaction pooler' tab is selected.",
    ),
    # Before the generic password hint: otherwise an unsubstituted placeholder
    # sends you off to reset a password that was never the problem.
    (
        "your-password",
        "the URI still contains the literal [YOUR-PASSWORD] placeholder -- replace it\n"
        "    with the real database password.",
    ),
    (
        "password authentication failed",
        "wrong database password. Reset it under Project Settings -> Database ->\n"
        "    Database password, then update DATABASE_URL_SUPABASE.",
    ),
    # psycopg3 says "failed to resolve host"; libpq says "could not translate
    # host name". Both mean the same thing and both must be recognised.
    (
        "failed to resolve host",
        "the hostname does not resolve. See the host note below.",
    ),
    (
        "could not translate host name",
        "the hostname does not resolve. See the host note below.",
    ),
    (
        "network is unreachable",
        "no route to the host. `db.<ref>.supabase.co` is IPv6-only on newer projects;\n"
        "    use the pooler host instead.",
    ),
    ("timeout expired", "unreachable -- firewall, VPN, or an IPv6-only host."),
    ("connection timed out", "unreachable -- firewall, VPN, or an IPv6-only host."),
    ("does not exist", "the database name is wrong; it is normally `postgres`."),
    ("ssl", "TLS negotiation failed. Supabase requires SSL; do not set sslmode=disable."),
    (
        "server closed the connection unexpectedly",
        "the server dropped the connection -- often the wrong port for the chosen host.",
    ),
)


def _redact(text: str, secret: str | None) -> str:
    """Remove the password, and nothing else.

    psycopg echoes the DSN in some errors. The password is the only true secret
    in it -- host and username are needed to diagnose anything, so they stay.
    """
    if not secret:
        return text
    from urllib.parse import quote

    for form in {secret, quote(secret, safe=""), quote(secret)}:
        if form:
            text = text.replace(form, "[REDACTED]")
    return text


def _diagnose_connection(database_url: str, exc: Exception) -> None:
    """Explain a failed connection, layer by layer."""
    import socket

    parsed = urlparse(database_url)
    host, port = parsed.hostname, parsed.port or 5432

    print(f"  CANNOT CONNECT: {type(exc).__name__}")
    message = _redact(str(exc), parsed.password).strip()
    for line in message.splitlines():
        if line.strip():
            print(f"    {line.strip()}")

    lowered = message.lower()
    for needle, meaning in CONNECT_HINTS:
        if needle in lowered:
            print(f"\n  likely cause: {meaning}")
            break
    else:
        print("\n  no known cause matched -- the layer check below narrows it down.")

    # The single most common misconfiguration: taking the direct-connection URI
    # when the pooler is what actually works from most networks.
    if host and host.startswith("db.") and host.endswith(".supabase.co"):
        ref = host[len("db.") : -len(".supabase.co")]
        print(
            "\n  NOTE: that is Supabase's DIRECT connection host. It has no IPv4 address\n"
            "  (and on some projects no DNS record at all), so it fails from most\n"
            "  networks. Use the transaction pooler instead:\n\n"
            f"    postgresql://postgres.{ref}:<password>"
            "@aws-0-<region>.pooler.supabase.com:6543/postgres\n\n"
            "  Take it from Project Settings -> Database -> Connection string ->\n"
            "  Transaction pooler. Note the username becomes `postgres.<project-ref>`."
        )

    print("\n  connectivity:")
    if not host:
        print("    host                no host in the URI -- it is malformed")
        return
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, port)})
        print(f"    DNS                 ok -> {', '.join(addresses)}")
        if all(":" in a for a in addresses):
            print("    ^ IPv6 only. Use the pooler host unless you have the IPv4 add-on.")
    except OSError as dns_error:
        print(f"    DNS                 FAILED ({type(dns_error).__name__})")
        return
    try:
        with socket.create_connection((host, port), timeout=8):
            print(f"    TCP :{port:<15} ok")
    except OSError as tcp_error:
        print(f"    TCP :{port:<15} FAILED ({type(tcp_error).__name__})")
        print("    ^ nothing accepted the connection: wrong port, firewall, or host down.")
        return
    print("    => network is fine; the failure is authentication or database selection.")


def check_db() -> int:
    from app.core.config import get_settings
    from app.db.session import connect_sync

    settings = get_settings()
    print(f"\nSchema check ({'remote' if settings.is_remote_db else 'local'}) -- read-only")
    try:
        conn = connect_sync(connect_timeout=10)
    except Exception as exc:
        _diagnose_connection(settings.database_url, exc)
        return 1

    with conn, conn.cursor() as cur:
        cur.execute("select current_database(), current_user, version()")
        db, user, version = cur.fetchone()
        print(f"  connected as          {user} on {db}")
        print(f"  server                {version.split(',')[0]}")

        cur.execute("select extname from pg_extension order by extname")
        extensions = {r[0] for r in cur.fetchall()}
        print(f"  extensions            {', '.join(sorted(extensions)) or '(none)'}")
        print(f"  pgvector              {'yes' if 'vector' in extensions else 'MISSING'}")

        cur.execute("select tablename from pg_tables where schemaname = 'public'")
        tables = {r[0] for r in cur.fetchall()}
        missing = [t for t in CORE_TABLES if t not in tables]
        print(f"  public tables         {len(tables)}")
        summary = "all present" if not missing else "MISSING: " + ", ".join(missing)
        print(f"  expected tables       {summary}")

        if "schema_migrations" in tables:
            cur.execute("select filename from schema_migrations order by filename")
            applied = [r[0] for r in cur.fetchall()]
            print(f"  migrations recorded   {len(applied)}/{EXPECTED_MIGRATIONS}")
            for name in applied:
                print(f"                          {name}")
        else:
            print("  migrations recorded   no schema_migrations table")
            print("                          (schema may have been applied another way)")

        cur.execute(
            "select c.relname from pg_trigger t join pg_class c on c.oid = t.tgrelid"
            " where t.tgname like '%_append_only' and not t.tgisinternal"
        )
        guarded = {r[0] for r in cur.fetchall()}
        unguarded = [t for t in APPEND_ONLY if t not in guarded]
        print(f"  append-only triggers  {len(guarded)}/{len(APPEND_ONLY)}")
        if unguarded:
            print(f"                          missing on: {', '.join(unguarded)}")

        cur.execute("select count(*) from pg_tables where schemaname='public' and rowsecurity")
        print(f"  tables with RLS on    {cur.fetchone()[0]}/{len(tables)}")

        for table in ("client", "instrument", "model_portfolio", "instrument_price"):
            if table in tables:
                cur.execute(f"select count(*) from {table}")  # noqa: S608 - fixed allowlist
                print(f"  rows in {table:<14}{cur.fetchone()[0]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="store_true", help="also verify the database schema")
    args = ap.parse_args()

    status = check_config()
    if status == 0 and args.db:
        status = check_db()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
