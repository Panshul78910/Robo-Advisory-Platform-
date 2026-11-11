"""Two ways to misstate eligibility that the code-matching check stepped over.

Observed, with a client eligible for P1-P6 and holding P4:

    "you are eligible for P4 and any portfolios above it"
    "you are *not* eligible for portfolios below your category"

The first names only P4, which is eligible, so a check reading portfolio codes
saw nothing wrong -- while the sentence the client reads grants P7 and P8. The
second is a denial, and denials were skipped wholesale so that "you are not
eligible for P7" would stay sayable.

Both invert Appendix E section 2, which caps eligibility at the client's
category and says in as many words that "a client may always choose a portfolio
below their category". Both were caught only by the faithfulness judge, which
is a hosted call that frequently does not complete -- so in practice they
reached the client.

What these tests pin down is the line between the two: the direction a claim
points, not whether it is negated.
"""

from __future__ import annotations

import pytest

from app.orchestration import guardrails as g

ELIGIBLE_P1_TO_P6 = {"eligiblePortfolios": ["P1", "P2", "P3", "P4", "P5", "P6"]}
REFS: frozenset[str] = frozenset()


def verdict(text: str, facts: dict | None = None):
    return g.egress_verify(
        text=text,
        intent="risk_explanation",
        allowed_chunk_refs=REFS,
        facts=ELIGIBLE_P1_TO_P6 if facts is None else facts,
    )


class TestReachingUpwardsWithoutNamingACode:
    @pytest.mark.parametrize(
        "sentence",
        [
            "You are eligible for P4 and any portfolios above it.",
            "You are eligible for P6 or higher.",
            "You qualify for P5 and anything above that.",
            "You are permitted to hold P4 and above.",
        ],
    )
    def test_an_open_ended_reach_is_a_claim_about_the_codes_it_covers(self, sentence):
        assert verdict(sentence).blocked

    @pytest.mark.parametrize(
        "sentence",
        [
            "You are eligible for P1 through P6.",
            "You are eligible for portfolios P1 to P6.",
            "You are eligible for P1, P2, P3, P4, P5 and P6.",
            "You are eligible for up to P6.",
        ],
    )
    def test_a_bounded_range_names_its_own_ceiling_and_passes(self, sentence):
        assert not verdict(sentence).blocked

    def test_the_open_end_is_only_a_problem_when_it_overshoots(self):
        """Reaching up from the cap itself still reaches past it."""
        assert verdict("You are eligible for P6 and above.").blocked

    def test_a_client_eligible_for_everything_can_be_told_so(self):
        facts = {"eligiblePortfolios": [f"P{n}" for n in range(1, 10)]}
        assert not verdict("You are eligible for P4 and any portfolio above it.", facts).blocked


class TestDenyingTheLowerPortfolios:
    @pytest.mark.parametrize(
        "sentence",
        [
            "You are not eligible for portfolios below your category.",
            "You cannot hold anything beneath your assessed category.",
            "You are not permitted to hold portfolios lower than P4.",
            "You aren't eligible for the portfolios below this one.",
        ],
    )
    def test_a_downward_denial_is_blocked(self, sentence):
        assert verdict(sentence).blocked

    @pytest.mark.parametrize(
        "sentence",
        [
            "You are not eligible for P7.",
            "You are not eligible for portfolios above your category.",
            "You cannot hold P7 or P8; your category caps you at P6.",
            "To be eligible for P8 you would need a higher risk category.",
            "You are not eligible for anything higher than P6.",
        ],
    )
    def test_an_upward_denial_is_true_and_must_stay_sayable(self, sentence):
        assert not verdict(sentence).blocked

    def test_the_policys_own_sentence_passes(self):
        assert not verdict(
            "You may always choose a portfolio below your category."
        ).blocked

    def test_vacuous_for_a_client_with_nothing_below_them(self):
        """A P1 client has no lower portfolio, so the sentence is not a false
        claim about their record -- just a pointless one."""
        facts = {"eligiblePortfolios": ["P1"]}
        assert not verdict(
            "You are not eligible for portfolios below your category.", facts
        ).blocked


class TestTheOriginalCheckIsIntact:
    def test_a_code_outside_the_list_is_still_blocked(self):
        assert verdict("You are also eligible for P7 because you passed the CKA.").blocked

    def test_the_eligible_codes_still_pass(self):
        assert not verdict("You are eligible for P1, P2, P3 and P4.").blocked

    def test_no_facts_means_no_eligibility_check(self):
        assert not g.egress_verify(
            text="You are eligible for P9 and above.",
            intent="risk_explanation",
            allowed_chunk_refs=REFS,
            facts={},
        ).blocked

    def test_the_real_answer_that_started_this(self):
        """Verbatim from decision D-2163, which only the judge caught."""
        reply = (
            "According to the Company Suitability Policy (Appendix E, Section 2), "
            "a client assessed at risk category N is eligible for portfolios 1 "
            "through N.\n"
            "* You are currently held in Portfolio P4.\n"
            "* Because your risk category is \"Growth,\" you are eligible for P4 "
            "and any portfolios above it (up to the maximum allowed for your "
            "category). You are not eligible for portfolios below your category "
            "if they were to be considered \"lower risk\" in a way that restricts "
            "you, but the policy states you may always choose a portfolio below "
            "your category."
        )
        assert verdict(reply).blocked
