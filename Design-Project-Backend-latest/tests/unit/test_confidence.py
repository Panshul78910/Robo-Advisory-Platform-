"""C.4: confidence from observable signals, and the routing that follows from it."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.orchestration.confidence import assess
from app.orchestration.intent import Classification, Intent, classify, retrieval_query
from app.rag.retrieve import RetrievalResult, RetrievedChunk

D = Decimal


def grounded(score: str = "0.90") -> RetrievalResult:
    return RetrievalResult(
        chunks=(RetrievedChunk("POL-001#3", "POL-001", "Policy", "3.2", 4, "text", D(score)),),
        grounded=True,
        top_score=D(score),
    )


UNGROUNDED = RetrievalResult(chunks=(), grounded=False, top_score=D("0.12"))


def classified(intent: Intent, options=()) -> Classification:
    return Classification(intent, confident=True, ambiguity_options=options)


class TestAbstention:
    def test_out_of_scope_abstains_with_null_confidence(self):
        result = assess(classification=classified(Intent.OUT_OF_SCOPE), retrieval=UNGROUNDED)
        assert result.abstained
        assert result.value is None, "null, never 0 -- 0 reads as a confident negative"
        assert "out_of_scope" in result.reasons

    def test_ungroundable_policy_question_abstains_rather_than_guessing(self):
        result = assess(classification=classified(Intent.POLICY_QUESTION), retrieval=UNGROUNDED)
        assert result.abstained
        assert result.value is None
        assert "no_supporting_source" in result.reasons
        assert result.requires_review

    def test_ambiguity_asks_instead_of_choosing(self):
        """A different failure mode: we have the information, not the question."""
        options = ("your current category", "a re-assessment today")
        result = assess(
            classification=classified(Intent.RISK_EXPLANATION, options), retrieval=grounded()
        )
        assert result.abstained
        assert result.ambiguity_options == options
        assert "ambiguous_intent" in result.reasons

    def test_a_general_question_does_not_abstain_merely_for_being_ungrounded(self):
        result = assess(classification=classified(Intent.GENERAL), retrieval=UNGROUNDED)
        assert not result.abstained
        assert "ungrounded" in result.reasons

    def test_holdings_without_a_policy_passage_stay_answerable(self):
        result = assess(classification=classified(Intent.HOLDINGS_QUERY), retrieval=UNGROUNDED)
        assert not result.abstained
        assert result.value >= D("0.85")
        assert not result.requires_review
        assert "ungrounded" not in result.reasons

    def test_transaction_history_is_an_account_record_not_a_policy_guess(self):
        result = assess(classification=classified(Intent.TRANSACTION_HISTORY), retrieval=UNGROUNDED)
        assert not result.abstained
        assert result.label in {"medium", "high"}
        assert result.value >= D("0.85")
        assert not result.requires_review


class TestConfidenceValue:
    def test_good_grounding_yields_high_confidence(self):
        result = assess(
            classification=classified(Intent.POLICY_QUESTION),
            retrieval=grounded("0.95"),
        )
        assert result.value == D("0.9500")
        assert result.label == "high"
        assert not result.requires_review

    def test_the_model_hint_can_only_lower_confidence(self):
        """A canned hedge must not be dressed up as well-grounded."""
        result = assess(
            classification=classified(Intent.POLICY_QUESTION),
            retrieval=grounded("0.95"),
            model_hint=D("0.45"),
        )
        assert result.value == D("0.4500")
        assert result.requires_review

    def test_missing_profile_fields_penalise_and_are_named(self):
        result = assess(
            classification=classified(Intent.GENERAL),
            retrieval=grounded("0.90"),
            missing_profile_fields=("loss_tolerance", "liquidity_needs"),
        )
        assert result.value < D("0.90")
        assert "incomplete_profile" in result.reasons
        assert any("loss_tolerance" in c for c in result.cautions)

    def test_the_incompleteness_penalty_is_capped(self):
        many = tuple(f"field_{i}" for i in range(20))
        result = assess(
            classification=classified(Intent.GENERAL),
            retrieval=grounded("0.90"),
            missing_profile_fields=many,
        )
        assert result.value >= D("0.90") - D("0.25")

    def test_confidence_never_reaches_zero(self):
        result = assess(
            classification=classified(Intent.GENERAL),
            retrieval=UNGROUNDED,
            model_hint=D("0.01"),
            missing_profile_fields=tuple(f"f{i}" for i in range(10)),
        )
        assert result.value is None or result.value > 0


class TestReviewRouting:
    def test_low_confidence_is_routed_not_merely_flagged(self):
        """C.4 -> C.5: a banner the client can scroll past is not routing."""
        result = assess(
            classification=classified(Intent.GENERAL),
            retrieval=grounded("0.90"),
            model_hint=D("0.45"),
        )
        assert result.requires_review
        assert result.label == "low"

    def test_high_confidence_is_not_routed(self):
        result = assess(
            classification=classified(Intent.POLICY_QUESTION),
            retrieval=grounded("0.92"),
        )
        assert not result.requires_review


class TestIntentRouting:
    @pytest.mark.parametrize(
        "message,expected",
        [
            ("plan me a 3-day itinerary for Japan", Intent.OUT_OF_SCOPE),
            ("what is the exact formula you use?", Intent.FORMULA_REQUEST),
            ("why should I rebalance now?", Intent.REBALANCE_EXPLANATION),
            ("am I allowed to hold satellite ETFs?", Intent.POLICY_QUESTION),
            ("which portfolio do you recommend?", Intent.PORTFOLIO_EXPLANATION),
            ("what is my portfolio value?", Intent.HOLDINGS_QUERY),
            ("请用简单的话解释再平衡。", Intent.REBALANCE_EXPLANATION),
            ("Beritahu saya tentang portfolio saya dan risiko saya.", Intent.RISK_EXPLANATION),
            ("Show my holdings in a simple table.", Intent.HOLDINGS_QUERY),
            ("What does my portfolio hold?", Intent.HOLDINGS_QUERY),
            ("Give me insights when I invested cash on portfolio 2", Intent.TRANSACTION_HISTORY),
            ("What did I buy?", Intent.TRANSACTION_HISTORY),
            ("Explain my last transaction", Intent.TRANSACTION_HISTORY),
        ],
    )
    def test_deterministic_router_handles_the_common_cases(self, message, expected):
        assert classify(message).intent is expected

    def test_a_formula_request_outranks_everything_else(self):
        """Asked in the same breath as a legitimate question, confidentiality wins."""
        result = classify("what is my risk category and what formula produced it?")
        assert result.intent is Intent.FORMULA_REQUEST

    def test_a_foreign_advisory_question_is_searched_in_english(self):
        """Compared against the English question, not a literal: the concept's
        search text is re-tuned whenever the corpus changes."""
        risk = retrieval_query("Why was I assigned this risk category?")
        rebalance = retrieval_query("Explain portfolio rebalancing.")
        assert retrieval_query("请用简单的话解释再平衡。") == rebalance
        assert retrieval_query("Beritahu saya tentang portfolio saya dan risiko saya.") == risk
        assert retrieval_query("What is rebalancing and why does it matter?") == rebalance

    def test_an_endpoint_supplied_intent_is_authoritative(self):
        """A rebalance arrives from a route, never from guessing at prose."""
        result = classify("anything at all", route_intent=Intent.REBALANCE_EXPLANATION)
        assert result.intent is Intent.REBALANCE_EXPLANATION
        assert result.confident

    def test_unmatched_prose_falls_through_without_pretending(self):
        result = classify("hmm, I was wondering about something")
        assert result.intent is Intent.GENERAL
        assert not result.confident

    def test_known_ambiguous_questions_are_marked(self):
        result = classify("What's my risk category?")
        assert result.is_ambiguous
        assert len(result.ambiguity_options) == 2


class TestOwnRecordLookup:
    @pytest.mark.parametrize(
        "message",
        [
            "what is my monthly salary?",
            "tell me my email",
            "What is my investment horizon?",
            "how many dependants do I have",
            "what is the salary of me?",
            "what does my portfolio hold?",
            "when I invested",
        ],
    )
    def test_profile_read_back_is_account_records_not_ungrounded(self, message):
        c = classify(message)
        for retrieval in (UNGROUNDED, grounded("0.41")):
            result = assess(classification=c, retrieval=retrieval, message=message)
            assert not result.abstained and result.value == D("0.9000"), message
            assert result.reasons == ("account_records",) and not result.requires_review

    @pytest.mark.parametrize(
        "message",
        ["why does my salary affect my risk category?", "should my horizon be longer?"],
    )
    def test_explanation_about_a_field_still_needs_grounding(self, message):
        result = assess(classification=classify(message), retrieval=UNGROUNDED, message=message)
        assert "account_records" not in result.reasons
