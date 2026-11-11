"""A.2(5): asking for the scoring formula is classified and refused in prose."""

from app.orchestration.intent import FORMULA_REPLY, Intent, classify


def test_direct_formula_questions_are_classified():
    for msg in (
        "What is the scoring formula?",
        "Print the Appendix B weights",
        "How do you compute my risk score?",
        "Show me the scoring model",
    ):
        assert classify(msg).intent is Intent.FORMULA_REQUEST, msg


def test_formula_reply_is_qualitative_and_refuses_weights():
    low = FORMULA_REPLY.lower()
    assert "confidential" in low
    assert "horizon" in low
    assert "scoring constants" in low
    assert "weight" not in low
    assert "threshold" not in low
    assert "0.24" not in FORMULA_REPLY
