"""Knowledge-assessment questions get their own search text.

One `_CONCEPTS` entry used to carry "cka" and "knowledge assessment" alongside
"eligib", "time horizon" and "diversif", with a single search string tuned for
eligibility. So four unrelated questions all became "eligible portfolios risk
category" and came back with byte-identical passages: what the CKA is, what
happens if you fail it, and why you are not eligible for a portfolio were all
answered out of the eligibility matrix.

The keyword arm lost most by it. "What is the CKA?" parses to the single token
`cka`, which appears in 3 of 250 chunks -- the rare literal the second arm
exists for. The rewrite replaced it before the arm ever ran.

Both entries keep the `policy_question` intent, so nothing downstream of
`semantic_intent_name` moves.

The split alone did not fix the second half of that. The string it moved to --
"customer knowledge assessment specified investment product" -- is MAS statutory
vocabulary and contains no `cka` either, so it retrieved FAA-N16 and SFA04-N12
and never Appendix E section 4, the only passage stating the question count and
the pass mark. "knowledge check" was not a trigger, so it escaped the rewrite
and was the one wording that answered correctly. The tests below pin all three
names onto one path, and pin the search text to the subject rather than to the
figures themselves.
"""

from __future__ import annotations

import pytest

from app.orchestration.intent import retrieval_query
from app.orchestration.semantics import semantic_intent_name, semantic_query

ELIGIBILITY = "eligible portfolios risk category"
KNOWLEDGE = (
    "Knowledge check (CKA-style): the customer knowledge assessment quiz, how many "
    "questions it has and its pass mark, required before the higher portfolios or "
    "any satellite ETF exposure"
)


class TestTheTwoTopicsAskDifferentQuestions:
    @pytest.mark.parametrize(
        "question",
        [
            "What is a Customer Knowledge Assessment?",
            "What is the CKA?",
            "What happens if I fail the knowledge assessment?",
            "Why do I need to pass the knowledge assessment?",
            # The three names for one thing. "knowledge check" used to be the
            # only one that answered correctly, because it was the only one that
            # escaped the rewrite -- so it is pinned alongside the others.
            "What is the knowledge check?",
            "How many questions are in the knowledge check?",
            "How many CKA knowledge check questions are there and what is the pass mark?",
        ],
    )
    def test_a_knowledge_question_searches_for_the_knowledge_assessment(self, question):
        assert semantic_query(question) == KNOWLEDGE

    def test_every_name_for_the_check_asks_the_same_question(self):
        """CKA, knowledge check and knowledge assessment are one topic. When
        they took different paths, the answer depended on which word was used."""
        asked = {
            semantic_query("How many CKA questions are there?"),
            semantic_query("How many knowledge check questions are there?"),
            semantic_query("How many knowledge assessment questions are there?"),
        }
        assert len(asked) == 1

    @pytest.mark.parametrize(
        "question",
        [
            "Am I eligible for P6?",
            "What determines my eligibility?",
            "Why am I not eligible for the aggressive portfolio?",
            "What is my investment horizon used for?",
            "How diversified is my portfolio?",
        ],
    )
    def test_an_eligibility_question_still_searches_for_eligibility(self, question):
        assert semantic_query(question) == ELIGIBILITY

    def test_which_portfolio_routes_earlier_and_always_did(self):
        """Not a casualty of the split: "which portfolio" is a
        `portfolio_explanation` needle, and that concept precedes both of
        these in the table. Pinned so the next person reading the eligibility
        tests does not mistake it for a regression."""
        assert semantic_query("Which portfolios am I eligible for?") == (
            "model portfolios allocation eligibility"
        )

    def test_they_no_longer_collapse_to_one_query(self):
        assert semantic_query("What is the CKA?") != semantic_query(
            "Why am I not eligible for the aggressive portfolio?"
        )


class TestNothingDownstreamMoves:
    @pytest.mark.parametrize(
        "question",
        [
            "What is the CKA?",
            "What happens if I fail the knowledge assessment?",
            "Am I eligible for P6?",
        ],
    )
    def test_both_entries_keep_the_policy_question_intent(self, question):
        assert semantic_intent_name(question) == "policy_question"

    def test_retrieval_query_passes_the_concept_through(self):
        assert retrieval_query("What is the CKA?") == KNOWLEDGE

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("Why was I assigned this risk category?", "risk_explanation"),
            ("What is rebalancing?", "rebalance_explanation"),
            ("What are my holdings?", "holdings_query"),
            ("What is my asset allocation?", "portfolio_explanation"),
        ],
    )
    def test_the_other_concepts_are_unaffected(self, question, expected):
        assert semantic_intent_name(question) == expected

    def test_the_knowledge_entry_is_reached_before_eligibility(self):
        """First match wins, and "knowledge assessment" would otherwise be
        swallowed by `eligib` appearing in the same sentence."""
        assert semantic_query("Am I eligible once I pass the knowledge assessment?") == KNOWLEDGE


class TestTheRareTokenSurvives:
    def test_the_cka_abbreviation_still_routes(self):
        assert semantic_query("cka") == KNOWLEDGE

    def test_the_search_text_names_the_subject(self):
        """The point of the split: a question about the knowledge assessment
        searches for the knowledge assessment."""
        assert "knowledge assessment" in KNOWLEDGE
        assert "eligible portfolios" not in KNOWLEDGE

    def test_the_search_text_carries_the_rare_token(self):
        """`cka` appears in 5 chunks. The keyword arm exists to catch exactly
        that kind of literal, and the previous string omitted it, so the arm
        never saw the token the concept was built around."""
        assert "CKA" in KNOWLEDGE

    def test_the_search_text_states_the_subject_not_the_answer(self):
        """The figures live in Appendix E section 4 and are read from the
        retrieved chunk. Writing them into the search string would put a second
        copy of a policy value in code -- and measured worse besides (0.760 for
        a string naming "five-question, 4 of 5" against 0.807 for this one)."""
        assert "five-question" not in KNOWLEDGE
        assert "4 of 5" not in KNOWLEDGE
