"""A.2: the profiling conversation collects; the scorer decides.

The graded behaviours are: reuse what A.1 already captured, ask for what is
missing, refuse to score an incoherent profile, and normalise answers so the
same information always produces the same score however it was phrased.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from app.domain.profile.conversation import SLOTS_BY_FIELD, advance, opening
from app.domain.profile.validation import coherence_check
from app.domain.risk.scorer import ScoringInputs, score

D = Decimal

REQUIRED = (
    "monthly_salary",
    "monthly_commitments",
    "dependants",
    "investment_objective",
    "investment_horizon_yrs",
    "loss_tolerance",
    "liquidity_needs",
    "income_stability",
    "investment_experience",
)


@dataclass(frozen=True)
class FakeProfile:
    monthly_salary: Decimal | None = None
    monthly_commitments: Decimal | None = None
    dependants: int | None = None
    investment_objective: str | None = None
    investment_horizon_yrs: int | None = None
    loss_tolerance: str | None = None
    liquidity_needs: str | None = None
    income_stability: str | None = None
    investment_experience: str | None = None

    def missing_fields(self) -> list[str]:
        return [f for f in REQUIRED if getattr(self, f) is None]


COMPLETE = FakeProfile(
    monthly_salary=D("8000"),
    monthly_commitments=D("4500"),
    dependants=1,
    investment_objective="balanced",
    investment_horizon_yrs=8,
    loss_tolerance="medium",
    liquidity_needs="medium",
    income_stability="stable",
    investment_experience="moderate",
)


class TestSlotParsing:
    @pytest.mark.parametrize(
        "field,answer,expected",
        [
            ("monthly_salary", "about $6,500 a month", D("6500")),
            ("monthly_salary", "roughly 8k", D("8000")),
            ("dependants", "two kids", 2),
            ("dependants", "none", 0),
            ("investment_horizon_yrs", "maybe 15 years", 15),
            ("loss_tolerance", "none at all, I can't lose anything", "zero"),
            ("loss_tolerance", "a moderate fall would be fine", "medium"),
            ("loss_tolerance", "I could stomach a large drop", "high"),
            ("liquidity_needs", "unlikely, it's long-term money", "low"),
            ("liquidity_needs", "quite likely actually", "high"),
            ("income_stability", "I'm freelance so it varies", "variable"),
            ("income_stability", "steady salaried job", "stable"),
            ("investment_experience", "none, this is my first time", "none"),
            ("investment_experience", "a fair amount over a few years", "moderate"),
            ("investment_objective", "aggressive growth please", "aggressive_growth"),
            ("investment_objective", "I want to protect my capital", "preservation"),
        ],
    )
    def test_phrasing_normalises_to_a_stable_value(self, field, answer, expected):
        assert SLOTS_BY_FIELD[field].parse(answer) == expected

    def test_longest_keyword_wins(self):
        """'not very experienced' must not match on 'experienced'."""
        assert SLOTS_BY_FIELD["loss_tolerance"].parse("none at all") == "zero"

    def test_an_unparseable_answer_is_re_asked_not_guessed(self):
        profile = FakeProfile()
        turn = advance(profile, awaiting="loss_tolerance", message="hmm, hard to say")
        assert turn.captured == {}
        assert turn.awaiting == "loss_tolerance"
        assert "none at all" in turn.reply


class TestConversationFlow:
    def test_it_asks_for_the_first_missing_field(self):
        turn = opening(FakeProfile())
        assert turn.awaiting == "monthly_salary"
        assert not turn.complete

    def test_it_references_what_a1_already_captured_rather_than_re_asking(self):
        """A.2(2): the stored salary is used, not asked for again."""
        profile = FakeProfile(monthly_salary=D("8000"), dependants=1)
        turn = opening(profile)
        assert "8,000" in turn.reply
        assert turn.awaiting == "monthly_commitments", "must skip what is already on file"

    def test_it_advances_through_every_remaining_slot(self):
        profile = FakeProfile()
        answers = {
            "monthly_salary": "8000",
            "monthly_commitments": "4500",
            "dependants": "1",
            "investment_objective": "balanced",
            "investment_horizon_yrs": "8 years",
            "loss_tolerance": "moderate",
            "liquidity_needs": "possibly",
            "income_stability": "stable",
            "investment_experience": "a fair amount",
        }
        turn = opening(profile)
        captured: dict = {}
        for _ in range(len(REQUIRED)):
            assert turn.awaiting is not None
            turn = advance(
                replace(profile, **captured),
                awaiting=turn.awaiting,
                message=answers[turn.awaiting],
            )
            captured.update(turn.captured)

        assert turn.complete
        assert set(captured) == set(REQUIRED)

    def test_a_complete_coherent_profile_finishes_immediately(self):
        turn = opening(COMPLETE)
        assert turn.complete
        assert turn.awaiting is None


class TestIncoherenceRefusal:
    def test_zero_tolerance_with_aggressive_objective_is_not_scored(self):
        """A.2(4) / Appendix D User 4."""
        profile = replace(COMPLETE, loss_tolerance="zero", investment_objective="aggressive_growth")
        turn = opening(profile)
        assert not turn.complete, "an inconsistent profile must not yield a category"
        assert turn.inconsistencies
        assert turn.inconsistencies[0].code == "zero_tolerance_aggressive_objective"
        assert "?" in turn.reply, "the client should be asked, not told"

    def test_short_horizon_with_growth_objective_is_queried(self):
        profile = replace(COMPLETE, investment_horizon_yrs=1, investment_objective="growth")
        codes = {i.code for i in coherence_check(profile)}
        assert "short_horizon_aggressive_objective" in codes

    def test_commitments_above_salary_is_queried(self):
        profile = replace(COMPLETE, monthly_salary=D("3000"), monthly_commitments=D("4000"))
        codes = {i.code for i in coherence_check(profile)}
        assert "commitments_exceed_salary" in codes

    def test_siti_style_inconsistencies_are_questions_not_a_category(self):
        """A.2(4) / Appendix D User 4: high liquidity + no experience + aggressive."""
        profile = replace(
            COMPLETE,
            investment_objective="aggressive_growth",
            investment_horizon_yrs=0,
            loss_tolerance="high",
            liquidity_needs="high",
            investment_experience="none",
        )
        codes = {i.code for i in coherence_check(profile)}
        assert "short_horizon_aggressive_objective" in codes
        assert "high_liquidity_aggressive_objective" in codes
        assert "inexperienced_aggressive_objective" in codes
        assert not opening(profile).complete

    def test_a_coherent_profile_reports_no_problems(self):
        assert coherence_check(COMPLETE) == []

    def test_resolving_the_conflict_allows_scoring(self):
        conflicted = replace(
            COMPLETE, loss_tolerance="zero", investment_objective="aggressive_growth"
        )
        assert not opening(conflicted).complete
        resolved = replace(conflicted, investment_objective="preservation")
        assert opening(resolved).complete


def test_phrasing_does_not_change_the_score():
    """The point of deterministic slot parsing: wording must not move the number."""
    phrasings = [
        {
            "loss_tolerance": "a moderate fall",
            "liquidity_needs": "possibly",
            "investment_experience": "a fair amount",
        },
        {
            "loss_tolerance": "medium I suppose",
            "liquidity_needs": "maybe sometimes",
            "investment_experience": "moderate, a few years",
        },
    ]
    scores = set()
    for answers in phrasings:
        parsed = {f: SLOTS_BY_FIELD[f].parse(a) for f, a in answers.items()}
        inputs = ScoringInputs(
            monthly_salary=D("8000"),
            monthly_commitments=D("4500"),
            dependants=1,
            investment_objective="balanced",
            investment_horizon_yrs=8,
            income_stability="stable",
            **parsed,
        )
        scores.add(score(inputs).score)
    assert len(scores) == 1
