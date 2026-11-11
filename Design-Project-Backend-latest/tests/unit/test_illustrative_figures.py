"""A worked example is not a claim about the account.

Asked "what is rebalancing and why does it matter?", the model explained it the
way any adviser would -- "if your portfolio is designed to be 60% equities and
40% bonds... it might shift to 70%/30%" -- and the whole answer was refused with
"I couldn't verify one of the figures against your records. Your dashboard shows
exact values." Four invented figures, every one of them hypothetical, and a
refusal about account records for a question that asked about none.

An assistant that cannot say "for example" cannot teach. But the exemption has
to be narrow, or it becomes the way to launder a fabricated balance, so these
tests are mostly about what it still catches.
"""

from __future__ import annotations

import pytest

from app.orchestration.guardrails import egress_verify, ungrounded_numbers

EVIDENCE = [
    '{"cash": 12000.0, "riskCategory": "growth"}',
    "What is rebalancing and why does it matter?",
    "Rebalancing brings the portfolio back to the recommended asset allocation.",
]


class TestTheObservedFalseRefusal:
    def test_the_rebalancing_example_is_allowed(self):
        reply = (
            "For example, if your portfolio is designed to be 60% equities and 40% bonds, "
            "and equities perform well, your portfolio might shift to 70% equities and "
            "30% bonds."
        )
        assert ungrounded_numbers(reply, EVIDENCE) == []

    @pytest.mark.parametrize(
        "lead",
        [
            "For example,",
            "For instance,",
            "Suppose",
            "Imagine",
            "Hypothetically,",
            "To illustrate,",
            "As an example,",
        ],
    )
    def test_any_declared_illustration_is_allowed(self, lead):
        reply = f"{lead} a portfolio holding 65% equities drifts to 75%."
        assert ungrounded_numbers(reply, EVIDENCE) == []


class TestItStillCatchesClaims:
    def test_a_bare_allocation_claim_is_still_flagged(self):
        """No marker, so it reads as a statement about this client."""
        assert ungrounded_numbers("Your portfolio holds 73% equities.", EVIDENCE) == ["73"]

    def test_the_exemption_does_not_spill_into_the_next_sentence(self):
        """The marker must not work as a blanket prefix for a whole answer."""
        reply = (
            "For example, a portfolio might hold 60% equities. "
            "Your own portfolio holds 73% equities."
        )
        assert ungrounded_numbers(reply, EVIDENCE) == ["73"]

    def test_the_exemption_does_not_spill_across_bullets(self):
        reply = (
            "- For example, a balanced mix is 60% equities.\n"
            "- Your current allocation is 88% equities."
        )
        assert ungrounded_numbers(reply, EVIDENCE) == ["88"]

    def test_a_bare_conditional_is_not_an_illustration(self):
        """A bare "if" is not a declaration of example -- otherwise any invented
        figure could be hedged into safety."""
        reply = "If you held more equities, you would be at 91%."
        assert ungrounded_numbers(reply, EVIDENCE) == ["91"]

    def test_an_invented_account_figure_is_caught_even_when_hedged(self):
        """The money check reads the fact bundle and does not care about hedging,
        so the illustration exemption cannot be used to state a balance."""
        verdict = egress_verify(
            text="For example, your cash balance is $99,999.00.",
            intent="",
            allowed_chunk_refs=frozenset(),
            facts={"cash": 12000.0},
        )
        assert verdict.blocked
        assert verdict.triggered == ("verify_figure",)


class TestUnchangedBehaviour:
    def test_a_grounded_figure_passes(self):
        assert ungrounded_numbers("Your cash is 12000.", EVIDENCE) == []

    def test_small_numbers_and_years_are_still_skipped(self):
        assert ungrounded_numbers("Rule E-1 caps it at 2 for 2026.", EVIDENCE) == []
