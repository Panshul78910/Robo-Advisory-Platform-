"""Advisory chat and the client's own audit trail."""

from __future__ import annotations

import datetime as _dt
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Query

from app.api.deps import chat_pipeline, client_repo, market_repo, portfolio_repo
from app.api.dto.common import (
    AuditEntryDto,
    ChatAnswerDto,
    PolicyChunkDto,
    epoch_ms,
    override_dto,
    status_after_review,
)
from app.api.dto.requests import ChatRequest
from app.api.views import asset_class_rows, client_facts, recent_transactions
from app.core.errors import NotFound
from app.core.timing import accounted, display_name, is_nested, snapshot, stage, start_request
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.domain.suitability.engine import evaluate
from app.domain.trading.valuation import value_position
from app.orchestration.intent import NEEDS_MARKET_EVIDENCE, classify
from app.orchestration.pipeline import ChatPipeline

router = APIRouter(tags=["chat"])
log = logging.getLogger("app.api.chat")


@router.post("/chat", response_model=ChatAnswerDto)
async def chat(
    body: ChatRequest,
    pipeline: ChatPipeline = Depends(chat_pipeline),
    repo: ClientRepository = Depends(client_repo),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
    when: _dt.date | None = Query(default=None, alias="session_date"),
) -> ChatAnswerDto:
    start_request()
    started = time.perf_counter()
    with stage("db.client_reads"):
        profile = await repo.profile()
        assessment = await repo.latest_risk_assessment()

    suitability = (
        evaluate(profile=profile, risk_category=assessment.category) if assessment else None
    )
    # Prices, positions and the ledger are expensive and were previously attached
    # to every turn, yet still omitted the trades. NEEDS_MARKET_EVIDENCE decides
    # whether this question actually needs them.
    classification = classify(body.message)
    load_market = classification.intent in NEEDS_MARKET_EVIDENCE
    valuation = None
    holdings = None
    ledger = None
    if load_market and (when is not None or await repo.current_session_date()):
        with stage("db.valuation"):
            session = when or await repo.current_session_date()
            try:
                valuation = await value_position(portfolios, market, session)
            except Exception:  # a missing price must not break the conversation
                valuation = None
        if valuation is not None:
            with stage("db.holdings"):
                try:
                    holdings = asset_class_rows(valuation, await market.instruments())
                except Exception:
                    holdings = None
        with stage("db.ledger"):
            try:
                ledger = await recent_transactions(portfolios, market)
            except Exception:
                ledger = None

    facts = client_facts(
        profile=profile,
        risk_category=assessment.category if assessment else None,
        eligible=suitability.eligible_portfolios if suitability else (),
        held_portfolio=(
            valuation["portfolio"].code if valuation and valuation["portfolio"] else None
        ),
        valuation=valuation,
        holdings=holdings,
        recent_transactions=ledger,
    )
    from app.orchestration.untrusted_text import statements_from_documents

    uploaded = statements_from_documents(await repo.documents())
    if uploaded:
        facts["uploadedStatements"] = uploaded

    conversation_id = (
        uuid.UUID(body.conversation_id)
        if body.conversation_id
        else await repo.open_conversation("advisory")
    )

    result = await pipeline.run(
        body.message,
        client_facts=facts,
        missing_profile_fields=tuple(profile.missing_fields()),
        conversation_id=conversation_id,
        policy_version=suitability.policy_version if suitability else None,
        client_email=profile.email or "",
    )

    # A total tells you a request was slow; a breakdown tells you which stage to
    # fix. Emitted once per chat request, slowest stage first, so the answer is
    # readable off the terminal rather than reconstructed from timestamps.
    stages = snapshot()
    total = int((time.perf_counter() - started) * 1000)
    explained = accounted()
    log.info(
        "request timing",
        extra={
            "input_chars": len(body.message),
            "total_ms": total,
            "accounted_ms": explained,
            "unaccounted_ms": total - explained,
            # Nested stages are shown indented rather than hidden: the point of
            # timing the embedding call inside retrieval is to see it. They are
            # excluded from `accounted_ms` only, where the parent covers them.
            "stages": [
                {"stage": ("  " + display_name(k)) if is_nested(k) else k, "ms": v}
                for k, v in stages.items()
            ],
        },
    )
    return ChatAnswerDto(**result.to_api(), conversation_id=str(conversation_id))


@router.get("/clients/me/audit", response_model=list[AuditEntryDto])
async def my_audit(
    limit: int = 100, repo: ClientRepository = Depends(client_repo)
) -> list[AuditEntryDto]:
    """C.6, scoped to the caller. An admin sees everyone via /api/admin/audit."""
    decisions = await repo.decisions(limit=limit)
    ids = [d["id"] for d in decisions]
    sources = await repo.decision_sources(ids)
    overrides = await repo.overrides_for(ids)
    return [_audit_dto(d, sources.get(d["id"], []), overrides.get(d["id"])) for d in decisions]


@router.get("/clients/me/audit/{decision_no}", response_model=AuditEntryDto)
async def my_audit_entry(
    decision_no: str, repo: ClientRepository = Depends(client_repo)
) -> AuditEntryDto:
    decisions = await repo.decisions(limit=500)
    match = next((d for d in decisions if d["display_no"] == decision_no), None)
    if match is None:
        raise NotFound("No such decision in your history.")
    sources = await repo.decision_sources([match["id"]])
    overrides = await repo.overrides_for([match["id"]])
    return _audit_dto(match, sources.get(match["id"], []), overrides.get(match["id"]))


def _audit_dto(decision: dict, sources: list[dict], override: dict | None = None) -> AuditEntryDto:
    return AuditEntryDto(
        decision_id=decision["display_no"],
        timestamp=epoch_ms(decision["created_at"]),
        decision_type=decision["decision_type"],
        input=decision["input"],
        output=decision["output"],
        confidence=decision["confidence"],
        model_version=decision["model_version"],
        prompt_version=decision["prompt_version"],
        policy_version=decision["policy_version"],
        retrieved_sources=[
            f"{s['doc_id']}" + (f" §{s['section']}" if s["section"] else "") for s in sources
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
            for s in sources
        ],
        human_override=override_dto(override),
        status=status_after_review(decision["status"], override),
        session_date=decision["session_date"],
        correlation_id=decision["correlation_id"],
    )
