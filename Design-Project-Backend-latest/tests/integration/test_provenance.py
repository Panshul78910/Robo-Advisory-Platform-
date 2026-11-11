"""C.6: retrieve the full provenance trail for a past decision.

The trail must show not only what happened, but what the AI would have done on
its own and what a human decided instead.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.audit import service as audit
from app.db.repositories.admin import AdminReadRepository

D = Decimal

SOURCES = [
    audit.SourceRef(
        chunk_ref="POL-001#12",
        doc_id="POL-001",
        title="Company Suitability Policy",
        section="3.2 Eligibility Matrix",
        page=4,
        score=D("0.9123"),
        passage_text="Clients in the Growth category may hold portfolios up to P6.",
    )
]


@pytest.fixture
async def recorded(conn, principal):
    return await audit.record_decision(
        conn,
        client_id=principal.client_id,
        decision_type="recommendation",
        status="pending_review",
        input="Which portfolio suits me?",
        output="P6 (Growth) looks appropriate for your assessed category.",
        confidence=D("0.8800"),
        sources=SOURCES,
        model_version="stub-1.0",
        policy_version="appendix-e-placeholder-v1",
        escalate=audit.EscalationReason("low_confidence"),
    )


class TestTrailContents:
    async def test_every_required_field_is_retrievable(self, conn, recorded):
        trail = await audit.provenance(conn, recorded.id)
        decision = trail["decision"]

        assert decision["input"] == "Which portfolio suits me?"
        assert decision["output"].startswith("P6")
        assert decision["confidence"] == D("0.8800")
        assert decision["model_version"] == "stub-1.0"
        assert decision["prompt_version"], "prompt version is stamped automatically"
        assert decision["policy_version"] == "appendix-e-placeholder-v1"
        assert decision["correlation_id"]

    async def test_the_passage_text_is_copied_not_referenced(self, conn, recorded):
        """A citation shown in week 8 must still expand after a week 10 re-ingest."""
        trail = await audit.provenance(conn, recorded.id)
        source = trail["sources"][0]
        assert source["passage_text"].startswith("Clients in the Growth category")
        assert source["section"] == "3.2 Eligibility Matrix"
        assert source["page"] == 4
        assert source["chunk_ref"] == "POL-001#12"

    async def test_a_display_number_is_assigned(self, recorded):
        assert recorded.display_no.startswith("D-")

    async def test_an_escalation_is_queued(self, conn, recorded, admin_principal):
        repo = AdminReadRepository(conn, admin_principal)
        queue = await repo.escalations("pending")
        assert any(e["display_no"] == recorded.display_no for e in queue)


class TestOverride:
    async def test_the_human_decision_supersedes_the_ai_output(
        self, conn, recorded, admin_principal
    ):
        """C.5 sign-off."""
        await audit.record_override(
            conn,
            decision_id=recorded.id,
            reviewer_user_id=admin_principal.user_id,
            action="edit",
            final_output="P5 is more appropriate given the client's stated horizon.",
            reason="Horizon shorter than the model assumed.",
        )

        trail = await audit.provenance(conn, recorded.id)
        override = trail["override"]
        assert override is not None
        assert override["action"] == "edit"
        assert override["final_output"].startswith("P5")
        # Both versions survive: that is what makes it provenance, not history.
        assert override["pre_override_output"].startswith("P6")
        assert trail["decision"]["output"].startswith("P6")

    async def test_reviewing_resolves_the_escalation(self, conn, recorded, admin_principal):
        await audit.record_override(
            conn,
            decision_id=recorded.id,
            reviewer_user_id=admin_principal.user_id,
            action="approve",
            final_output="Confirmed.",
        )
        status = (
            await conn.execute(
                sa.text("select status from escalation where decision_id = :d"),
                {"d": recorded.id},
            )
        ).scalar_one()
        assert status == "resolved"

    async def test_the_client_trail_includes_the_human_override(
        self, conn, recorded, admin_principal, principal
    ):
        """C.5 is client-visible. The admin console is not the only surface."""
        from app.api.routers.chat import _audit_dto
        from app.db.repositories.client import ClientRepository

        await audit.record_override(
            conn,
            decision_id=recorded.id,
            reviewer_user_id=admin_principal.user_id,
            action="edit",
            final_output="P5 is more appropriate given the client's stated horizon.",
            reason="Horizon shorter than the model assumed.",
        )
        repo = ClientRepository(conn, principal)
        match = next(d for d in await repo.decisions() if d["id"] == recorded.id)
        override = (await repo.overrides_for([match["id"]])).get(match["id"])
        dto = _audit_dto(match, [], override)
        assert dto.output.startswith("P6"), "the AI proposal stays on the decision"
        assert dto.human_override is not None
        assert dto.human_override.action == "edit"
        assert dto.human_override.final_output.startswith("P5")
        assert dto.status == "approved_edited"


class TestAbstentionRecording:
    async def test_an_abstention_stores_null_confidence_not_zero(self, conn, principal):
        decision = await audit.record_decision(
            conn,
            client_id=principal.client_id,
            decision_type="chat_qa",
            status="abstained",
            input="Plan me a trip to Japan",
            output="That is outside what I can help with.",
            confidence=None,
        )
        trail = await audit.provenance(conn, decision.id)
        assert trail["decision"]["confidence"] is None
        assert trail["decision"]["abstained"] is True


class TestAppendOnly:
    async def test_updating_a_decision_is_rejected_by_the_database(self, conn, recorded):
        """Invariant 2, enforced in 007_lockdown.sql rather than by convention."""
        with pytest.raises(Exception, match="append-only"):
            await conn.execute(
                sa.text("update decision set output = 'tampered' where id = :d"),
                {"d": recorded.id},
            )
