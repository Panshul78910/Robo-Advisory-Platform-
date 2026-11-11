"""A displayed figure is rounded; an invented figure is not.

`ungrounded_numbers` and `egress_verify` both used to match a reply's figures
against the record by equality. The weights in the fact bundle are computed per
request -- units times price over total -- so they arrive as full-precision
floats, and the model displays them the way a person would. D-2074 is the
observed cost: asked "Can I invest in SPY?", the assistant produced a correct,
cited, six-paragraph answer saying P4 targets 55% Broad Equity and the client
actually holds 43.31% in equities, and the whole answer was discarded because
43.31 is not 43.307692307692304.

The check now allows half a unit in the last place the reply wrote. These tests
hold both halves of that: every way a true figure is legitimately displayed, and
every way an invented one tries to look like a rounding. The adversarial half is
the point -- the rule is a loosening, and the only evidence a loosening is safe
is the attacks beside it that still fail.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.orchestration import guardrails as g

#: The bundle the pipeline actually builds: weights computed, not stored.
#: 43307.69 / 100000 is 43.307692307692304 when Python divides it.
EQUITY_WEIGHT = 43.307692307692304
FACTS = {
    "totalValue": 100000.0,
    "cash": 1250.44,
    "holdings": [
        {
            "assetClass": "equities",
            "value": 43307.69,
            "actualWeightPct": EQUITY_WEIGHT,
            "targetWeightPct": 55.0,
        },
        {
            "assetClass": "bonds",
            "value": 34692.31,
            "actualWeightPct": 34.69230769230769,
            "targetWeightPct": 35.0,
        },
    ],
}
POLICY = "4 Balanced 55% 35% 10% up to 5% (from SPY sleeve)"
EVIDENCE = [json.dumps(FACTS, default=str), POLICY]


def flagged(reply: str) -> list[str]:
    return g.ungrounded_numbers(reply, EVIDENCE)


class TestTheFigureTheClientOwns:
    """Every precision a model plausibly picks for one true weight."""

    @pytest.mark.parametrize(
        "reply",
        [
            "Your record shows an actual weight of 43.31% in Equities.",  # 2dp
            "Your record shows an actual weight of 43.3% in Equities.",  # 1dp
            "Your record shows an actual weight of 43% in Equities.",  # 0dp
            "Equities sit at 43.308% of the portfolio.",  # 3dp
            "Equities are roughly 43.307692% of the portfolio.",  # 6dp
        ],
    )
    def test_a_displayed_weight_is_not_a_hallucination(self, reply):
        assert flagged(reply) == [], reply

    def test_the_whole_d_2074_sentence_passes(self):
        reply = (
            "Your current portfolio, P4 (Balanced), has a target allocation of 55% to "
            "Broad Equity (SPY). Your record shows you hold a portfolio with an actual "
            "weight of 43.31% in Equities."
        )
        assert flagged(reply) == []

    def test_two_sleeves_at_once(self):
        assert flagged("You hold 43.31% equities and 34.69% bonds.") == []


class TestTheFigureNoRecordSupports:
    """The loosening must not buy an invented figure a way through."""

    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            ("Your equity weight is 61.40%.", ["61.4"]),
            ("Your equity weight is 4.33%.", ["4.33"]),  # right digits, wrong place
            ("Your equity weight is 433.1%.", ["433.1"]),  # right digits, shifted
            ("Your cash balance is $9,999.00.", ["9999"]),
            ("Your portfolio is worth $250,000.", ["250000"]),
        ],
    )
    def test_an_invented_figure_is_still_refused(self, reply, expected):
        assert flagged(reply) == expected, reply

    @pytest.mark.parametrize("claim", ["43.9", "43.4", "42.8", "44"])
    def test_near_misses_outside_the_tolerance_are_refused(self, claim):
        """43.9 is only 0.6 from the truth and is still a different number.
        Tolerance comes from the precision written, not from how close it looks."""
        assert flagged(f"Your equity weight is {claim}%.") == [claim.rstrip("0").rstrip(".")]

    def test_precision_is_claimed_not_granted(self):
        """Writing more decimals narrows the tolerance rather than widening it:
        a figure cannot buy slack by looking precise."""
        assert flagged("Your equity weight is 43.3077%.") == []  # true to 4dp
        assert flagged("Your equity weight is 43.3177%.") == ["43.3177"]


class TestTheRoundNumberHole:
    """`Decimal.normalize` puts a round number in exponent form, so "half a unit
    in the last place" taken literally would make $1,000 match anything from 500
    to 1500 -- and a round number is what an invented figure looks like."""

    @pytest.mark.parametrize(
        ("written", "tolerance"),
        [("43.31", "0.005"), ("43.3", "0.05"), ("43", "0.5"), ("1000", "0.5"), ("250000", "0.5")],
    )
    def test_the_tolerance_never_exceeds_half_a_unit(self, written, tolerance):
        assert g._rounding_tolerance(Decimal(written).normalize()) == Decimal(tolerance)

    def test_a_round_invented_balance_cannot_borrow_a_nearby_figure(self):
        facts = {"cash": 1250.44, "totalValue": 51905.51}
        evidence = [json.dumps(facts)]
        # 51905.51 is within 500 of 52000, which the naive rule would have allowed.
        assert g.ungrounded_numbers("Your total is $52,000.", evidence) == ["52000"]
        assert g.ungrounded_numbers("Your total is $51,906.", evidence) == []


class TestTheMoneyCheckAgrees:
    """`egress_verify` is the stricter of the two and ran a different rule. A
    balance correct to the cent passed there and a balance correct to the dollar
    did not, for no reason either check could state."""

    def _verify(self, text: str):
        return g.egress_verify(
            text=text, intent="holdings_query", allowed_chunk_refs=frozenset(), facts=FACTS
        )

    @pytest.mark.parametrize(
        "reply",
        [
            "Your cash balance is $1,250.44.",
            "Your cash balance is $1,250.4.",
            "Your cash balance is $1,250.",
            "Your total value is $100,000.00.",
        ],
    )
    def test_a_true_balance_passes_at_any_sensible_precision(self, reply):
        assert not self._verify(reply).blocked, reply

    @pytest.mark.parametrize(
        "reply",
        [
            "Your cash balance is $9,999.00.",
            "Your cash balance is $1,350.44.",
            "Your total value is $120,000.00.",
        ],
    )
    def test_an_invented_balance_is_still_blocked(self, reply):
        v = self._verify(reply)
        assert v.blocked and v.triggered == ("verify_figure",), reply

    def test_both_checks_now_answer_alike(self):
        """The two paths disagreeing is what made the behaviour unexplainable."""
        for reply in ("Your cash balance is $1,250.44.", "Your cash balance is $1,250."):
            assert not self._verify(reply).blocked
            assert g.ungrounded_numbers(reply, EVIDENCE) == []
        for reply in ("Your cash balance is $9,999.00.",):
            assert self._verify(reply).blocked
            assert g.ungrounded_numbers(reply, EVIDENCE) != []


class TestAdversarialRounding:
    """Ways a fabricated figure could try to dress itself as a displayed one."""

    def test_an_illustration_marker_still_cannot_launder_a_claim(self):
        """The hedge exemption and the rounding tolerance are separate doors;
        neither opens the other."""
        assert flagged("Your actual equity weight is 61.4%.") == ["61.4"]
        assert flagged("For example, a portfolio might hold 61.4% equities.") == []

    def test_a_figure_cannot_borrow_tolerance_from_an_unrelated_number(self):
        """34.69 is a real bond weight. It does not license a 34.69% cash claim
        -- grounding is numeric, so this is a limit worth stating, not hiding."""
        assert flagged("Your cash is 34.69% of the portfolio.") == []

    @pytest.mark.parametrize("pad", ["0043.31", "043.31"])
    def test_leading_zeros_claim_nothing(self, pad):
        assert flagged(f"Your equity weight is {pad}%.") == []

    @pytest.mark.parametrize(
        ("written", "verdict"),
        [
            ("43.31", []),  # true to the hundredth
            ("43.310", ["43.31"]),  # claims a thousandth it does not have
            ("43.3100", ["43.31"]),  # claims four places; the truth is 43.3077
            ("43.00", ["43"]),  # claims the sleeve is 43 to the cent; it is not
            ("43", []),  # claims only the unit, and is right about it
        ],
    )
    def test_trailing_zeros_are_a_claim_about_precision(self, written, verdict):
        """A figure is checked at the precision it asserts. Padding a number with
        zeros asserts more precision, so it is held to more -- the tolerance is
        earned by rounding honestly, not bought by writing extra digits."""
        assert flagged(f"Your equity weight is {written}%.") == verdict

    @pytest.mark.parametrize("claim", ["55.4", "55.5", "54.6"])
    def test_a_policy_figure_cannot_drift(self, claim):
        """55% comes from the policy table. One decimal place of drift is a
        different cap, and the client would act on it."""
        assert flagged(f"P4 targets {claim}% Broad Equity.") == [claim]

    def test_the_skip_window_is_unchanged(self):
        """Integers at or below 20, and years, were never checked. The tolerance
        must not quietly extend that window upward."""
        assert flagged("Rule E-1 caps it at 5% for 2026.") == []
        assert flagged("The cap is 21%.") == ["21"]


class TestTheExistingContractHolds:
    """The cases the check already got right, re-asserted against the new rule."""

    EV = ['{"cash": 1250.44, "totalValue": 51905.51}', "capped at 20%", "see paragraph 27"]

    @pytest.mark.parametrize(
        "reply",
        [
            "See paragraph 27 and section 3.2 of the guidelines.",
            "Outcome 5 of 5 is set out in paragraph 114.",
            "Satellite exposure is capped at 20%",
            "You have $1,250.44 in cash, total 51,905.51.",
        ],
    )
    def test_references_and_grounded_figures_pass(self, reply):
        assert g.ungrounded_numbers(reply, self.EV) == [], reply

    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            ("You have $9,999.00 in cash.", ["9999"]),
            ("Your allocation is 35.5%", ["35.5"]),
            ("Your weight is 35.5 percent", ["35.5"]),
        ],
    )
    def test_invented_money_and_weights_are_still_caught(self, reply, expected):
        assert g.ungrounded_numbers(reply, self.EV) == expected, reply
