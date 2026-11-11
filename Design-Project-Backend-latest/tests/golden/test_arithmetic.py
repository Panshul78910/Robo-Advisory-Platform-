"""Trade arithmetic verified to the cent against a fixed price table.

These are the numbers a supervisor recomputes by hand for A.5(3) and A.6(2).
"""

from __future__ import annotations

import pathlib
import uuid
from decimal import Decimal

import pytest

from app.core.errors import PriceUnavailable, ValidationError
from app.core.money import ZERO, round_money
from app.domain.trading.arithmetic import (
    actual_weights,
    apply_trades,
    drift,
    invest_trades,
    rebalance_trades,
    units_for_target,
    value_holdings,
)

D = Decimal

# Fixed instrument ids, ordered so `sorted(key=str)` in the module is stable.
EQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
BD = uuid.UUID("22222222-2222-2222-2222-222222222222")
AL = uuid.UUID("33333333-3333-3333-3333-333333333333")

PRICES_D1 = {EQ: D("52.110000"), BD: D("101.250000"), AL: D("18.400000")}
PRICES_D2 = {EQ: D("57.320000"), BD: D("100.100000"), AL: D("17.050000")}

BALANCED = {EQ: D("50"), BD: D("40"), AL: D("10")}
CASH = D("50000.00")


def total_value(units, cash, prices) -> Decimal:
    """Portfolio value without per-line rounding.

    Summing `value_holdings` instead would fold in up to half a cent of display
    rounding per instrument, which masks whether value is actually conserved.
    """
    return cash + sum((u * prices[iid] for iid, u in units.items()), ZERO)


#: A trade settles cash at 2dp while units carry 6dp, so each trade can shift
#: value by up to half a cent. That is settlement rounding, not leakage.
SETTLEMENT_TOLERANCE = Decimal("0.02")


class TestUnitsForTarget:
    def test_units_are_truncated_not_rounded_up(self):
        units = units_for_target(CASH, BALANCED, PRICES_D1)
        # 50000 * 50% = 25000 -> 25000 / 52.11 = 479.754365765...
        assert units[EQ] == D("479.754365")
        # 50000 * 40% = 20000 -> 20000 / 101.25 = 197.530864197...
        assert units[BD] == D("197.530864")
        # 50000 * 10% =  5000 ->  5000 / 18.40  = 271.739130434...
        assert units[AL] == D("271.739130")

    def test_missing_price_raises_rather_than_substituting(self):
        with pytest.raises(PriceUnavailable):
            units_for_target(CASH, BALANCED, {EQ: D("52.11"), BD: D("101.25")})

    def test_weights_must_sum_to_one_hundred(self):
        with pytest.raises(ValidationError):
            units_for_target(CASH, {EQ: D("50"), BD: D("30")}, PRICES_D1)


class TestValuation:
    def test_value_and_weights_to_the_cent(self):
        units = units_for_target(CASH, BALANCED, PRICES_D1)
        values = value_holdings(units, PRICES_D1)
        assert values[EQ] == D("25000.00")
        assert values[BD] == D("20000.00")
        assert values[AL] == D("5000.00")

        weights = actual_weights(values, cash=ZERO)
        assert weights[EQ] == D("50.0000")
        assert weights[BD] == D("40.0000")
        assert weights[AL] == D("10.0000")

    def test_cash_is_inside_the_denominator(self):
        weights = actual_weights({EQ: D("25000.00")}, cash=D("25000.00"))
        assert weights[EQ] == D("50.0000")

    def test_drift_is_signed_percentage_points(self):
        d = drift({EQ: D("53.4000"), BD: D("36.6000")}, BALANCED)
        assert d[EQ] == D("3.4000")
        assert d[BD] == D("-3.4000")
        assert d[AL] == D("-10.0000")  # target present, position absent


class TestInvest:
    def test_invest_conserves_value_and_never_overdraws(self):
        trades = invest_trades(CASH, BALANCED, PRICES_D1)
        assert {t.side for t in trades} == {"buy"}
        units, cash = apply_trades({}, CASH, trades)
        assert cash >= ZERO
        assert abs(total_value(units, cash, PRICES_D1) - CASH) <= SETTLEMENT_TOLERANCE

    def test_gross_amount_is_units_times_price(self):
        trades = invest_trades(CASH, BALANCED, PRICES_D1)
        for t in trades:
            assert t.gross_amount == round_money(t.units * t.price)


class TestRebalance:
    def test_drifted_position_returns_to_target(self):
        """A.6(1): after rebalancing, actual weights match target within tolerance."""
        units = units_for_target(CASH, BALANCED, PRICES_D1)
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))

        # Revalue a day later; the position has drifted.
        before = actual_weights(value_holdings(units, PRICES_D2), cash)
        assert before[EQ] > D("50.5")  # equities rallied

        trades = rebalance_trades(units, PRICES_D2, BALANCED, cash)
        new_units, new_cash = apply_trades(units, cash, trades)
        after = actual_weights(value_holdings(new_units, PRICES_D2), new_cash)

        for iid, target in BALANCED.items():
            assert abs(after[iid] - target) <= D("0.01"), f"{iid} off target"

    def test_value_is_conserved_across_the_rebalance(self):
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))
        before = total_value(units, cash, PRICES_D2)
        trades = rebalance_trades(units, PRICES_D2, BALANCED, cash)
        new_units, new_cash = apply_trades(units, cash, trades)
        after = total_value(new_units, new_cash, PRICES_D2)
        assert abs(after - before) <= SETTLEMENT_TOLERANCE

    def test_cash_never_goes_negative(self):
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))
        _, new_cash = apply_trades(units, cash, rebalance_trades(units, PRICES_D2, BALANCED, cash))
        assert new_cash >= ZERO

    def test_a_different_session_date_produces_different_trades(self):
        """A.6(3)."""
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))
        d2 = rebalance_trades(units, PRICES_D2, BALANCED, cash)
        d1 = rebalance_trades(units, PRICES_D1, BALANCED, cash)
        assert {(t.instrument_id, t.side, t.units) for t in d1} != {
            (t.instrument_id, t.side, t.units) for t in d2
        }

    def test_rebalancing_an_on_target_position_is_a_no_op(self):
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))
        trades = rebalance_trades(units, PRICES_D1, BALANCED, cash)
        assert trades == []

    def test_an_immediate_rerun_after_invest_or_rebalance_is_empty(self):
        """B4: flooring leftover must not emit dust trades at the same prices."""
        invest = invest_trades(CASH, BALANCED, PRICES_D1)
        units, cash = apply_trades({}, CASH, invest)
        assert rebalance_trades(units, PRICES_D1, BALANCED, cash) == []

        moved = rebalance_trades(units, PRICES_D2, BALANCED, cash)
        assert moved, "the later date must be material enough to trade"
        units2, cash2 = apply_trades(units, cash, moved)
        assert rebalance_trades(units2, PRICES_D2, BALANCED, cash2) == []
        assert all(t.gross_amount >= D("1.00") for t in moved)

    def test_sub_band_drift_produces_no_trade(self):
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))
        # ~0.08pp on the equity sleeve: 0.16% price move on a 50% weight.
        nudged = {EQ: PRICES_D1[EQ] * D("1.0016"), BD: PRICES_D1[BD], AL: PRICES_D1[AL]}
        trades = rebalance_trades(units, nudged, BALANCED, cash)
        assert trades == []

    def test_a_three_point_drift_does_trade(self):
        units, cash = apply_trades({}, CASH, invest_trades(CASH, BALANCED, PRICES_D1))
        jumped = {EQ: D("65.00"), BD: PRICES_D1[BD], AL: PRICES_D1[AL]}
        before = actual_weights(value_holdings(units, jumped), cash)
        assert before[EQ] - BALANCED[EQ] >= D("3")
        trades = rebalance_trades(units, jumped, BALANCED, cash)
        assert trades
        assert all(t.gross_amount > ZERO for t in trades)

    def test_selling_more_than_held_is_rejected(self):
        from app.domain.trading.arithmetic import Trade

        bad = Trade(EQ, "sell", D("10"), D("52.11"), D("521.10"))
        with pytest.raises(ValidationError):
            apply_trades({EQ: D("5")}, CASH, [bad])


def test_floats_are_rejected_at_the_boundary():
    with pytest.raises(TypeError):
        value_holdings({EQ: D("1")}, {EQ: 52.11})  # type: ignore[dict-item]


def test_arithmetic_stays_pure():
    src = (
        pathlib.Path(__file__).resolve().parents[2] / "app/domain/trading/arithmetic.py"
    ).read_text(encoding="utf-8")
    assert "get_settings" not in src
    assert "app.core.config" not in src
    assert "datetime" not in src
