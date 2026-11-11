"""The single writer for the decision audit log (C.6).

Every feature that makes a consequential decision -- suitability rule firings,
rebalances, reverts, chat answers, overrides -- routes through `record_decision`
and lands in the same table with the same schema. That is what lets a supervisor
pull one client's history as a single continuous narrative rather than joining
several per-feature tables.

This module never opens a transaction. It writes inside the caller's, so the
decision and the action it records commit or roll back together (invariant 10).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import WallClock
from app.core.correlation import get_correlation_id
from app.core.errors import NotFound
from app.core.version import prompt_version
from app.db import models as m


@dataclass(frozen=True, slots=True)
class SourceRef:
    """A retrieved passage, copied rather than referenced.

    `passage_text` is stored so a citation shown in week 8 still expands to the
    text the client actually saw after the corpus is re-ingested in week 10.
    """

    chunk_ref: str
    doc_id: str
    title: str | None
    section: str | None
    page: int | None
    score: Decimal | None
    passage_text: str


@dataclass(frozen=True, slots=True)
class EscalationReason:
    reason_code: str


@dataclass(frozen=True, slots=True)
class Decision:
    id: uuid.UUID
    display_no: str
    status: str


async def record_decision(
    conn: AsyncConnection,
    *,
    client_id: uuid.UUID | None,
    decision_type: str,
    status: str,
    input: str | None = None,
    output: str | None = None,
    confidence: Decimal | None = None,
    sources: list[SourceRef] | None = None,
    model_version: str | None = None,
    prompt_version_: str | None = None,
    policy_version: str | None = None,
    session_date: date | None = None,
    correlation_id: str | None = None,
    escalate: EscalationReason | None = None,
) -> Decision:
    """Append one decision, its sources and optionally an escalation.

    `confidence` stays NULL when the system abstained. Storing 0 would be
    indistinguishable from a confident "definitely not" (C.4).
    """
    abstained = confidence is None and status == "abstained"
    row = (
        (
            await conn.execute(
                sa.insert(m.decision)
                .values(
                    client_id=client_id,
                    decision_type=decision_type,
                    status=status,
                    input=input,
                    output=output,
                    confidence=confidence,
                    abstained=abstained,
                    refused=status == "refused",
                    model_version=model_version,
                    prompt_version=prompt_version_ or prompt_version(),
                    policy_version=policy_version,
                    session_date=session_date,
                    correlation_id=correlation_id or get_correlation_id(),
                )
                .returning(m.decision.c.id, m.decision.c.display_no, m.decision.c.status)
            )
        )
        .mappings()
        .one()
    )

    decision_id = row["id"]

    if sources:
        await conn.execute(
            sa.insert(m.decision_source),
            [
                {
                    "decision_id": decision_id,
                    "chunk_ref": s.chunk_ref,
                    "doc_id": s.doc_id,
                    "title": s.title,
                    "section": s.section,
                    "page": s.page,
                    "score": s.score,
                    "passage_text": s.passage_text,
                }
                for s in sources
            ],
        )

    if escalate is not None:
        if client_id is None:
            raise ValueError("an escalation must belong to a client")
        await conn.execute(
            sa.insert(m.escalation).values(
                decision_id=decision_id,
                client_id=client_id,
                reason_code=escalate.reason_code,
            )
        )

    return Decision(id=decision_id, display_no=row["display_no"], status=row["status"])


async def record_override(
    conn: AsyncConnection,
    *,
    decision_id: uuid.UUID,
    reviewer_user_id: uuid.UUID,
    action: str,
    final_output: str,
    reason: str | None = None,
) -> None:
    """Record a human decision over an AI proposal (C.5).

    The original decision row is untouched -- it is append-only, and preserving
    what the AI proposed is the point. `pre_override_output` is copied from it so
    the trail shows both what the model would have done and what the human chose.
    """
    pre = (
        await conn.execute(sa.select(m.decision.c.output).where(m.decision.c.id == decision_id))
    ).scalar_one_or_none()
    if pre is None:
        raise NotFound("No such decision.")

    await conn.execute(
        sa.insert(m.decision_override).values(
            decision_id=decision_id,
            reviewer_user_id=reviewer_user_id,
            action=action,
            reason=reason,
            pre_override_output=pre,
            final_output=final_output,
        )
    )
    # The escalation queue is working state, not an audit record, so it is the
    # one thing here that is updated in place.
    await conn.execute(
        sa.update(m.escalation)
        .where(m.escalation.c.decision_id == decision_id)
        .where(m.escalation.c.status == "pending")
        .values(status="resolved", resolved_at=WallClock.now())
    )


async def provenance(conn: AsyncConnection, decision_id: uuid.UUID) -> dict:
    """The full trail for one decision (C.6 sign-off)."""
    decision = (
        (await conn.execute(sa.select(m.decision).where(m.decision.c.id == decision_id)))
        .mappings()
        .first()
    )
    if decision is None:
        raise NotFound("No such decision.")

    sources = (
        (
            await conn.execute(
                sa.select(m.decision_source)
                .where(m.decision_source.c.decision_id == decision_id)
                .order_by(m.decision_source.c.id)
            )
        )
        .mappings()
        .all()
    )
    override = (
        (
            await conn.execute(
                sa.select(m.decision_override).where(
                    m.decision_override.c.decision_id == decision_id
                )
            )
        )
        .mappings()
        .first()
    )

    return {
        "decision": dict(decision),
        "sources": [dict(s) for s in sources],
        "override": dict(override) if override else None,
    }
