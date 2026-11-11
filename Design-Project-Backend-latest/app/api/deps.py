"""Shared FastAPI dependencies.

The session date is resolved here rather than accepted blindly: an unpriced date
would fail deep inside the trade arithmetic, where the error is far less
intelligible than it is at the edge.
"""

from __future__ import annotations

import datetime as _dt

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.auth import Principal, require_admin, require_client
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.db.session import get_connection
from app.domain.trading.execution import ExecutionService
from app.llm.factory import get_llm_client
from app.llm.faults import FAULTS, set_fault
from app.llm.faults import HEADER as FAULT_HEADER
from app.orchestration.pipeline import ChatPipeline


async def client_repo(
    conn: AsyncConnection = Depends(get_connection),
    principal: Principal = Depends(require_client),
) -> ClientRepository:
    return ClientRepository(conn, principal)


async def portfolio_repo(
    conn: AsyncConnection = Depends(get_connection),
    principal: Principal = Depends(require_client),
) -> PortfolioRepository:
    return PortfolioRepository(conn, principal)


async def market_repo(conn: AsyncConnection = Depends(get_connection)) -> MarketRepository:
    return MarketRepository(conn)


async def session_date(
    session_date: _dt.date | None = Query(default=None, alias="session_date"),
    repo: ClientRepository = Depends(client_repo),
    market: MarketRepository = Depends(market_repo),
) -> _dt.date:
    """Explicit query parameter, else the date chosen at login.

    There is no fallback to the real calendar: every valuation in the system is a
    function of a session date the user picked (invariant 4).
    """
    resolved = session_date or await repo.current_session_date()
    if resolved is None:
        first, last = await market.available_dates()
        raise ValidationError(
            "Select a session date before viewing prices or holdings."
            + (f" Prices are available from {first} to {last}." if first else "")
        )
    return resolved


async def execution_service(
    conn: AsyncConnection = Depends(get_connection),
    principal: Principal = Depends(require_client),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> ExecutionService:
    return ExecutionService(conn, portfolios, market, principal.require_client_id())


async def chat_pipeline(
    conn: AsyncConnection = Depends(get_connection),
    principal: Principal = Depends(require_client),
    clients: ClientRepository = Depends(client_repo),
    when: _dt.date | None = Query(default=None, alias="session_date"),
) -> ChatPipeline:
    return ChatPipeline(
        conn,
        llm=get_llm_client(),
        clients=clients,
        client_id=principal.require_client_id(),
        session_date=when,
    )


async def idempotency_key(
    key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """Required on every transactional endpoint (B.6).

    Without it a retried request -- by the browser, a proxy, or an impatient
    user -- would place a second order.
    """
    if not key or len(key) < 8:
        raise ValidationError(
            "This action requires an Idempotency-Key header of at least 8 characters."
        )
    return key


async def apply_fault_header(
    fault: str | None = Header(default=None, alias=FAULT_HEADER),
    _: Principal = Depends(require_admin),
) -> None:
    """Admin-only, and only when explicitly enabled. Used to demonstrate B.6."""
    if fault and get_settings().allow_fault_injection:
        if fault not in FAULTS:
            raise ValidationError(f"Unknown fault {fault!r}. Expected one of {sorted(FAULTS)}.")
        set_fault(fault)
