"""Read-only valuation and confirmation previews.

Separate from `execution.py` because these never write: the dashboard, the
holdings view and the confirmation preview all run through here, need no
transaction, and must keep working when the LLM is unreachable.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.core.money import ONE_HUNDRED, ZERO, round_money, round_weight
from app.db.repositories.market import MarketRepository, ModelPortfolio
from app.db.repositories.portfolio import PortfolioRepository, Snapshot
from app.domain.trading.arithmetic import (
    actual_weights,
    drift,
    rebalance_trades,
    value_holdings,
)


async def current_portfolio(
    portfolios: PortfolioRepository, market: MarketRepository, snapshot: Snapshot
) -> ModelPortfolio:
    portfolio_id = await portfolios.current_portfolio_id()
    if portfolio_id is None or snapshot.is_empty:
        raise NotFound("You do not hold a portfolio yet.")
    return await market.portfolio_by_id(portfolio_id)


async def value_position(
    portfolios: PortfolioRepository, market: MarketRepository, session_date: _dt.date
) -> dict:
    """The client's position valued at `session_date` (A.5 iv, C.1)."""
    snapshot = await portfolios.latest_snapshot(get_settings().starting_cash)
    portfolio_id = await portfolios.current_portfolio_id()
    portfolio = await market.portfolio_by_id(portfolio_id) if portfolio_id else None
    prices = await market.prices_on(session_date, set(snapshot.units)) if snapshot.units else {}
    values = value_holdings(snapshot.units, prices)
    return {
        "snapshot": snapshot,
        "portfolio": portfolio,
        "prices": prices,
        "values": values,
        "actual_weights": actual_weights(values, snapshot.cash),
        "target_weights": portfolio.targets if portfolio else {},
        "total_value": round_money(sum(values.values(), ZERO) + snapshot.cash),
    }


def _planned_trades(
    snapshot: Snapshot,
    prices: dict,
    targets: dict,
    *,
    band_pct: Decimal,
) -> list:
    settings = get_settings()
    return rebalance_trades(
        snapshot.units,
        prices,
        targets,
        snapshot.cash,
        band_pct=band_pct,
        min_notional=settings.min_trade_notional,
    )


async def preview_rebalance(
    portfolios: PortfolioRepository, market: MarketRepository, session_date: _dt.date
) -> dict:
    """What a rebalance would do, without writing anything.

    C.1 requires the client to see the proposed trades and why the allocation
    drifted *before* anything is executed.
    """
    snapshot = await portfolios.latest_snapshot(get_settings().starting_cash)
    portfolio = await current_portfolio(portfolios, market, snapshot)
    prices = await market.prices_on(session_date, set(portfolio.targets) | set(snapshot.units))
    values = value_holdings(snapshot.units, prices)
    current = actual_weights(values, snapshot.cash)
    band = get_settings().rebalance_band_pct
    trades = _planned_trades(snapshot, prices, portfolio.targets, band_pct=band)
    return {
        "snapshot": snapshot,
        "portfolio": portfolio,
        "prices": prices,
        "values": values,
        "actual_weights": current,
        "target_weights": portfolio.targets,
        "drift": drift(current, portfolio.targets),
        "trades": trades,
        "band_pct": band,
        "total_value": round_money(sum(values.values(), ZERO) + snapshot.cash),
    }


async def preview_switch(
    portfolios: PortfolioRepository,
    market: MarketRepository,
    session_date: _dt.date,
    portfolio: ModelPortfolio,
) -> dict:
    """Trades that would move the current position onto `portfolio`'s targets."""
    snapshot = await portfolios.latest_snapshot(get_settings().starting_cash)
    current = await current_portfolio(portfolios, market, snapshot)
    if current.code == portfolio.code:
        raise Conflict(
            f"You already hold {portfolio.code}. Use rebalance to restore its target weights."
        )
    prices = await market.prices_on(session_date, set(portfolio.targets) | set(snapshot.units))
    values = value_holdings(snapshot.units, prices)
    actual = actual_weights(values, snapshot.cash)
    # A switch is a change of mandate, not a drift trim: any target difference
    # must trade. Dust notionals are still dropped via min_notional.
    trades = _planned_trades(snapshot, prices, portfolio.targets, band_pct=Decimal("0"))
    return {
        "snapshot": snapshot,
        "portfolio": portfolio,
        "from_portfolio": current,
        "prices": prices,
        "values": values,
        "actual_weights": actual,
        "target_weights": portfolio.targets,
        "drift": drift(actual, portfolio.targets),
        "trades": trades,
        "band_pct": Decimal("0"),
        "total_value": round_money(sum(values.values(), ZERO) + snapshot.cash),
    }


async def preview_revert(
    portfolios: PortfolioRepository, market: MarketRepository, session_date: _dt.date
) -> dict:
    """The position a revert would restore, valued at `session_date`."""
    target_event = await portfolios.latest_event("rebalance")
    if target_event is None:
        raise NotFound("There is no rebalance to revert.")

    latest = await portfolios.latest_event()
    if latest is not None and latest.event_type == "revert":
        raise Conflict("The most recent rebalance has already been reverted.")

    restored = await portfolios.snapshot_before_event(target_event.seq)
    if restored is None:  # pragma: no cover
        raise NotFound("No pre-rebalance position was recorded.")

    prices = await market.prices_on(session_date, set(restored.units)) if restored.units else {}
    values = value_holdings(restored.units, prices)
    total = round_money(sum(values.values(), ZERO) + restored.cash)
    portfolio = None
    if target_event.portfolio_id:
        portfolio = await market.portfolio_by_id(target_event.portfolio_id)
    return {
        "snapshot": restored,
        "portfolio": portfolio,
        "rebalance_session_date": target_event.session_date,
        "session_date": session_date,
        "prices": prices,
        "values": values,
        "cash": restored.cash,
        "total_value": total,
    }


def cash_weight(snapshot: Snapshot, total_value: Decimal) -> Decimal:
    if total_value <= ZERO:
        return ZERO
    return round_weight(snapshot.cash * ONE_HUNDRED / total_value)
