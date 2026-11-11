"""Client-owned data. Every query is filtered by the bound principal's client id."""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa

from app.core.clock import WallClock
from app.core.config import get_settings
from app.core.errors import NotFound
from app.db import models as m
from app.db.repositories.base import ClientScopedRepository

PROFILE_FIELDS = (
    "full_name",
    "date_of_birth",
    "email",
    "monthly_salary",
    "monthly_commitments",
    "dependants",
    "investment_objective",
    "investment_horizon_yrs",
    "loss_tolerance",
    "liquidity_needs",
    "income_stability",
    "investment_experience",
)


@dataclass(frozen=True, slots=True)
class ClientProfile:
    client_id: uuid.UUID
    client_code: str
    full_name: str
    date_of_birth: _dt.date | None
    email: str | None
    monthly_salary: Decimal | None
    monthly_commitments: Decimal | None
    dependants: int | None
    investment_objective: str | None
    investment_horizon_yrs: int | None
    loss_tolerance: str | None
    liquidity_needs: str | None
    income_stability: str | None
    investment_experience: str | None
    cka_passed: bool

    def missing_fields(self) -> list[str]:
        """Fields the A.2 conversation still needs before scoring can be attempted."""
        required = (
            "monthly_salary",
            "monthly_commitments",
            "dependants",
            "investment_objective",
            "investment_horizon_yrs",
            "loss_tolerance",
            "liquidity_needs",
            "income_stability",
            "investment_experience",
        )
        return [f for f in required if getattr(self, f) is None]

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields()


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    id: uuid.UUID
    score: Decimal
    category: str
    formula_version: str
    created_at: _dt.datetime


class ClientRepository(ClientScopedRepository):
    # --- profile ------------------------------------------------------------
    async def profile(self) -> ClientProfile:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.client_profile, m.client.c.client_code)
                    .join(m.client, m.client.c.id == m.client_profile.c.client_id)
                    .where(m.client_profile.c.client_id == self._client_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("No profile has been created for this account yet.")
        data = dict(row)
        data.pop("cka_passed_at", None)
        data.pop("updated_at", None)
        return ClientProfile(**data)

    async def update_profile(self, changes: dict) -> ClientProfile:
        changes = {k: v for k, v in changes.items() if k in PROFILE_FIELDS}
        if changes:
            # The prior state is archived before the edit so A.1(2) can show a
            # salary change as a before/after rather than only an after.
            await self._archive_profile()
            await self._conn.execute(
                sa.update(m.client_profile)
                .where(m.client_profile.c.client_id == self._client_id)
                .values(**changes, updated_at=WallClock.now())
            )
        return await self.profile()

    async def _archive_profile(self) -> None:
        await self._conn.execute(
            sa.text(
                "insert into client_profile_history (client_id, snapshot, changed_by)"
                " select client_id, to_jsonb(client_profile) - 'client_id', :actor"
                " from client_profile where client_id = :cid"
            ),
            {"cid": self._client_id, "actor": self._principal.user_id},
        )

    async def pass_cka(self) -> None:
        await self._archive_profile()
        await self._conn.execute(
            sa.update(m.client_profile)
            .where(m.client_profile.c.client_id == self._client_id)
            .values(cka_passed=True, cka_passed_at=WallClock.now(), updated_at=WallClock.now())
        )

    async def add_document(
        self, *, storage_path: str, filename: str, mime_type: str | None, size_bytes: int
    ) -> uuid.UUID:
        return (
            await self._conn.execute(
                sa.insert(m.client_document)
                .values(
                    client_id=self._client_id,
                    storage_path=storage_path,
                    filename=filename,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                )
                .returning(m.client_document.c.id)
            )
        ).scalar_one()

    async def documents(self) -> list[dict]:
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.client_document)
                    .where(m.client_document.c.client_id == self._client_id)
                    .order_by(m.client_document.c.uploaded_at.desc())
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # --- risk ---------------------------------------------------------------
    async def latest_risk_assessment(self) -> RiskAssessment | None:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.risk_assessment)
                    .where(m.risk_assessment.c.client_id == self._client_id)
                    .order_by(
                        m.risk_assessment.c.created_at.desc(),
                        m.risk_assessment.c.id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return RiskAssessment(
            id=row["id"],
            score=Decimal(row["score"]),
            category=row["category"],
            formula_version=row["formula_version"],
            created_at=row["created_at"],
        )

    async def record_risk_assessment(
        self,
        *,
        score: Decimal,
        category: str,
        inputs_snapshot: dict,
        formula_version: str,
        conversation_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return (
            await self._conn.execute(
                sa.insert(m.risk_assessment)
                .values(
                    client_id=self._client_id,
                    score=score,
                    category=category,
                    inputs_snapshot=inputs_snapshot,
                    formula_version=formula_version,
                    conversation_id=conversation_id,
                    # now() is the transaction start, so two assessments in one
                    # request would tie and `latest` could return the earlier one.
                    created_at=sa.func.clock_timestamp(),
                )
                .returning(m.risk_assessment.c.id)
            )
        ).scalar_one()

    # --- conversations ------------------------------------------------------
    async def open_conversation(self, kind: str) -> uuid.UUID:
        """The client's current session of this kind, created when none is active.

        Continuity within a session is load-bearing, in two places: the profiling
        interview keeps what it is `awaiting` in `conversation.state` (A.2), and
        the ingress judge reads prior turns to catch a probe spread across several
        of them (D.4(2)) -- "how is my category decided" through to "what share of
        the total, as a percentage" is only an attack when read together.

        What this used to lack was any notion of a session *ending*. "Most recent"
        meant most recent ever, so one conversation accumulated for days: a client
        who asked something off-topic on Tuesday carried it into Thursday, and the
        judge -- correctly, given what it was shown -- treated an ordinary question
        as suspicious because of turns from another sitting. Idle time ends the
        session instead.

        Activity is measured from the last *message*, not `updated_at`: only
        `save_conversation_state` touches that column, so an advisory conversation
        that has been busy all afternoon still carries its creation timestamp.
        """
        idle_cutoff = WallClock.now() - _dt.timedelta(
            minutes=get_settings().conversation_idle_minutes
        )
        last_message_at = (
            sa.select(sa.func.max(m.message.c.created_at))
            .where(m.message.c.conversation_id == m.conversation.c.id)
            .correlate(m.conversation)
            .scalar_subquery()
        )
        last_active = sa.func.greatest(
            m.conversation.c.updated_at,
            sa.func.coalesce(last_message_at, m.conversation.c.created_at),
        )
        existing = (
            await self._conn.execute(
                sa.select(m.conversation.c.id)
                .where(m.conversation.c.client_id == self._client_id)
                .where(m.conversation.c.kind == kind)
                .where(last_active > idle_cutoff)
                .order_by(m.conversation.c.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        return (
            await self._conn.execute(
                sa.insert(m.conversation)
                .values(client_id=self._client_id, kind=kind, state={})
                .returning(m.conversation.c.id)
            )
        ).scalar_one()

    async def conversation_state(self, conversation_id: uuid.UUID) -> dict:
        row = (
            await self._conn.execute(
                sa.select(m.conversation.c.state)
                .where(m.conversation.c.id == conversation_id)
                .where(m.conversation.c.client_id == self._client_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound("Conversation not found.")
        return dict(row)

    async def list_conversations(self) -> list[dict]:
        """This client's threads only — another login cannot see them (A.1(4))."""
        rows = (
            (
                await self._conn.execute(
                    sa.text(
                        """
                    select c.id, c.kind, c.created_at, c.updated_at,
                           (select count(*) from message m
                             where m.conversation_id = c.id) as message_count,
                           (select m.content from message m
                             where m.conversation_id = c.id
                             order by m.seq desc limit 1) as last_message
                      from conversation c
                     where c.client_id = :cid
                     order by c.updated_at desc nulls last, c.created_at desc
                    """
                    ),
                    {"cid": self._client_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def save_conversation_state(self, conversation_id: uuid.UUID, state: dict) -> None:
        await self._conn.execute(
            sa.update(m.conversation)
            .where(m.conversation.c.id == conversation_id)
            .where(m.conversation.c.client_id == self._client_id)
            .values(state=state, updated_at=WallClock.now())
        )

    async def history(self, conversation_id: uuid.UUID, limit: int = 20) -> list[dict]:
        """Oldest-first tail of the conversation, scoped to this client."""
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.message.c.role, m.message.c.content, m.message.c.seq)
                    .select_from(
                        m.message.join(
                            m.conversation, m.conversation.c.id == m.message.c.conversation_id
                        )
                    )
                    .where(m.message.c.conversation_id == conversation_id)
                    .where(m.conversation.c.client_id == self._client_id)
                    .order_by(m.message.c.seq.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in reversed(rows)]

    async def append_message(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        decision_id: uuid.UUID | None = None,
    ) -> None:
        await self._conn.execute(
            sa.text(
                "insert into message (conversation_id, seq, role, content, decision_id)"
                " select :cid,"
                "        coalesce((select max(seq) from message"
                "                   where conversation_id = :cid), 0) + 1,"
                "        :role, :content, :did"
                " where exists (select 1 from conversation"
                "               where id = :cid and client_id = :client)"
            ),
            {
                "cid": conversation_id,
                "role": role,
                "content": content,
                "did": decision_id,
                "client": self._client_id,
            },
        )

    # --- audit (client's own trail) ----------------------------------------
    async def decisions(self, limit: int = 100) -> list[dict]:
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.decision)
                    .where(m.decision.c.client_id == self._client_id)
                    .order_by(m.decision.c.created_at.desc())
                    .limit(limit)
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def decision_sources(self, decision_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
        if not decision_ids:
            return {}
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.decision_source)
                    .join(m.decision, m.decision.c.id == m.decision_source.c.decision_id)
                    .where(m.decision_source.c.decision_id.in_(decision_ids))
                    .where(m.decision.c.client_id == self._client_id)
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
                    sa.select(m.decision_override)
                    .join(m.decision, m.decision.c.id == m.decision_override.c.decision_id)
                    .where(m.decision_override.c.decision_id.in_(decision_ids))
                    .where(m.decision.c.client_id == self._client_id)
                )
            )
            .mappings()
            .all()
        )
        return {r["decision_id"]: dict(r) for r in rows}

    async def set_session_date(self, session_date: _dt.date) -> None:
        await self._conn.execute(
            sa.insert(m.app_session).values(
                user_id=self._principal.user_id, session_date=session_date
            )
        )

    async def current_session_date(self) -> _dt.date | None:
        return (
            await self._conn.execute(
                sa.select(m.app_session.c.session_date)
                .where(m.app_session.c.user_id == self._principal.user_id)
                .order_by(m.app_session.c.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
