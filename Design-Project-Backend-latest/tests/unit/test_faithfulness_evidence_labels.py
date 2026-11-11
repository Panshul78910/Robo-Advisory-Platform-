"""The judge is shown which document each passage came from.

Asked what a Specified Investment Product is, the model cited SFA 04-N12
correctly. gpt-oss-20b reasoned, verbatim: "The policy excerpt does not mention
SFA04-N12. It mentions 'Notice on Recommendations On Investment Products' but
not SFA04-N12. So that claim is unsupported by evidence." It was right about
the text it was given -- the document's identity lives in the `chunk_ref`, and
the ref was never passed. The generator sees those refs as citation markers, so
naming a source was precisely what made an answer unverifiable.

The labelled string is for the judge alone. `ungrounded_numbers` keeps taking
the raw chunk texts, because a ref carries digits and folding them into its
evidence would admit them as grounded figures.
"""

from __future__ import annotations

from app.orchestration.guardrails import _faithfulness_evidence, ungrounded_numbers

REFS = ("SFA04-N12_SALE_OF_INVESTMENT_PRODUCTS_20#7", "FAA-N16_RECOMMENDATIONS#10")
TEXTS = ["An Excluded Investment Product means any product set out in Annex 1.",
         "A reasonable basis is required for every recommendation."]


class TestThePassagesCarryTheirSource:
    def test_each_passage_is_labelled(self):
        out = _faithfulness_evidence("no citations here", TEXTS, REFS)
        assert "[SFA04-N12_SALE_OF_INVESTMENT_PRODUCTS_20#7]" in out
        assert "[FAA-N16_RECOMMENDATIONS#10]" in out

    def test_the_document_name_is_findable_in_the_evidence(self):
        """The exact failure: the judge looked for "SFA04-N12" and did not
        find it."""
        assert "SFA04-N12" in _faithfulness_evidence("no citations", TEXTS, REFS)

    def test_the_passage_text_survives_intact(self):
        out = _faithfulness_evidence("no citations", TEXTS, REFS)
        for text in TEXTS:
            assert text in out


class TestTheNarrowingStillWorks:
    def test_a_cited_reply_is_judged_on_what_it_cites(self):
        reply = f"As set out in chunk_ref={REFS[0]}, excluded products are listed."
        out = _faithfulness_evidence(reply, list(TEXTS), REFS)
        assert TEXTS[0] in out
        assert TEXTS[1] not in out

    def test_an_uncited_reply_still_gets_everything(self):
        out = _faithfulness_evidence("no citations at all", list(TEXTS), REFS)
        assert TEXTS[0] in out and TEXTS[1] in out

    def test_no_chunks_is_empty(self):
        assert _faithfulness_evidence("anything", [], ()) == ""

    def test_mismatched_refs_fall_back_to_bare_text(self):
        out = _faithfulness_evidence("anything", list(TEXTS), ("only-one-ref",))
        assert TEXTS[0] in out and TEXTS[1] in out
        assert "[only-one-ref]" not in out


class TestTheNumberCheckIsNotContaminated:
    def test_ref_digits_do_not_become_grounded_figures(self):
        """`...PRODUCTS_20#31` carries 31, which no passage states. Figures at
        or below 20 are exempt anyway as section and paragraph numbers, so the
        contamination only shows above that."""
        refs = ("SFA04-N12_SALE_OF_INVESTMENT_PRODUCTS_20#31", REFS[1])
        invented = "Your equity sleeve is 31%."

        assert ungrounded_numbers(invented, ["{}", "", *TEXTS])

        # The same call with the labelled string in place of the raw texts --
        # the mistake this separation exists to prevent.
        labelled = _faithfulness_evidence("no citations", list(TEXTS), refs)
        assert not ungrounded_numbers(invented, ["{}", "", labelled])

    def test_a_figure_genuinely_in_a_passage_still_passes(self):
        evidence = ["{}", "", "The satellite cap is 15% of the portfolio."]
        assert not ungrounded_numbers("The satellite cap is 15%.", evidence)
