"""Internal dicts must survive DTO validation with every field intact.

`serialization_alias` governs output only. A dict built with the camelCase wire
names therefore fails validation on required fields and -- far worse -- is
*silently discarded* on optional ones, leaving every field at its default.

That is not hypothetical. `/api/chat` returned 500 on any grounded answer
because `doc_id` was emitted as `docId`, and in the same response `decisionId`,
`confidenceLabel`, `requiresReview` and `cautions` were all quietly dropped,
taking C.4's confidence banding and C.5's review routing with them. Neither
showed up in 325 passing tests.
"""

from __future__ import annotations

from decimal import Decimal

from app.api.dto.common import ChatAnswerDto, PolicyChunkDto, status_after_review
from app.orchestration.confidence import Confidence
from app.orchestration.intent import Intent
from app.orchestration.pipeline import ChatResult
from app.rag.retrieve import RetrievalResult, RetrievedChunk

D = Decimal

CHUNK = RetrievedChunk(
    chunk_ref="POL-001#7",
    doc_id="POL-001",
    title="Company Suitability Policy",
    section="3.2 Eligibility matrix",
    page=4,
    text="Table 3.2: Portfolio ceiling by risk category",
    score=D("0.6008"),
)


def _result() -> ChatResult:
    import uuid

    return ChatResult(
        text="Your ceiling is 25%.",
        confidence=Confidence(
            value=D("0.4200"),
            abstained=False,
            reasons=("below_threshold",),
            ambiguity_options=("now", "if re-assessed"),
            requires_review=True,
            cautions=("Please review this before acting on it.",),
        ),
        retrieval=RetrievalResult(chunks=(CHUNK,), grounded=True, top_score=D("0.6008")),
        intent=Intent.POLICY_QUESTION,
        decision_id=uuid.uuid4(),
        decision_no="D-1234",
        model_version="gpt-4o-mini",
        disclosure_applied=True,
        requires_review=True,
        refused_formula=False,
    )


class TestCitation:
    def test_a_retrieved_chunk_validates_as_a_dto(self):
        """This is the exact call `/api/chat` makes; it used to raise."""
        dto = PolicyChunkDto(**CHUNK.to_citation())
        assert dto.doc_id == "POL-001"
        assert dto.chunk_ref == "POL-001#7"

    def test_it_still_serialises_to_the_camelcase_wire_names(self):
        payload = PolicyChunkDto(**CHUNK.to_citation()).model_dump(by_alias=True)
        assert "docId" in payload and "doc_id" not in payload
        assert "chunkRef" in payload


class TestChatAnswer:
    def test_the_pipeline_result_validates_whole(self):
        dto = ChatAnswerDto(**_result().to_api())
        assert dto.decision_id == "D-1234"
        assert dto.confidence_label == "low"
        assert dto.requires_review is True
        assert dto.cautions and dto.ambiguity_options
        assert len(dto.sources) == 1

    def test_no_field_is_silently_dropped(self):
        """The dangerous half: optional fields fall back to defaults in silence."""
        api = _result().to_api()
        dto = ChatAnswerDto(**api)
        dumped = dto.model_dump()  # field names, not aliases
        lost = [
            k for k, v in api.items() if k in dumped and dumped[k] != v and not isinstance(v, list)
        ]
        assert not lost, f"these values did not survive validation: {lost}"

    def test_every_key_matches_a_field_name(self):
        """A key the DTO does not recognise is the failure mode itself."""
        unknown = set(_result().to_api()) - set(ChatAnswerDto.model_fields)
        assert not unknown, (
            f"to_api emits keys ChatAnswerDto cannot bind: {sorted(unknown)}. "
            "Use field names; serialization aliases do not apply on input."
        )


class TestReviewStatus:
    def test_an_edit_is_not_left_pending(self):
        assert status_after_review("pending_review", {"action": "edit"}) == "approved_edited"

    def test_an_unreviewed_row_keeps_its_status(self):
        assert status_after_review("pending_review", None) == "pending_review"
