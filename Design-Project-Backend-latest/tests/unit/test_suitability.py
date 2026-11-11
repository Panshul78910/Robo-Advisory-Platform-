"""A.3: category caps, each override, CKA gating, satellite caps, refusal."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from app.domain.suitability.engine import check_satellite_exposure, evaluate
from app.domain.suitability.rules import load_policy

D = Decimal


@dataclass(frozen=True)
class FakeProfile:
    loss_tolerance: str = "high"
    investment_horizon_yrs: int = 15
    liquidity_needs: str = "low"
    income_stability: str = "stable"
    cka_passed: bool = True


CLEAN = FakeProfile()


class TestCategoryCap:
    def test_cap_follows_the_eligibility_matrix(self):
        result = evaluate(profile=CLEAN, risk_category="balanced")
        assert result.max_portfolio == "P4"
        assert result.eligible_portfolios == ("P1", "P2", "P3", "P4")
        assert result.capped_by is None  # the category itself bound, no override

    def test_top_category_reaches_the_top_portfolio(self):
        assert evaluate(profile=CLEAN, risk_category="very_aggressive").max_portfolio == "P8"

    def test_policy_version_travels_with_the_result(self):
        result = evaluate(profile=CLEAN, risk_category="growth")
        assert result.policy_version == load_policy().version


class TestOverrides:
    @pytest.mark.parametrize(
        "change,expected_cap,expected_code",
        [
            ({"loss_tolerance": "zero"}, "P1", "zero_loss_tolerance"),
            ({"investment_horizon_yrs": 2}, "P2", "short_horizon"),
            ({"liquidity_needs": "high"}, "P3", "high_liquidity_needs"),
            ({"income_stability": "unstable"}, "P4", "unstable_income"),
            ({"loss_tolerance": "low"}, "P3", "loss_up_to_10pct"),
        ],
    )
    def test_each_override_caps_a_high_scorer(self, change, expected_cap, expected_code):
        """A.3(1): the override binds even though the raw score allows more."""
        result = evaluate(profile=replace(CLEAN, **change), risk_category="very_aggressive")
        assert result.max_portfolio == expected_cap
        assert result.capped_by is not None
        assert result.capped_by.code == expected_code
        assert expected_code in {r.code for r in result.fired_rules}

    def test_most_restrictive_override_wins(self):
        profile = replace(CLEAN, loss_tolerance="zero", liquidity_needs="high")
        result = evaluate(profile=profile, risk_category="very_aggressive")
        assert result.max_portfolio == "P1"
        assert result.capped_by.code == "zero_loss_tolerance"
        # Both still recorded, so the audit log shows every rule that fired.
        codes = {r.code for r in result.fired_rules}
        assert {"zero_loss_tolerance", "high_liquidity_needs"} <= codes

    def test_an_override_never_raises_a_low_category(self):
        result = evaluate(
            profile=replace(CLEAN, income_stability="unstable"), risk_category="conservative"
        )
        assert result.max_portfolio == "P2"

    def test_refusal_explanation_names_the_binding_rule(self):
        result = evaluate(
            profile=replace(CLEAN, loss_tolerance="zero"), risk_category="very_aggressive"
        )
        reason = result.refusal_reason("P7")
        assert "P7" in reason and "P1" in reason
        assert "not prepared to accept any loss" in reason


class TestCkaGate:
    def test_without_cka_the_client_is_held_at_the_non_cka_cap(self):
        """A.3(2).

        Appendix E section 4 gates portfolios 5-8 behind the knowledge check,
        and rule E-4 caps a failed check at "4 - Balanced". The placeholder
        policy gated P6-P8 and capped at P5, which let an unassessed client
        reach P5.
        """
        result = evaluate(profile=replace(CLEAN, cka_passed=False), risk_category="very_aggressive")
        assert result.max_portfolio == "P4"
        assert not result.permits("P5")
        assert result.capped_by.code == "cka_not_passed"

    def test_passing_cka_unlocks_the_higher_portfolios(self):
        assert evaluate(profile=CLEAN, risk_category="very_aggressive").permits("P8")

    def test_without_cka_satellite_exposure_is_zero(self):
        result = evaluate(profile=replace(CLEAN, cka_passed=False), risk_category="growth")
        assert result.satellite_cap_pct == D(0)


class TestSatelliteCaps:
    def test_cap_is_per_risk_category(self):
        """The Satellite cap column of Appendix E section 1, category by category."""
        for category, cap in [
            ("capital_preservation", 0),
            ("conservative", 0),
            ("moderately_conservative", 5),
            ("balanced", 5),
            ("moderately_aggressive", 5),
            ("growth", 10),
            ("aggressive", 10),
            ("very_aggressive", 15),
        ]:
            actual = evaluate(profile=CLEAN, risk_category=category).satellite_cap_pct
            assert actual == D(cap), f"{category} cap is {actual}%, Appendix E says {cap}%"

    def test_an_unknown_category_permits_no_satellite(self):
        """Absent means zero, so a category added to the enum but not to the
        policy cannot silently inherit someone else's ceiling.

        Asked of the policy directly: `evaluate` rejects an unknown category at
        the eligibility ladder, well before the satellite cap is read.
        """
        assert load_policy().satellite_cap("not_a_category") == D(0)

    def test_exposure_within_the_cap_passes(self):
        sat, core = uuid.uuid4(), uuid.uuid4()
        result = evaluate(profile=CLEAN, risk_category="growth")
        assert check_satellite_exposure(result, {sat: D(10), core: D(90)}, {sat}) is None

    def test_exposure_above_the_cap_is_refused_with_the_numbers(self):
        sat, core = uuid.uuid4(), uuid.uuid4()
        result = evaluate(profile=CLEAN, risk_category="growth")
        reason = check_satellite_exposure(result, {sat: D(25), core: D(75)}, {sat})
        assert reason is not None and "25" in reason and "10" in reason

    def test_any_satellite_without_cka_names_the_assessment(self):
        sat, core = uuid.uuid4(), uuid.uuid4()
        result = evaluate(profile=replace(CLEAN, cka_passed=False), risk_category="growth")
        reason = check_satellite_exposure(result, {sat: D(1), core: D(99)}, {sat})
        assert reason is not None and "Knowledge Assessment" in reason


class TestPolicyIsData:
    def test_conditions_cannot_execute_code(self):
        from app.domain.suitability.rules import COMPARATORS, Condition

        assert set(COMPARATORS) == {"eq", "ne", "lt", "lte", "gt", "gte", "in"}
        # An unknown operator raises rather than falling through to eval.
        with pytest.raises(KeyError):
            Condition("loss_tolerance", "__import__", "os").matches(CLEAN)
