"""Invariant 3: money, units and prices are Decimal, rounded deliberately."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.money import (
    dec,
    floor_units,
    round_money,
    round_units,
    round_weight,
    to_json_number,
    weights_sum_ok,
)

D = Decimal


class TestRounding:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.005", "1.01"),  # half-up, not banker's
            ("1.004", "1.00"),
            ("2.675", "2.68"),  # the classic float trap: 2.675 -> 2.67 in binary
            ("-1.005", "-1.01"),
            ("0", "0.00"),
        ],
    )
    def test_money_rounds_half_up_to_two_places(self, raw, expected):
        assert round_money(D(raw)) == D(expected)

    def test_units_keep_six_places(self):
        assert round_units(D("479.7543657")) == D("479.754366")

    def test_floor_units_never_rounds_up(self):
        """You cannot buy units you cannot fund."""
        assert floor_units(D("479.7543999")) == D("479.754399")
        assert floor_units(D("1.9999999")) == D("1.999999")

    def test_weights_keep_four_places(self):
        assert round_weight(D("53.44449")) == D("53.4445")


class TestFloatRejection:
    def test_dec_rejects_floats_outright(self):
        with pytest.raises(TypeError, match="floats are not permitted"):
            dec(52.11)

    def test_dec_accepts_strings_and_ints(self):
        assert dec("52.11") == D("52.11")
        assert dec(3) == D("3")

    def test_to_json_number_is_the_only_sanctioned_conversion(self):
        assert to_json_number(D("52.11")) == 52.11
        assert to_json_number(None) is None


class TestWeightSum:
    def test_exact_hundred_passes(self):
        assert weights_sum_ok({"a": D("50"), "b": D("50")})

    def test_within_tolerance_passes(self):
        assert weights_sum_ok({"a": D("50"), "b": D("49.995")})

    def test_outside_tolerance_fails(self):
        assert not weights_sum_ok({"a": D("50"), "b": D("30")})
        assert not weights_sum_ok({"a": D("50"), "b": D("50.5")})
