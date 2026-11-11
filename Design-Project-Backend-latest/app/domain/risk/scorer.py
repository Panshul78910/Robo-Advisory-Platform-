"""Deterministic risk scoring (A.2).

Pure and side-effect free: the same inputs always yield the same score and
category, whatever order the conversation elicited them in. That reproducibility
is why scoring is Python rather than something the model is asked to do.

The factor *values* live in `weights.py`; this module only combines them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.core.errors import ValidationError
from app.domain.risk import weights as W

SCORE_EXP = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class ScoringInputs:
    """The normalised profile the scorer consumes.

    Decoupled from the natural-language layer that collects it, so phrasing and
    question order cannot influence the result.
    """

    monthly_salary: Decimal
    monthly_commitments: Decimal
    dependants: int
    investment_objective: str
    investment_horizon_yrs: int
    loss_tolerance: str
    liquidity_needs: str
    income_stability: str
    investment_experience: str

    def as_snapshot(self) -> dict:
        """Key-sorted, JSON-safe record of exactly what was scored."""
        return {k: str(v) for k, v in sorted(asdict(self).items())}


@dataclass(frozen=True, slots=True)
class RiskAssessmentResult:
    score: Decimal
    category: str
    formula_version: str
    #: Factor name -> normalised 0..1 contribution, strongest first. Used to
    #: explain the outcome qualitatively (C.3) without exposing any weight.
    factor_ranking: tuple[str, ...]


def _banded(value, bands: Sequence[tuple]) -> Decimal:
    for upper, points in bands:
        if upper is None or value < upper:
            return points
    raise AssertionError("band table has no catch-all")  # pragma: no cover


def _lookup(table: dict[str, Decimal], key: str, field: str) -> Decimal:
    try:
        return table[key]
    except KeyError:
        raise ValidationError(detail=f"unrecognised {field}: {key!r}") from None


def _factor_points(i: ScoringInputs) -> dict[str, Decimal]:
    if i.monthly_salary <= 0:
        raise ValidationError(detail="monthly salary must be positive to score a profile")
    surplus_ratio = (i.monthly_salary - i.monthly_commitments) / i.monthly_salary

    return {
        "loss_tolerance": _lookup(W.LOSS_TOLERANCE_POINTS, i.loss_tolerance, "loss_tolerance"),
        "investment_objective": _lookup(
            W.INVESTMENT_OBJECTIVE_POINTS, i.investment_objective, "investment_objective"
        ),
        "investment_horizon": _banded(i.investment_horizon_yrs, W.HORIZON_BANDS),
        "investment_experience": _lookup(
            W.INVESTMENT_EXPERIENCE_POINTS, i.investment_experience, "investment_experience"
        ),
        "liquidity_needs": _lookup(W.LIQUIDITY_NEEDS_POINTS, i.liquidity_needs, "liquidity_needs"),
        "income_stability": _lookup(
            W.INCOME_STABILITY_POINTS, i.income_stability, "income_stability"
        ),
        "surplus_ratio": _banded(surplus_ratio, W.SURPLUS_RATIO_BANDS),
        "dependants": _banded(i.dependants, W.DEPENDANTS_BANDS),
    }


def score(inputs: ScoringInputs) -> RiskAssessmentResult:
    points = _factor_points(inputs)

    # Iteration follows FACTOR_WEIGHTS' fixed order, never the caller's dict
    # ordering, so accumulation is bit-for-bit repeatable.
    raw = sum(
        (weight * points[factor] for factor, weight in W.FACTOR_WEIGHTS.items()),
        Decimal(0),
    )
    total = (raw * W.SCORE_SCALE).quantize(SCORE_EXP, rounding=ROUND_HALF_UP)

    category = next(c for upper, c in W.CATEGORY_BANDS if upper is None or total < upper)

    # Ranked by contribution, tie-broken by name so the order is stable.
    ranking = tuple(
        factor
        for factor, _ in sorted(
            ((f, W.FACTOR_WEIGHTS[f] * points[f]) for f in W.FACTOR_WEIGHTS),
            key=lambda kv: (-kv[1], kv[0]),
        )
    )
    return RiskAssessmentResult(
        score=total,
        category=category,
        formula_version=W.FORMULA_VERSION,
        factor_ranking=ranking,
    )


def formula_version() -> str:
    return W.FORMULA_VERSION
