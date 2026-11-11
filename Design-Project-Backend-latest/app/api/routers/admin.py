"""Admin console: client oversight, the escalation queue, C.5 review, RAG management."""

from __future__ import annotations

import datetime as _dt
import pathlib
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import apply_fault_header
from app.api.dto.common import (
    AuditEntryDto,
    OkDto,
    PolicyChunkDto,
    epoch_ms,
    override_dto,
    status_after_review,
)
from app.api.dto.requests import RagIngestRequest, RagQueryRequest, ReviewRequest
from app.audit import service as audit
from app.core.auth import Principal, require_admin
from app.core.errors import NotFound, ValidationError
from app.db.repositories.admin import AdminReadRepository
from app.db.session import get_connection
from app.rag import store_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

_SCORE_KEYS = {"score", "risk_score", "riskScore"}


def _jsonable(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


async def admin_repo(
    conn: AsyncConnection = Depends(get_connection),
    principal: Principal = Depends(require_admin),
) -> AdminReadRepository:
    return AdminReadRepository(conn, principal)


@router.get("/clients")
async def list_clients(repo: AdminReadRepository = Depends(admin_repo)) -> list[dict]:
    """A.1(5): every Appendix D profile, visible only to the admin role."""
    return [
        {
            "clientId": str(c["id"]),
            "clientCode": c["client_code"],
            "fullName": c["full_name"],
            "email": c["email"],
            "ckaPassed": c["cka_passed"],
            "riskCategory": c["category"],
            "assessedAt": c["assessed_at"],
            "pendingEscalations": c["pending"],
        }
        for c in await repo.clients()
    ]


@router.get("/clients/{client_id}")
async def get_client(client_id: uuid.UUID, repo: AdminReadRepository = Depends(admin_repo)) -> dict:
    """Structured fields only — never dump the raw SQLAlchemy row (that overflowed the officer UI)."""
    record = await repo.client(client_id)
    if record is None:
        raise NotFound("No such client.")
    # Incoming main compacted the same _SCORE_KEYS dump. That dump overflowed
    # the officer UI; the structured record still omits scores (pushed contract)
    # and is what the console renders.
    return {
        "clientCode": record.get("client_code"),
        "fullName": record.get("full_name"),
        "dateOfBirth": _jsonable(record.get("date_of_birth")),
        "email": record.get("email"),
        "monthlySalary": _jsonable(record.get("monthly_salary")),
        "monthlyCommitments": _jsonable(record.get("monthly_commitments")),
        "dependants": record.get("dependants"),
        "investmentObjective": record.get("investment_objective"),
        "investmentHorizonYrs": record.get("investment_horizon_yrs"),
        "lossTolerance": record.get("loss_tolerance"),
        "liquidityNeeds": record.get("liquidity_needs"),
        "incomeStability": record.get("income_stability"),
        "investmentExperience": record.get("investment_experience"),
        "ckaPassed": record.get("cka_passed"),
        "ckaPassedAt": epoch_ms(record.get("cka_passed_at")),
        "updatedAt": epoch_ms(record.get("updated_at")),
    }


@router.get("/escalations")
async def escalations(
    status: str | None = Query(default="pending"),
    repo: AdminReadRepository = Depends(admin_repo),
) -> list[dict]:
    """A.3(3): refusals surface here as a queue a human can act on."""
    return [
        {
            "escalationId": str(e["id"]),
            "decisionId": e["display_no"],
            "clientCode": e["client_code"],
            "clientName": e["full_name"],
            "reasonCode": e["reason_code"],
            "status": e["status"],
            "decisionStatus": e.get("decision_status"),
            "refused": bool(e.get("refused")),
            "decisionType": e["decision_type"],
            "input": e["input"],
            "aiOutput": e["output"],
            "confidence": float(e["confidence"]) if e["confidence"] is not None else None,
            "createdAt": epoch_ms(e["created_at"]),
            "resolvedAt": epoch_ms(e["resolved_at"]),
        }
        for e in await repo.escalations(status)
    ]


@router.post("/decisions/{decision_no}/review", response_model=OkDto)
async def review(
    decision_no: str,
    body: ReviewRequest,
    principal: Principal = Depends(require_admin),
    repo: AdminReadRepository = Depends(admin_repo),
    conn: AsyncConnection = Depends(get_connection),
) -> OkDto:
    """C.5: the human decision, not the AI's proposal, becomes the final result.

    The original decision row is never edited -- preserving what the model
    proposed alongside what the reviewer chose is what makes this a provenance
    trail rather than a transaction history.
    """
    decision = await repo.decision_by_display_no(decision_no)
    if decision is None:
        raise NotFound("No such decision.")
    if body.action == "edit" and not body.final_output:
        raise ValidationError("An edited review must supply the corrected output.")

    final = {
        "approve": decision["output"] or "",
        "edit": body.final_output or "",
        "reject": body.reason or "The reviewer declined this recommendation.",
    }[body.action]

    await audit.record_override(
        conn,
        decision_id=decision["id"],
        reviewer_user_id=principal.user_id,
        action=body.action,
        final_output=final,
        reason=body.reason,
    )
    return OkDto(
        detail=f"Decision {decision_no} {body.action}d by {principal.display_name}.",
        data={"finalOutput": final},
    )


@router.get("/audit", response_model=list[AuditEntryDto])
async def audit_trail(
    client_id: uuid.UUID | None = None,
    limit: int = 200,
    repo: AdminReadRepository = Depends(admin_repo),
) -> list[AuditEntryDto]:
    """C.6: the full provenance trail for any past decision."""
    decisions = await repo.audit(client_id=client_id, limit=limit)
    ids = [d["id"] for d in decisions]
    sources = await repo.sources_for(ids)
    overrides = await repo.overrides_for(ids)

    return [
        AuditEntryDto(
            decision_id=d["display_no"],
            timestamp=epoch_ms(d["created_at"]),
            client_id=str(d["client_id"]) if d["client_id"] else None,
            client_name=d.get("full_name"),
            decision_type=d["decision_type"],
            input=d["input"],
            output=d["output"],
            confidence=d["confidence"],
            model_version=d["model_version"],
            prompt_version=d["prompt_version"],
            policy_version=d["policy_version"],
            retrieved_sources=[
                f"{s['doc_id']}" + (f" §{s['section']}" if s["section"] else "")
                for s in sources.get(d["id"], [])
            ],
            sources=[
                PolicyChunkDto(
                    doc_id=s["doc_id"],
                    title=s["title"] or s["doc_id"],
                    section=s["section"],
                    page=s["page"],
                    text=s["passage_text"],
                    chunk_ref=s["chunk_ref"],
                    score=s["score"],
                )
                for s in sources.get(d["id"], [])
            ],
            human_override=override_dto(overrides.get(d["id"])),
            status=status_after_review(d["status"], overrides.get(d["id"])),
            session_date=d["session_date"],
            correlation_id=d["correlation_id"],
        )
        for d in decisions
    ]


# --- vector store management (B.4) -----------------------------------------
@router.get("/rag/query")
async def rag_query(
    query: str,
    top_k: int = 10,
    audience: str | None = None,
    conn: AsyncConnection = Depends(get_connection),
) -> dict:
    """Ranked results *with scores*, and no relevance floor.

    An admin needs to see what was rejected and why -- which is exactly what the
    floor hides from a client.
    """
    return await store_admin.query(conn, query, top_k=top_k, audience=audience)


@router.post("/rag/query")
async def rag_query_post(
    body: RagQueryRequest, conn: AsyncConnection = Depends(get_connection)
) -> dict:
    return await store_admin.query(conn, body.query, top_k=body.top_k, audience=body.audience)


@router.get("/rag/stats")
async def rag_stats(conn: AsyncConnection = Depends(get_connection)) -> dict:
    return await store_admin.stats(conn)


@router.post("/rag/ingest", response_model=OkDto)
async def rag_ingest(body: RagIngestRequest) -> OkDto:
    from app.rag.ingest import ingest

    source = pathlib.Path(body.source)
    if not source.is_dir():
        raise ValidationError(f"{body.source} is not a readable directory on the server.")
    stats = ingest(source, label=body.corpus_label, audience=body.audience, reset=body.reset)
    return OkDto(detail=f"Corpus {stats['version']} indexed.", data=stats)


@router.post("/rag/reset", response_model=OkDto)
async def rag_reset(conn: AsyncConnection = Depends(get_connection)) -> OkDto:
    return OkDto(detail="Vector store cleared.", data=await store_admin.reset(conn))


# --- diagnostics ------------------------------------------------------------
@router.get("/debug/last-payload")
async def last_payload(
    repo: AdminReadRepository = Depends(admin_repo),
    _: None = Depends(apply_fault_header),
) -> dict:
    """B.5 sign-off: the assembled context, its per-block token counts, and what
    was dropped to fit."""
    record = await repo.last_payload()
    if record is None:
        raise NotFound("No LLM call has been made yet.")
    return {
        "correlationId": record["correlation_id"],
        "status": record["status"],
        "attempt": record["attempt"],
        "totalTokens": record["total_tokens"],
        "contextLimit": record["context_limit"],
        "blockTokens": record["block_tokens"],
        "latencyMs": record["latency_ms"],
        "createdAt": epoch_ms(record["created_at"]),
        "payload": record["payload"],
        "responseText": record["response_text"],
    }
