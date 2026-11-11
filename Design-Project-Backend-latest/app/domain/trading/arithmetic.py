"""Pure trade arithmetic. No I/O, no clock, no LLM.

Everything the supervisor checks against the price file in A.5(3) and A.6(2) is
computed here, so it can be exercised exhaustively without a database.

Conventions
-----------
* Weights are percentages (50 means 50%), and target weights sum to 100.
* Units are truncated, never rounded up: you cannot buy units you cannot fund.
* Residual value from that truncation lands in cash. It is never discarded --
  cash + market value is conserved across every operation in this module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.core.errors import PriceUnavailable, ValidationError
from app.core.money import (
    ONE_HUNDRED,
    ZERO,
    floor_units,
    round_money,
    round_units,
    round_weight,
    weights_sum_ok,
)

Units = dict[uuid.UUID, Decimal]
Prices = dict[uuid.UUID, Decimal]
Weights = dict[uuid.UUID, Decimal]
Values = dict[uuid.UUID, Decimal]


@dataclass(frozen=True, slots=True)
class Trade:
    instrument_id: uuid.UUID
    side: str  # 'buy' | 'sell'
    units: Decimal
    price: Decimal
    gross_amount: Decimal

    @property
    def signed_cash(self) -> Decimal:
        """Effect on cash: sells add, buys subtract."""
        return self.gross_amount if self.side == "sell" else -self.gross_amount


def _price(prices: Prices, instrument_id: uuid.UUID) -> Decimal:
    """A missing price is fatal. Never carry a previous price forward or use zero."""
    price = prices.get(instrument_id)
    if price is None:
        raise PriceUnavailable(detail=f"instrument {instrument_id}")
    if price <= ZERO:
        raise ValidationError(detail=f"non-positive price for instrument {instrument_id}")
    return price


def _check_weights(weights: Weights) -> None:
    if not weights:
        raise ValidationError(detail="portfolio has no target weights")
    if not weights_sum_ok(weights):
        total = sum(weights.values(), ZERO)
        raise ValidationError(detail=f"target weights sum to {total}, expected 100")


def units_for_target(cash: Decimal, weights: Weights, prices: Prices) -> Units:
    """Units bought when `cash` is deployed into `weights` at `prices`."""
    _check_weights(weights)
    return {
        iid: floor_units(cash * weight / ONE_HUNDRED / _price(prices, iid))
        for iid, weight in weights.items()
        if weight > ZERO
    }


def value_holdings(units: Units, prices: Prices) -> Values:
    return {iid: round_money(u * _price(prices, iid)) for iid, u in units.items() if u != ZERO}


def actual_weights(values: Values, cash: Decimal) -> Weights:
    """Percentage of *total* portfolio value, cash included in the denominator.

    Weights therefore sum to 100 only when cash is nil; the shortfall is the cash
    drag, which is exactly what the drift view should show rather than hide.
    """
    total = sum(values.values(), ZERO) + cash
    if total <= ZERO:
        return dict.fromkeys(values, ZERO)
    return {iid: round_weight(v * ONE_HUNDRED / total) for iid, v in values.items()}


def drift(actual: Weights, target: Weights) -> Weights:
    """Signed percentage-point deviation, positive meaning overweight."""
    return {
        iid: round_weight(actual.get(iid, ZERO) - target.get(iid, ZERO))
        for iid in actual.keys() | target.keys()
    }


def _to_trade(instrument_id: uuid.UUID, delta_units: Decimal, price: Decimal) -> Trade | None:
    units = round_units(abs(delta_units))
    if units == ZERO:
        return None
    return Trade(
        instrument_id=instrument_id,
        side="buy" if delta_units > ZERO else "sell",
        units=units,
        price=price,
        gross_amount=round_money(units * price),
    )


def rebalance_trades(
    current_units: Units,
    prices: Prices,
    target_weights: Weights,
    cash: Decimal,
    *,
    band_pct: Decimal = Decimal("0.5"),
    min_notional: Decimal = Decimal("1.00"),
) -> list[Trade]:
    """Trades that move the position from `current_units` back onto target.

    Total portfolio value (holdings + cash) is the base, so cash sitting idle is
    put to work rather than treated as outside the portfolio.

    `band_pct` and `min_notional` are the materiality gate. Without them every
    rebalance of an on-target book still emits 1e-6-unit dust sells (flooring
    `desired` one microunit below the units flooring produced), which write
    audit rows and leave the book immediately "rebalanceable" again. Callers
    pass the configured defaults; this module stays pure and does not read
    settings.
    """
    _check_weights(target_weights)

    universe = current_units.keys() | target_weights.keys()
    priced = {iid: _price(prices, iid) for iid in universe}
    total = sum((u * priced[iid] for iid, u in current_units.items() if u != ZERO), ZERO) + cash
    if total <= ZERO:
        return []

    values = value_holdings(current_units, priced)
    current = actual_weights(values, cash)
    deviations = drift(current, target_weights)
    if not any(abs(points) > band_pct for points in deviations.values()):
        return []

    desired = {
        iid: floor_units(total * target_weights.get(iid, ZERO) / ONE_HUNDRED / priced[iid])
        for iid in universe
    }
    trades = [
        t
        for iid in sorted(universe, key=str)
        if (t := _to_trade(iid, desired[iid] - current_units.get(iid, ZERO), priced[iid]))
    ]
    trades = [t for t in trades if t.gross_amount >= min_notional]
    if not trades:
        return []
    return _fund(trades, cash)


def _fund(trades: list[Trade], cash: Decimal) -> list[Trade]:
    """Guarantee the trade set is self-funding.

    Truncating units keeps buys under budget in exact arithmetic, but rounding
    each gross amount to the cent can still leave a few cents unfunded. Rather
    than let cash go negative, shave the largest buy by the units the shortfall
    represents.
    """
    residual = cash + sum((t.signed_cash for t in trades), ZERO)
    if residual >= ZERO:
        return trades

    largest = max(
        (t for t in trades if t.side == "buy"), key=lambda t: t.gross_amount, default=None
    )
    if largest is None:  # pragma: no cover - sells alone can never overdraw
        raise ValidationError(detail="rebalance overdraws cash with no buy to reduce")

    shaved = _to_trade(
        largest.instrument_id,
        largest.units - floor_units(-residual / largest.price) - Decimal("0.000001"),
        largest.price,
    )
    return [t for t in trades if t is not largest] + ([shaved] if shaved else [])


def apply_trades(current_units: Units, cash: Decimal, trades: list[Trade]) -> tuple[Units, Decimal]:
    """Position after `trades` settle. Cash absorbs the whole net cash effect."""
    units = {iid: u for iid, u in current_units.items() if u != ZERO}
    for t in trades:
        delta = t.units if t.side == "buy" else -t.units
        new = round_units(units.get(t.instrument_id, ZERO) + delta)
        if new < ZERO:
            raise ValidationError(detail=f"sell exceeds holding for {t.instrument_id}")
        units[t.instrument_id] = new
    new_cash = round_money(cash + sum((t.signed_cash for t in trades), ZERO))
    if new_cash < ZERO:
        raise ValidationError(detail="trades overdraw available cash")
    return {iid: u for iid, u in units.items() if u != ZERO}, new_cash


def invest_trades(cash: Decimal, target_weights: Weights, prices: Prices) -> list[Trade]:
    """Opening purchase of a model portfolio with a pre-funded cash balance (A.5)."""
    units = units_for_target(cash, target_weights, prices)
    trades = [t for iid in sorted(units, key=str) if (t := _to_trade(iid, units[iid], prices[iid]))]
    return _fund(trades, cash)
