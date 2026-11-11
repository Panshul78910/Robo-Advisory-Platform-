"""B.3: heading-aware chunking that keeps tables whole.

The graded property is table preservation. A chunk holding three category names
and none of their caps retrieves well on keywords but grounds an explanation in
a fragment, which is worse than returning nothing.
"""

from __future__ import annotations

from app.rag.chunker import chunk_document

ELIGIBILITY_DOC = """## 3.2 Eligibility Matrix

The following matrix defines the highest model portfolio available to a client
in each risk category.

Table 3.2: Portfolio eligibility by risk category

| Risk category | Max portfolio | CKA required |
|---|---|---|
| Capital Preservation | P1 | No |
| Conservative | P2 | No |
| Moderately Conservative | P3 | No |
| Balanced | P4 | No |
| Moderately Aggressive | P5 | No |
| Growth | P6 | Yes |
| Aggressive | P7 | Yes |
| Very Aggressive | P8 | Yes |

Clients may hold any portfolio at or below their eligibility ceiling.

## 3.3 Override Conditions

An override forces a lower ceiling regardless of the computed score.
"""


def tables(chunks):
    return [c for c in chunks if c.is_table]


class TestTablePreservation:
    def test_a_table_becomes_exactly_one_chunk(self):
        chunks = chunk_document(ELIGIBILITY_DOC)
        assert len(tables(chunks)) == 1

    def test_every_row_survives_in_that_one_chunk(self):
        table = tables(chunk_document(ELIGIBILITY_DOC))[0]
        for category, portfolio in [
            ("Capital Preservation", "P1"),
            ("Balanced", "P4"),
            ("Very Aggressive", "P8"),
        ]:
            assert category in table.text
            assert portfolio in table.text

    def test_the_caption_travels_with_the_table(self):
        table = tables(chunk_document(ELIGIBILITY_DOC))[0]
        assert "Table 3.2" in table.text, "a table without its caption loses its meaning"

    def test_an_oversized_table_is_still_not_split(self):
        rows = "\n".join(f"| Instrument {i} | {i * 3}% | Notes for row {i} |" for i in range(200))
        doc = f"## Big\n\nTable 9.1: Large holdings\n\n| A | B | C |\n|---|---|---|\n{rows}\n"
        chunks = chunk_document(doc, target_tokens=100)
        assert len(tables(chunks)) == 1
        assert "Instrument 199" in tables(chunks)[0].text


class TestProseChunking:
    def test_headings_are_recorded_on_their_chunks(self):
        chunks = chunk_document(ELIGIBILITY_DOC)
        headings = {c.heading for c in chunks if c.heading}
        assert "3.2 Eligibility Matrix" in headings
        assert "3.3 Override Conditions" in headings

    def test_long_prose_is_split_with_overlap(self):
        body = " ".join(f"Sentence number {i} about the suitability policy." for i in range(300))
        chunks = chunk_document(f"## Policy\n\n{body}", target_tokens=120, overlap_ratio=0.15)
        assert len(chunks) > 1
        # Consecutive prose chunks should share some text.
        assert any(
            set(a.text.split()) & set(b.text.split())
            for a, b in zip(chunks, chunks[1:], strict=False)
        )

    def test_ordinals_are_contiguous_from_zero(self):
        chunks = chunk_document(ELIGIBILITY_DOC)
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))

    def test_page_markers_are_captured(self):
        doc = "<!-- page: 4 -->\n\n## Section\n\nSome policy prose here.\n"
        chunks = chunk_document(doc)
        assert any(c.page == 4 for c in chunks)

    def test_empty_input_produces_no_chunks(self):
        assert chunk_document("   \n\n  ") == []


def test_no_chunk_is_empty():
    for chunk in chunk_document(ELIGIBILITY_DOC):
        assert chunk.text.strip()
        assert chunk.token_count > 0
