"""Transactional portfolio endpoints (A.5, A.6, A.7) and the C.1 confirmation preview.

Every write requires an Idempotency-Key. None of these call the LLM: the trade
and its explanation are separate concerns, so a model outage cannot roll back a
correctly executed transaction.
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    client_repo,
    execution_service,
    idempotency_key,
    market_repo,
    portfolio_repo,
    session_date,
)
from app.api.dto.common import Dto, Money
from app.api.dto.requests import InvestRequest, RebalanceRequest, SwitchRequest
from app.audit import service as audit
from app.core.config import get_settings
from app.core.errors import Conflict, SuitabilityRefused
from app.core.money import ZERO
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository, ModelPortfolio
from app.db.repositories.portfolio import PortfolioRepository
from app.db.session import get_engine
from app.domain.suitability.engine import (
    SuitabilityResult,
    check_satellite_exposure,
    evaluate,
    fired_rules_text,
)
from app.domain.trading.execution import ExecutionService, compute_preview_token, run_idempotent
from app.domain.trading.valuation import (
    cash_weight,
    current_portfolio,
    preview_rebalance,
    preview_revert,
    preview_switch,
)

router = APIRouter(tags=["portfolio"])


class TradeDto(Dto):
    symbol: str
    name: str
    side: str
    units: Money
    price: Money
    grossAmount: Money


class RebalancePreviewDto(Dto):
    sessionDate: _dt.date
    portfolioCode: str
    portfolioName: str
    totalValue: Money
    cash: Money
    trades: list[TradeDto]
    drift: list[dict]
    explanation: str
    previewToken: str
    alreadyOnTarget: bool = False
    requiresConfirmation: bool = True


class RevertPreviewDto(Dto):
    sessionDate: _dt.date
    rebalanceSessionDate: _dt.date
    portfolioCode: str | None
    portfolioName: str | None
    cash: Money
    totalValue: Money
    positions: list[dict]
    explanation: str


async def _trade_dtos(trades, market: MarketRepository) -> list[TradeDto]:
    instruments = await market.instruments()
    return [
        TradeDto(
            symbol=instruments[t.instrument_id].symbol,
            name=instruments[t.instrument_id].name,
            side=t.side,
            units=t.units,
            price=t.price,
            grossAmount=t.gross_amount,
        )
        for t in trades
    ]


async def _enrich_trades(payload: dict, market: MarketRepository) -> dict:
    """Attach symbol/name so the order ticket shows units × session-date price."""
    trades = payload.get("trades") or []
    if not trades:
        return payload
    instruments = await market.instruments()
    by_id = {str(iid): info for iid, info in instruments.items()}
    for trade in trades:
        info = by_id.get(str(trade.get("instrumentId") or ""))
        if info is None:
            continue
        trade["symbol"] = info.symbol
        trade["name"] = info.name
    return payload


def _drift_rows(preview: dict, instruments: dict) -> list[dict]:
    """Instrument rows plus the cash drag, so underweights are not a mystery.

    `actual_weights` puts cash in the denominator and its docstring says the
    drag is exactly what the drift view should show. Omitting cash made every
    instrument look underweight with no on-screen reason.
    """
    rows = [
        {
            "symbol": instruments[iid].symbol,
            "targetWeight": float(preview["target_weights"].get(iid, 0)),
            "actualWeight": float(preview["actual_weights"].get(iid, 0)),
            "driftPct": float(value),
        }
        for iid, value in sorted(preview["drift"].items(), key=lambda kv: -abs(kv[1]))
        if iid in instruments
    ]
    snapshot = preview["snapshot"]
    cash_pct = float(cash_weight(snapshot, preview["total_value"]))
    rows.append(
        {
            "symbol": "CASH",
            "targetWeight": 0.0,
            "actualWeight": cash_pct,
            "driftPct": cash_pct,
        }
    )
    return rows


def _preview_explanation(preview: dict, drift_rows: list[dict], *, switching: bool = False) -> str:
    """Tell the truth about whether anything material has moved.

    The previous copy always named the worst instrument, including a +0.00pp
    'drift' on the invest's own session date.
    """
    band = preview.get("band_pct") or get_settings().rebalance_band_pct
    material = [
        row for row in drift_rows if row["symbol"] != "CASH" and abs(row["driftPct"]) > float(band)
    ]
    if switching:
        dest = preview["portfolio"]
        origin = preview.get("from_portfolio")
        from_code = origin.code if origin is not None else "your current portfolio"
        if not preview["trades"]:
            return (
                f"Switching to {dest.code} {dest.name} would not require any trades "
                "at these prices — the position already matches that allocation."
            )
        return (
            f"These trades sell what {from_code} holds and buy {dest.code} "
            f"{dest.name}'s target mix at the selected session date's prices. "
            "Instruments the destination does not target are sold in full."
        )

    if not preview["trades"]:
        return (
            "Your allocation is already within the rebalancing band of its targets "
            "at these prices. No trades are needed."
        )
    worst = max(material or drift_rows, key=lambda row: abs(row["driftPct"]))
    named = (
        f" — {worst['symbol']} is {worst['driftPct']:+.2f} percentage points off. "
        if worst["symbol"] != "CASH"
        else ". "
    )
    return (
        "Prices have moved since your last transaction, so your actual allocation "
        "has drifted from its targets"
        + named
        + "The trades below sell what has grown beyond its target share and buy what "
        "has fallen below it, restoring the mix your risk category supports."
    )


def preview_token_of(
    *, client_id, snapshot_seq: int, session_date: _dt.date, portfolio_code: str, trades
) -> str:
    return compute_preview_token(
        client_id=client_id,
        snapshot_seq=snapshot_seq,
        session_date=session_date,
        portfolio_code=portfolio_code,
        trades=trades,
    )


async def _as_preview_dto(
    preview: dict,
    *,
    when: _dt.date,
    market: MarketRepository,
    client_id,
    switching: bool = False,
) -> RebalancePreviewDto:
    instruments = await market.instruments()
    portfolio = preview["portfolio"]
    drift_rows = _drift_rows(preview, instruments)
    trades = preview["trades"]
    token = preview_token_of(
        client_id=client_id,
        snapshot_seq=preview["snapshot"].seq,
        session_date=when,
        portfolio_code=portfolio.code,
        trades=trades,
    )
    return RebalancePreviewDto(
        sessionDate=when,
        portfolioCode=portfolio.code,
        portfolioName=portfolio.name,
        totalValue=preview["total_value"],
        cash=preview["snapshot"].cash,
        trades=await _trade_dtos(trades, market),
        drift=drift_rows,
        explanation=_preview_explanation(preview, drift_rows, switching=switching),
        previewToken=token,
        alreadyOnTarget=not trades,
    )


async def _enforce_suitability(
    *,
    clients: ClientRepository,
    market: MarketRepository,
    portfolio: ModelPortfolio,
    when: _dt.date,
    input_label: str,
) -> SuitabilityResult:
    """Refuse + escalate the way invest does. Used by rebalance and switch too.

    Written on its own transaction, not the request's. `get_connection` wraps
    the handler in `engine.begin()`, which rolls back when an exception leaves
    the handler -- and the next statement raises one. Auditing the refusal on
    `conn` would therefore discard the very escalation A.3(iv) requires, leaving
    only a gap in the decision display_no sequence as evidence it ever happened.
    """
    profile = await clients.profile()
    assessment = await clients.latest_risk_assessment()
    if assessment is None:
        raise Conflict("Complete your risk profiling conversation before investing.")

    result = evaluate(profile=profile, risk_category=assessment.category)
    instruments = await market.instruments()
    satellite_ids = {i for i, info in instruments.items() if info.is_satellite}
    breach = None if result.permits(portfolio.code) else result.refusal_reason(portfolio.code)
    breach = breach or check_satellite_exposure(result, portfolio.targets, satellite_ids)
    if not breach:
        return result

    async with get_engine().begin() as audit_conn:
        await audit.record_decision(
            audit_conn,
            client_id=profile.client_id,
            decision_type="suitability_gate",
            status="refused",
            input=input_label,
            output=f"{breach}\n\n{fired_rules_text(result)}",
            policy_version=result.policy_version,
            session_date=when,
            escalate=audit.EscalationReason("suitability_refusal"),
        )
    raise SuitabilityRefused(breach)


@router.post("/clients/me/invest")
async def invest(
    body: InvestRequest,
    when: _dt.date = Depends(session_date),
    key: str = Depends(idempotency_key),
    service: ExecutionService = Depends(execution_service),
    clients: ClientRepository = Depends(client_repo),
    market: MarketRepository = Depends(market_repo),
) -> dict:
    """A.5: deploy the pre-funded balance into an eligible portfolio.

    Eligibility is checked by the deterministic engine, not the model, and a
    request above the client's assessed category is refused and escalated (A.3 iv)
    rather than negotiated.
    """
    portfolio = await market.portfolio_by_code(body.portfolio_code)
    result = await _enforce_suitability(
        clients=clients,
        market=market,
        portfolio=portfolio,
        when=when,
        input_label=f"Requested {body.portfolio_code}",
    )
    payload = await run_idempotent(
        service.invest(
            portfolio=portfolio,
            session_date=when,
            idempotency_key=key,
            policy_version=result.policy_version,
        )
    )
    return await _enrich_trades(payload, market)


@router.get("/clients/me/rebalance/preview", response_model=RebalancePreviewDto)
async def rebalance_preview(
    when: _dt.date = Depends(session_date),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> RebalancePreviewDto:
    """C.1: the client sees the proposed trades and why, before anything executes."""
    preview = await preview_rebalance(portfolios, market, when)
    return await _as_preview_dto(preview, when=when, market=market, client_id=portfolios._client_id)


@router.post("/clients/me/rebalance")
async def rebalance(
    body: RebalanceRequest,
    when: _dt.date = Depends(session_date),
    key: str = Depends(idempotency_key),
    service: ExecutionService = Depends(execution_service),
    clients: ClientRepository = Depends(client_repo),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> dict:
    snapshot = await portfolios.latest_snapshot(get_settings().starting_cash)
    portfolio = await current_portfolio(portfolios, market, snapshot)
    result = await _enforce_suitability(
        clients=clients,
        market=market,
        portfolio=portfolio,
        when=when,
        input_label=f"Rebalance {portfolio.code}",
    )
    payload = await run_idempotent(
        service.rebalance(
            session_date=when,
            idempotency_key=key,
            preview_token=body.preview_token,
            policy_version=result.policy_version,
        )
    )
    return await _enrich_trades(payload, market)


@router.get("/clients/me/switch/preview", response_model=RebalancePreviewDto)
async def switch_preview(
    portfolio_code: str = Query(alias="portfolio_code", pattern=r"^P[1-8]$"),
    when: _dt.date = Depends(session_date),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> RebalancePreviewDto:
    """C.1 for a change of model portfolio: proposed trades, then confirm."""
    dest = await market.portfolio_by_code(portfolio_code)
    preview = await preview_switch(portfolios, market, when, dest)
    return await _as_preview_dto(
        preview,
        when=when,
        market=market,
        client_id=portfolios._client_id,
        switching=True,
    )


@router.post("/clients/me/switch")
async def switch_portfolio(
    body: SwitchRequest,
    when: _dt.date = Depends(session_date),
    key: str = Depends(idempotency_key),
    service: ExecutionService = Depends(execution_service),
    clients: ClientRepository = Depends(client_repo),
    market: MarketRepository = Depends(market_repo),
) -> dict:
    portfolio = await market.portfolio_by_code(body.portfolio_code)
    result = await _enforce_suitability(
        clients=clients,
        market=market,
        portfolio=portfolio,
        when=when,
        input_label=f"Switch to {body.portfolio_code}",
    )
    payload = await run_idempotent(
        service.switch(
            portfolio=portfolio,
            session_date=when,
            idempotency_key=key,
            preview_token=body.preview_token,
            policy_version=result.policy_version,
        )
    )
    return await _enrich_trades(payload, market)


@router.get("/clients/me/rebalance/revert/preview", response_model=RevertPreviewDto)
async def revert_preview(
    when: _dt.date = Depends(session_date),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> RevertPreviewDto:
    """A.7: show the position a revert would restore, before anything executes."""
    preview = await preview_revert(portfolios, market, when)
    instruments = await market.instruments()
    portfolio = preview["portfolio"]
    positions = [
        {
            "symbol": instruments[iid].symbol,
            "name": instruments[iid].name,
            "units": float(units),
            "price": float(preview["prices"].get(iid, ZERO)),
            "value": float(preview["values"].get(iid, ZERO)),
        }
        for iid, units in sorted(
            preview["snapshot"].units.items(),
            key=lambda kv: instruments[kv[0]].symbol if kv[0] in instruments else str(kv[0]),
        )
        if iid in instruments
    ]
    when_rebalanced = preview["rebalance_session_date"].isoformat()
    return RevertPreviewDto(
        sessionDate=when,
        rebalanceSessionDate=preview["rebalance_session_date"],
        portfolioCode=portfolio.code if portfolio else None,
        portfolioName=portfolio.name if portfolio else None,
        cash=preview["cash"],
        totalValue=preview["total_value"],
        positions=positions,
        explanation=(
            f"Revert restores the holdings from immediately before the rebalance "
            f"on {when_rebalanced}, valued here at {when.isoformat()} prices. "
            "Nothing is deleted; a new snapshot is appended."
        ),
    )


@router.post("/clients/me/rebalance/revert")
async def revert(
    when: _dt.date = Depends(session_date),
    key: str = Depends(idempotency_key),
    service: ExecutionService = Depends(execution_service),
) -> dict:
    """A.7: restore the snapshot from immediately before the last rebalance."""
    return await run_idempotent(service.revert(session_date=when, idempotency_key=key))


@router.get("/clients/me/events")
async def events(portfolios: PortfolioRepository = Depends(portfolio_repo)) -> list[dict]:
    return [
        {
            "eventId": str(e.id),
            "seq": e.seq,
            "eventType": e.event_type,
            "sessionDate": e.session_date,
            "revertsEventId": str(e.reverts_event_id) if e.reverts_event_id else None,
            "decisionId": str(e.decision_id) if e.decision_id else None,
            "createdAt": e.created_at,
        }
        for e in await portfolios.events()
    ]
