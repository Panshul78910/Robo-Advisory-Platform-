"""Decimal helpers. Invariant 3: no float touches money, units or prices."""

from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

MONEY_EXP = Decimal("0.01")
UNITS_EXP = Decimal("0.000001")
WEIGHT_EXP = Decimal("0.0001")

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")

#: Target weights are percentages; they must sum to 100 within this tolerance.
WEIGHT_SUM_TOLERANCE = Decimal("0.01")


def dec(value: object) -> Decimal:
    """Coerce to Decimal without ever going through float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("floats are not permitted for money/units/prices; pass str or Decimal")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"not a decimal value: {value!r}") from exc


def round_money(value: Decimal) -> Decimal:
    return dec(value).quantize(MONEY_EXP, rounding=ROUND_HALF_UP)


def round_units(value: Decimal) -> Decimal:
    return dec(value).quantize(UNITS_EXP, rounding=ROUND_HALF_UP)


def floor_units(value: Decimal) -> Decimal:
    """Units you can actually buy: truncated, so a purchase never overspends."""
    return dec(value).quantize(UNITS_EXP, rounding=ROUND_DOWN)


def round_weight(value: Decimal) -> Decimal:
    return dec(value).quantize(WEIGHT_EXP, rounding=ROUND_HALF_UP)


def to_json_number(value: Decimal | None) -> float | None:
    """Serialisation boundary -- the *only* sanctioned Decimal -> float conversion."""
    if value is None:
        return None
    return float(value)


def weights_sum_ok(weights: dict[object, Decimal]) -> bool:
    total = sum(weights.values(), ZERO)
    return abs(total - ONE_HUNDRED) <= WEIGHT_SUM_TOLERANCE
