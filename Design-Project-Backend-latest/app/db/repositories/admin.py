"""Admin reads across all clients. Reachable only via `require_admin`."""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from app.db import models as m
from app.db.repositories.base import AdminRepository


class AdminReadRepository(AdminRepository):
    async def clients(self) -> list[dict]:
        rows = (
            (
                await self._conn.execute(
                    sa.text(
                        """
                    select c.id, c.client_code, p.full_name, p.email, p.cka_passed,
                           r.category, r.score, r.created_at as assessed_at,
                           (select count(*) from escalation e
                             where e.client_id = c.id and e.status = 'pending') as pending
                      from client c
                      join client_profile p on p.client_id = c.id
                      left join lateral (
                            select category, score, created_at from risk_assessment
                             where client_id = c.id order by created_at desc limit 1
                      ) r on true
                     order by c.client_code
                    """
                    )
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def client(self, client_id: uuid.UUID) -> dict | None:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.client_profile, m.client.c.client_code)
                    .join(m.client, m.client.c.id == m.client_profile.c.client_id)
                    .where(m.client.c.id == client_id)
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def escalations(self, status: str | None = "pending") -> list[dict]:
        stmt = (
            sa.select(
                m.escalation,
                m.decision.c.display_no,
                m.decision.c.decision_type,
                m.decision.c.input,
                m.decision.c.output,
                m.decision.c.confidence,
                m.decision.c.status.label("decision_status"),
                m.decision.c.refused,
                m.decision.c.created_at.label("decided_at"),
                m.client.c.client_code,
                m.client_profile.c.full_name,
            )
            .select_from(
                m.escalation.join(m.decision, m.decision.c.id == m.escalation.c.decision_id)
                .join(m.client, m.client.c.id == m.escalation.c.client_id)
                .join(m.client_profile, m.client_profile.c.client_id == m.client.c.id)
            )
            .order_by(m.escalation.c.created_at.desc())
        )
        if status == "refused":
            stmt = stmt.where(m.decision.c.status == "refused")
        elif status:
            stmt = stmt.where(m.escalation.c.status == status)
        return [dict(r) for r in (await self._conn.execute(stmt)).mappings().all()]

    async def audit(self, *, client_id: uuid.UUID | None = None, limit: int = 200) -> list[dict]:
        stmt = (
            sa.select(
                m.decision,
                m.client.c.client_code,
                m.client_profile.c.full_name,
            )
            .select_from(
                m.decision.outerjoin(m.client, m.client.c.id == m.decision.c.client_id).outerjoin(
                    m.client_profile, m.client_profile.c.client_id == m.client.c.id
                )
            )
            .order_by(m.decision.c.created_at.desc())
            .limit(limit)
        )
        if client_id:
            stmt = stmt.where(m.decision.c.client_id == client_id)
        return [dict(r) for r in (await self._conn.execute(stmt)).mappings().all()]

    async def sources_for(self, decision_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
        if not decision_ids:
            return {}
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.decision_source)
                    .where(m.decision_source.c.decision_id.in_(decision_ids))
                    .order_by(m.decision_source.c.id)
                )
            )
            .mappings()
            .all()
        )
        out: dict[uuid.UUID, list[dict]] = {}
        for r in rows:
            out.setdefault(r["decision_id"], []).append(dict(r))
        return out

    async def overrides_for(self, decision_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
        if not decision_ids:
            return {}
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.decision_override).where(
                        m.decision_override.c.decision_id.in_(decision_ids)
                    )
                )
            )
            .mappings()
            .all()
        )
        return {r["decision_id"]: dict(r) for r in rows}

    async def decision_by_display_no(self, display_no: str) -> dict | None:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.decision).where(m.decision.c.display_no == display_no)
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None

    async def last_payload(self) -> dict | None:
        """Most recent assembled context, for the B.5 sign-off."""
        row = (
            (
                await self._conn.execute(
                    sa.select(m.llm_call).order_by(m.llm_call.c.created_at.desc()).limit(1)
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
