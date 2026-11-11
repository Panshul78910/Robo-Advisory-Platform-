"""Sections written for the team do not go into the client index.

Asked "why was I assigned this risk category?", retrieval handed the model the
confidentiality statement and the prohibited-behaviours list, and the answer
opened by refusing to disclose a scoring formula nobody had asked for. A client
reading that cannot tell they were not suspected of something, and an
over-refusal on a question the policy explicitly entitles them to ask is scored
as one.

Measured over six runs: with both sections in the client index the answer opened
with an unasked refusal four times; with both held back, none.
"""

from __future__ import annotations

import pytest

from app.rag.ingest import INTERNAL_SECTIONS, _chunk_audience


class TestWhichSectionsAreHeldBack:
    @pytest.mark.parametrize(
        "heading",
        [
            "7. Prohibited behaviours",
            "G.5 Confidentiality statement",
            "Prohibited Behaviours",
            "Section 9 - confidentiality statement",
        ],
    )
    def test_an_internal_section_becomes_admin(self, heading):
        assert _chunk_audience("client", heading) == "admin"

    @pytest.mark.parametrize(
        "heading",
        [
            "2. Eligibility matrix",
            "3. Suitability overrides (applied after scoring)",
            "4. Knowledge check (CKA-style)",
            "6. Escalation triggers",
            "G.2 Mandatory disclosure block (recommendations)",
            "1. Model portfolios",
            None,
        ],
    )
    def test_everything_else_stays_client_visible(self, heading):
        """The escalation triggers in particular: a client disputing their
        category needs to be told the route, and that lives next door to the
        sections being held back."""
        assert _chunk_audience("client", heading) == "client"


class TestItOnlyNarrows:
    def test_an_admin_corpus_is_untouched(self):
        assert _chunk_audience("admin", "2. Eligibility matrix") == "admin"
        assert _chunk_audience("admin", "7. Prohibited behaviours") == "admin"

    def test_the_pattern_is_not_anchored_to_one_document(self):
        """Numbering differs between Appendix E and Appendix G, so the rule
        matches the words rather than a section number."""
        assert INTERNAL_SECTIONS.search("7. Prohibited behaviours")
        assert INTERNAL_SECTIONS.search("G.5 Confidentiality statement")
        assert not INTERNAL_SECTIONS.search("5. Mandatory disclosure")
