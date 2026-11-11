"""Projections shared by the routers and the chat pipeline.

Two things live here because both the dashboard and the prompt need them and they
must not diverge: the asset-class aggregation of holdings, and the bounded
client-facts bundle.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from app.api.dto.common import HoldingRowDto, InstrumentPositionDto, PerformancePointDto
from app.core.errors import NotFound
from app.core.money import ONE_HUNDRED, ZERO, round_money, round_weight
from app.db.repositories.market import Instrument, MarketRepository
from app.domain.trading.arithmetic import value_holdings

ASSET_CLASS_LABEL = {
    "cash": "Cash",
    "bonds": "Bonds",
    "equities": "Equities",
    "alternatives": "Alternatives",
}
DISPLAY_ORDER = ("cash", "bonds", "equities", "alternatives")


def instrument_positions(valuation: dict, instruments: dict) -> list[InstrumentPositionDto]:
    """Per-instrument view: what is actually stored."""
    snapshot = valuation["snapshot"]
    rows = []
    for instrument_id, units in sorted(
        snapshot.units.items(), key=lambda kv: instruments[kv[0]].symbol
    ):
        info: Instrument = instruments[instrument_id]
        rows.append(
            InstrumentPositionDto(
                instrument_id=str(instrument_id),
                symbol=info.symbol,
                name=info.name,
                asset_class=info.asset_class,
                is_satellite=info.is_satellite,
                units=units,
                price=valuation["prices"].get(instrument_id, ZERO),
                value=valuation["values"].get(instrument_id, ZERO),
                target_weight=valuation["target_weights"].get(instrument_id, ZERO),
                actual_weight=valuation["actual_weights"].get(instrument_id, ZERO),
            )
        )
    return rows


def asset_class_rows(valuation: dict, instruments: dict) -> list[HoldingRowDto]:
    """The three-row view the frontend renders.

    An aggregation for display only -- positions are stored per instrument, and
    the arithmetic that matters is done there. Cash is folded in as its own row
    so the weights on screen sum to 100.
    """
    snapshot = valuation["snapshot"]
    buckets: dict[str, dict[str, Decimal]] = {
        ac: {"units": ZERO, "value": ZERO, "target": ZERO, "actual": ZERO} for ac in DISPLAY_ORDER
    }

    for instrument_id, units in snapshot.units.items():
        bucket = buckets[instruments[instrument_id].asset_class]
        bucket["units"] += units
        bucket["value"] += valuation["values"].get(instrument_id, ZERO)
        bucket["target"] += valuation["target_weights"].get(instrument_id, ZERO)
        bucket["actual"] += valuation["actual_weights"].get(instrument_id, ZERO)

    total = valuation["total_value"]
    cash = snapshot.cash
    buckets["cash"]["units"] += cash
    buckets["cash"]["value"] += cash
    if total > ZERO:
        buckets["cash"]["actual"] += round_weight(cash * ONE_HUNDRED / total)

    return [
        HoldingRowDto(
            asset=ASSET_CLASS_LABEL[ac],
            units=b["units"],
            # A blended price across an asset class is meaningless; the
            # per-instrument view carries real prices.
            price=round_money(b["value"] / b["units"]) if b["units"] > ZERO else ZERO,
            value=round_money(b["value"]),
            target_weight=round_weight(b["target"]),
            actual_weight=round_weight(b["actual"]),
        )
        for ac in DISPLAY_ORDER
        if (b := buckets[ac])["value"] > ZERO or b["target"] > ZERO
    ]


async def performance_series(
    market: MarketRepository, snapshot, session_date: _dt.date, days: int
) -> list[PerformancePointDto]:
    """Value the *current* position back through history.

    This is a valuation series, not a return series: it shows what today's
    holdings were worth on each past date, which is what makes drift visible.
    """
    if not snapshot.units:
        return []
    start = session_date - _dt.timedelta(days=days)
    history = await market.price_history(set(snapshot.units), start, session_date)

    points = []
    for day in sorted(history):
        prices = history[day]
        if set(snapshot.units) - prices.keys():
            continue  # a partial day would draw a false dip
        values = value_holdings(snapshot.units, prices)
        points.append(
            PerformancePointDto(
                date=day.strftime("%m-%d"),
                value=round_money(sum(values.values(), ZERO) + snapshot.cash),
            )
        )
    return points


def client_facts(
    *,
    profile,
    risk_category: str | None,
    eligible: tuple[str, ...],
    held_portfolio: str | None,
    valuation: dict | None,
    holdings: list | None = None,
    recent_transactions: list | None = None,
) -> dict:
    """The bounded fact bundle attached to every prompt.

    Deliberately small and flat. It carries the *outputs* of the deterministic
    engines -- category, eligibility, valuations -- and never their internals.
    """
    facts: dict = {
        "clientName": profile.full_name,
        "riskCategory": risk_category,
        "eligiblePortfolios": list(eligible),
        "heldPortfolio": held_portfolio,
        "investmentHorizonYrs": profile.investment_horizon_yrs,
        "lossTolerance": profile.loss_tolerance,
        "liquidityNeeds": profile.liquidity_needs,
        "incomeStability": profile.income_stability,
        "investmentExperience": profile.investment_experience,
        "investmentObjective": profile.investment_objective,
        "dependants": profile.dependants,
        "ckaPassed": profile.cka_passed,
        # The other capacity inputs -- dependants, horizon, income stability --
        # were already here; these two were not, so the assistant could explain
        # that commitments affect capacity and then deny holding the figure when
        # asked. `FACT_PRIORITY` has always listed both, and drops them first
        # under budget pressure, so the context builder expected them.
        "monthlySalary": profile.monthly_salary,
        "monthlyCommitments": profile.monthly_commitments,
    }
    if valuation is not None:
        facts["cash"] = float(valuation["snapshot"].cash)
        facts["totalValue"] = float(valuation["total_value"])
    if holdings:
        # What the client actually owns, by asset class -- the same rows the
        # dashboard renders. Without these, "what does my portfolio hold?" had
        # no evidence behind it: the bundle named the portfolio code and the
        # total, so the model had to invent the composition, and the egress
        # faithfulness check correctly refused to let an invented answer out.
        # The question looked unanswerable when it was only unevidenced.
        facts["holdings"] = [
            {
                "assetClass": row.asset,
                "value": float(row.value),
                "actualWeightPct": float(row.actual_weight),
                "targetWeightPct": float(row.target_weight),
            }
            for row in holdings
        ]
    if recent_transactions:
        # Same shape of bug, for the ledger. "When I invested, what did I buy?"
        # had the portfolio code and nothing else, so the model drafted an
        # explanation the faithfulness judge had no trades to check against.
        facts["recentTransactions"] = recent_transactions
    return {k: v for k, v in facts.items() if v is not None}


async def recent_transactions(
    portfolios, market: MarketRepository, *, limit: int = 10
) -> list[dict]:
    """Last N portfolio events as a flat, bounded evidence list.

    Outputs, not internals: event type, session date, portfolio code, the
    linked decision reference, and per-trade symbol/side/units/price/amount.
    """
    events = await portfolios.events(limit=limit)
    instruments = await market.instruments()
    rows: list[dict] = []
    for event in events:
        trades = await portfolios.trades_for_event(event.id)
        portfolio_code = None
        if event.portfolio_id:
            try:
                portfolio_code = (await market.portfolio_by_id(event.portfolio_id)).code
            except NotFound:
                portfolio_code = None
        decision_no = None
        if event.decision_id:
            decision_no = await portfolios.decision_display_no(event.decision_id)
        rows.append(
            {
                "eventType": event.event_type,
                "sessionDate": event.session_date.isoformat(),
                "portfolioCode": portfolio_code,
                "decisionId": decision_no,
                "trades": [
                    {
                        "symbol": (
                            instruments[t["instrument_id"]].symbol
                            if t["instrument_id"] in instruments
                            else str(t["instrument_id"])
                        ),
                        "side": t["side"],
                        "units": float(t["units"]),
                        "price": float(t["price"]),
                        "grossAmount": float(t["gross_amount"]),
                    }
                    for t in trades
                ],
            }
        )
    return rows
