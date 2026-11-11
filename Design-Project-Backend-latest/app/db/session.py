from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.config import get_settings

#: Supabase's Supavisor transaction-mode pooler. It multiplexes sessions across
#: backends, so server-side prepared statements are not usable on it.
TRANSACTION_POOLER_PORT = 6543


def _async_url(url: str) -> str:
    """Normalise a plain libpq URL onto SQLAlchemy's async psycopg driver."""
    if "+" in url.split("://", 1)[0]:
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _connect_args(url: str) -> dict:
    """Driver options the target database requires.

    Against a transaction pooler, psycopg's automatic prepared statements fail
    with "prepared statement already exists" once a connection is reused by a
    different session, so they are turned off. Direct connections keep them.
    """
    parsed = urlparse(url)
    args: dict = {}
    if parsed.port == TRANSACTION_POOLER_PORT:
        args["prepare_threshold"] = None
    return args


def connect_sync(**overrides):
    """Open a plain psycopg connection with the target's required options.

    Every script goes through this rather than calling `psycopg.connect` itself.
    The engine already handles the pooler's prepared-statement limitation, and
    scripts hit it just as hard -- `executemany` prepares on the first call, so
    a bulk load fails immediately where single statements survive.
    """
    import psycopg

    settings = get_settings()
    return psycopg.connect(
        settings.database_url, **{**_connect_args(settings.database_url), **overrides}
    )


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.database_url
    pooled = urlparse(url).port == TRANSACTION_POOLER_PORT
    return create_async_engine(
        _async_url(url),
        pool_pre_ping=True,
        # Pre-ping alone is not enough across a laptop sleeping or a pooler
        # recycling its clients. A socket killed while the machine was suspended
        # can still pass the trivial ping and only fail when a real statement
        # writes to it, which surfaces as `OperationalError: server closed the
        # connection unexpectedly` on the first query of a request -- a 500 the
        # user sees, hours after the connection actually died. Capping a
        # connection's age means a stale one is retired rather than handed out.
        # Well under Supavisor's idle timeout, and reconnecting through a pooler
        # is cheap.
        pool_recycle=300,
        pool_size=10,
        max_overflow=5,
        future=True,
        connect_args=_connect_args(url),
        # SQLAlchemy's own compiled-statement cache assumes a stable backend.
        query_cache_size=0 if pooled else 500,
    )


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """One connection, one transaction, committed on clean exit.

    Invariant 10 depends on this: a decision row and the action it records share
    a transaction, so they commit or roll back together.
    """
    async with get_engine().begin() as conn:
        yield conn


async def get_connection() -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency. The request handler owns the transaction boundary."""
    async with get_engine().begin() as conn:
        yield conn


async def dispose_engine() -> None:
    await get_engine().dispose()
    get_engine.cache_clear()
