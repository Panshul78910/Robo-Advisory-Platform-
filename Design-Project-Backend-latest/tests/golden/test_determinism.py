"""A.2 reproducibility: the same inputs always yield the same score and category.

The conversation may elicit answers in any order; the scorer must not care.
"""

from __future__ import annotations

import random
from dataclasses import asdict
from decimal import Decimal

import pytest

from app.domain.risk.scorer import ScoringInputs, score

D = Decimal

PROFILES = {
    "cautious_retiree": ScoringInputs(
        monthly_salary=D("4200"),
        monthly_commitments=D("3600"),
        dependants=2,
        investment_objective="preservation",
        investment_horizon_yrs=2,
        loss_tolerance="zero",
        liquidity_needs="high",
        income_stability="stable",
        investment_experience="none",
    ),
    "balanced_professional": ScoringInputs(
        monthly_salary=D("8000"),
        monthly_commitments=D("4500"),
        dependants=1,
        investment_objective="balanced",
        investment_horizon_yrs=8,
        loss_tolerance="medium",
        liquidity_needs="medium",
        income_stability="stable",
        investment_experience="moderate",
    ),
    "aggressive_young_earner": ScoringInputs(
        monthly_salary=D("12000"),
        monthly_commitments=D("3000"),
        dependants=0,
        investment_objective="aggressive_growth",
        investment_horizon_yrs=20,
        loss_tolerance="high",
        liquidity_needs="low",
        income_stability="stable",
        investment_experience="extensive",
    ),
    "variable_income_saver": ScoringInputs(
        monthly_salary=D("6000"),
        monthly_commitments=D("5200"),
        dependants=3,
        investment_objective="income",
        investment_horizon_yrs=5,
        loss_tolerance="low",
        liquidity_needs="medium",
        income_stability="variable",
        investment_experience="limited",
    ),
}


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_score_is_identical_under_shuffled_input_ordering(name):
    original = PROFILES[name]
    expected = score(original)

    rng = random.Random(1234)
    for _ in range(50):
        items = list(asdict(original).items())
        rng.shuffle(items)
        again = score(ScoringInputs(**dict(items)))
        assert again.score == expected.score
        assert again.category == expected.category
        assert again.factor_ranking == expected.factor_ranking


def test_categories_are_ordered_by_appetite():
    ordered = [
        score(PROFILES[n]).score
        for n in (
            "cautious_retiree",
            "variable_income_saver",
            "balanced_professional",
            "aggressive_young_earner",
        )
    ]
    assert ordered == sorted(ordered), "a more aggressive profile must not score lower"


def test_extremes_land_in_the_end_categories():
    """Band placement, not exact band -- the placeholder weights are not Appendix B.

    Note the cautious profile is not driven to the floor by score alone: its zero
    loss tolerance is what forces P1, via the A.3 override rather than the score.
    See tests/unit/test_suitability.py.
    """
    assert score(PROFILES["cautious_retiree"]).category in {
        "capital_preservation",
        "conservative",
    }
    assert score(PROFILES["aggressive_young_earner"]).category == "very_aggressive"


def test_score_stays_within_scale():
    for name in PROFILES:
        result = score(PROFILES[name])
        assert D(0) <= result.score <= D(100)


def test_snapshot_is_key_sorted_and_json_safe():
    import json

    snapshot = PROFILES["balanced_professional"].as_snapshot()
    assert list(snapshot) == sorted(snapshot)
    json.dumps(snapshot)


def test_unrecognised_enum_value_is_rejected_not_guessed():
    from app.core.errors import ValidationError

    bad = ScoringInputs(**{**asdict(PROFILES["balanced_professional"]), "loss_tolerance": "vibes"})
    with pytest.raises(ValidationError):
        score(bad)
