"""A client asking to take more or less risk must reach the suitability policy.

The concept needles were all noun phrases lifted from the policy's own wording
-- "risk category", "risk profile", "my risk". Clients do not ask that way. They
ask whether they can "invest more aggressively" or whether they should "be more
conservative", and none of those matched, so the whole colloquial sentence went
to the embedder.

Measured against the live corpus, that put five of thirteen ordinary questions
under the 0.34 relevance floor -- "should I be more conservative?" scored 0.12.
Nothing was retrieved, so the answer had no evidence to stand on and egress
refused it as unverifiable. The client saw a message about dashboard figures for
a question that asked for no figure.

These tests are on the routing, which is deterministic and needs no database.
The scores themselves are a property of the corpus and are checked by hand.
"""

from __future__ import annotations

import pytest

from app.orchestration.intent import Intent, classify, retrieval_query

#: The exact phrasings measured below the floor, plus the near neighbours a
#: client reaches for next.
RISK_APPETITE = [
    "Given my financial state, is it okay to invest more aggressively?",
    "Can I invest more aggressively?",
    "Should I be more aggressive with my investments?",
    "Is it time to be more aggressive?",
    "Should I be more conservative?",
    "Would a more conservative approach suit me better?",
    "Am I taking too much risk?",
    "Can I take on more risk?",
    "Can I go for something riskier?",
    "I want a safer portfolio, is that possible?",
    "Should I move to a higher risk portfolio?",
    "Is my portfolio too aggressive for someone like me?",
]


#: What the risk concept currently searches for. Derived, never hardcoded: the
#: text is re-tuned whenever the corpus changes -- replacing the placeholder
#: suitability document with Appendix E moved it from "risk category" to
#: "eligibility matrix risk category portfolios" -- and a literal here failed
#: eleven tests for a routing behaviour that had not changed at all.
RISK_QUERY = retrieval_query("Why was I assigned this risk category?")


@pytest.mark.parametrize("message", RISK_APPETITE)
def test_risk_appetite_questions_search_the_policy_by_concept(message):
    """The embedder gets the risk concept's text, not the client's sentence."""
    assert retrieval_query(message) == RISK_QUERY, message


@pytest.mark.parametrize("message", RISK_APPETITE)
def test_risk_appetite_questions_are_routed_not_left_general(message):
    """`general` + not confident is the bucket that gets no concept rewrite --
    and, now, the bucket the vagueness check inspects. These are neither vague
    nor unplaceable."""
    classification = classify(message)
    assert classification.intent is Intent.RISK_EXPLANATION, message
    assert classification.confident, message


class TestItDoesNotSwallowNeighbouringTopics:
    """Comparatives, not bare adjectives.

    "aggressive" and "conservative" both appear in the fair-dealing material in
    a completely different sense -- aggressive *sales tactics*. Matching the bare
    adjective would have routed those questions to the suitability policy and
    quietly made a different question unanswerable, trading one false refusal
    for another.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "What does the policy say about aggressive sales tactics?",
            "Are representatives allowed to use aggressive selling?",
            "What counts as aggressive marketing under the guidelines?",
        ],
    )
    def test_aggressive_sales_language_is_not_a_risk_appetite_question(self, message):
        assert retrieval_query(message) != RISK_QUERY, message

    def test_existing_concepts_still_route_apart(self):
        """Rebalancing and risk are different concepts and must stay so."""
        assert retrieval_query("What is rebalancing and why does it matter?") != RISK_QUERY
        assert retrieval_query("Why was I assigned this risk category?") == RISK_QUERY


class TestAcronymsAreSearchedByWhatTheyStandFor:
    """An acronym is a poor embedding. "Explain FEAT principles" scored 0.271
    against a 0.34 floor and returned one chunk; the four words it stands for
    score 0.552 and return the right document.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "Explain FEAT principles",
            "What are the FEAT principles?",
            "Tell me about the FEAT framework",
            "Does the FEAT guideline apply here?",
            "What does fairness ethics accountability transparency mean?",
        ],
    )
    def test_feat_is_searched_by_its_expansion(self, message):
        assert retrieval_query(message) == "fairness ethics accountability transparency", message

    @pytest.mark.parametrize(
        "message",
        [
            "What features does this platform have?",
            "Can you defeat the market?",
            "Is that a feature or a bug?",
        ],
    )
    def test_a_bare_substring_does_not_fire(self, message):
        """The concept match is a plain substring test, so the needles are
        qualified. An unqualified "feat" would fire on every one of these."""
        assert retrieval_query(message) != "fairness ethics accountability transparency", message


class TestOtherLanguages:
    """The priority languages get the same routing. Tamil is not covered yet --
    the needle list has its noun phrases but not its comparatives.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "¿Puedo invertir de forma mas agresiva?",
            "Boleh saya melabur dengan lebih agresif?",
            "我可以更激进地投资吗？",
        ],
    )
    def test_the_comparative_is_recognised(self, message):
        assert retrieval_query(message) == RISK_QUERY, message
