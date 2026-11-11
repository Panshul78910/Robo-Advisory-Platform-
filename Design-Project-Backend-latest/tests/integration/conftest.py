"""Fixtures for tests that need a migrated, seeded database.

Everything runs inside a transaction that is rolled back afterwards, so the
suite is re-runnable without `make reset` between runs.
"""

from __future__ import annotations

import datetime as _dt
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from app.core.auth import Principal
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.db.session import get_engine
from app.domain.trading.execution import ExecutionService


@pytest_asyncio.fixture
async def conn():
    """A connection whose transaction is always rolled back."""
    async with get_engine().connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


async def _principal_for(conn, client_code: str) -> Principal:
    row = (
        (
            await conn.execute(
                sa.text(
                    "select c.id as client_id, a.id as user_id, a.role, a.display_name"
                    "  from client c join app_user a on a.id = c.user_id"
                    " where c.client_code = :code"
                ),
                {"code": client_code},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        pytest.skip(f"client {client_code} not seeded; run `make reset`")
    return Principal(
        user_id=row["user_id"],
        role=row["role"],
        display_name=row["display_name"],
        client_id=row["client_id"],
    )


@pytest_asyncio.fixture
async def principal(conn) -> Principal:
    """Priya Sharma (C002): high risk, CKA passed, eligible for every portfolio."""
    return await _principal_for(conn, "C002")


@pytest_asyncio.fixture
async def other_principal(conn) -> Principal:
    """Lim Ah Hock (C001), for cross-client isolation checks."""
    return await _principal_for(conn, "C001")


@pytest_asyncio.fixture
async def admin_principal(conn) -> Principal:
    row = (
        (
            await conn.execute(
                sa.text("select id, role, display_name from app_user where role = 'admin' limit 1")
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        pytest.skip("no admin seeded; run `make reset`")
    return Principal(
        user_id=row["id"], role="admin", display_name=row["display_name"], client_id=None
    )


@pytest.fixture
def clients(conn, principal) -> ClientRepository:
    return ClientRepository(conn, principal)


@pytest.fixture
def portfolios(conn, principal) -> PortfolioRepository:
    return PortfolioRepository(conn, principal)


@pytest.fixture
def market(conn) -> MarketRepository:
    return MarketRepository(conn)


@pytest.fixture
def execution(conn, portfolios, market, principal) -> ExecutionService:
    return ExecutionService(conn, portfolios, market, principal.client_id)


@pytest_asyncio.fixture
async def session_dates(market) -> tuple[_dt.date, _dt.date]:
    """Two priced dates far enough apart for the allocation to have drifted."""
    first, last = await market.available_dates()
    if first is None:
        pytest.skip("no prices loaded; run `make reset`")
    priced = (
        (
            await market._conn.execute(
                sa.text("select distinct price_date from instrument_price order by price_date")
            )
        )
        .scalars()
        .all()
    )
    return priced[0], priced[-1]


@pytest_asyncio.fixture
async def assessed(conn, clients, principal):
    """Ensure the client has a risk assessment, as invest requires one."""
    existing = await clients.latest_risk_assessment()
    if existing is not None:
        return existing
    from app.domain.risk.scorer import ScoringInputs, score

    profile = await clients.profile()
    result = score(
        ScoringInputs(
            monthly_salary=profile.monthly_salary,
            monthly_commitments=profile.monthly_commitments,
            dependants=profile.dependants,
            investment_objective=profile.investment_objective,
            investment_horizon_yrs=profile.investment_horizon_yrs,
            loss_tolerance=profile.loss_tolerance,
            liquidity_needs=profile.liquidity_needs,
            income_stability=profile.income_stability,
            investment_experience=profile.investment_experience,
        )
    )
    await clients.record_risk_assessment(
        score=result.score,
        category=result.category,
        inputs_snapshot={},
        formula_version=result.formula_version,
    )
    return await clients.latest_risk_assessment()


def key(label: str) -> str:
    """A fresh idempotency key per call site."""
    return f"{label}-{uuid.uuid4().hex}"


async def preview_token_for_rebalance(execution, portfolios, market, when) -> str:
    from app.domain.trading.execution import compute_preview_token
    from app.domain.trading.valuation import preview_rebalance

    preview = await preview_rebalance(portfolios, market, when)
    return compute_preview_token(
        client_id=execution._client_id,
        snapshot_seq=preview["snapshot"].seq,
        session_date=when,
        portfolio_code=preview["portfolio"].code,
        trades=preview["trades"],
    )


async def preview_token_for_switch(execution, portfolios, market, when, portfolio) -> str:
    from app.domain.trading.execution import compute_preview_token
    from app.domain.trading.valuation import preview_switch

    preview = await preview_switch(portfolios, market, when, portfolio)
    return compute_preview_token(
        client_id=execution._client_id,
        snapshot_seq=preview["snapshot"].seq,
        session_date=when,
        portfolio_code=portfolio.code,
        trades=preview["trades"],
    )


async def execute_rebalance(execution, portfolios, market, when, idempotency_key, **kw):
    token = await preview_token_for_rebalance(execution, portfolios, market, when)
    return await execution.rebalance(
        session_date=when,
        idempotency_key=idempotency_key,
        preview_token=token,
        **kw,
    )


async def execute_switch(execution, portfolios, market, when, portfolio, idempotency_key, **kw):
    token = await preview_token_for_switch(execution, portfolios, market, when, portfolio)
    return await execution.switch(
        portfolio=portfolio,
        session_date=when,
        idempotency_key=idempotency_key,
        preview_token=token,
        **kw,
    )
