"""Positions and portfolio events. Append-only (invariant 2).

The client's position is the newest `holdings_snapshot`. Nothing is ever updated
in place, so the whole history -- including what a revert undid -- stays readable.
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa

from app.db import models as m
from app.db.repositories.base import ClientScopedRepository


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: uuid.UUID | None
    event_id: uuid.UUID | None
    seq: int
    cash: Decimal
    units: dict[uuid.UUID, Decimal] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.units


@dataclass(frozen=True, slots=True)
class PortfolioEvent:
    id: uuid.UUID
    seq: int
    event_type: str
    session_date: _dt.date
    portfolio_id: uuid.UUID | None
    reverts_event_id: uuid.UUID | None
    decision_id: uuid.UUID | None
    created_at: _dt.datetime


class PortfolioRepository(ClientScopedRepository):
    async def lock_latest_snapshot(self, starting_cash: Decimal) -> Snapshot:
        """Serialise concurrent trades for this client, then read the position.

        The lock is taken on `client` rather than on the snapshot: a client with
        no snapshot yet still needs mutual exclusion for their first invest.
        """
        await self._conn.execute(
            sa.text("select 1 from client where id = :cid for update"), {"cid": self._client_id}
        )
        return await self.latest_snapshot(starting_cash)

    async def latest_snapshot(self, starting_cash: Decimal) -> Snapshot:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.holdings_snapshot)
                    .where(m.holdings_snapshot.c.client_id == self._client_id)
                    .order_by(m.holdings_snapshot.c.seq.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return Snapshot(id=None, event_id=None, seq=0, cash=starting_cash, units={})
        return Snapshot(
            id=row["id"],
            event_id=row["event_id"],
            seq=row["seq"],
            cash=Decimal(row["cash"]),
            units=await self._lines(row["id"]),
        )

    async def snapshot_before_event(self, event_seq: int) -> Snapshot | None:
        """The position as it stood immediately before `event_seq` (A.7 revert)."""
        row = (
            (
                await self._conn.execute(
                    sa.select(m.holdings_snapshot)
                    .where(m.holdings_snapshot.c.client_id == self._client_id)
                    .where(m.holdings_snapshot.c.seq < event_seq)
                    .order_by(m.holdings_snapshot.c.seq.desc())
                    .limit(1)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return Snapshot(
            id=row["id"],
            event_id=row["event_id"],
            seq=row["seq"],
            cash=Decimal(row["cash"]),
            units=await self._lines(row["id"]),
        )

    async def _lines(self, snapshot_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
        rows = (
            await self._conn.execute(
                sa.select(
                    m.holdings_snapshot_line.c.instrument_id, m.holdings_snapshot_line.c.units
                ).where(m.holdings_snapshot_line.c.snapshot_id == snapshot_id)
            )
        ).all()
        return {iid: Decimal(u) for iid, u in rows}

    async def latest_event(self, event_type: str | None = None) -> PortfolioEvent | None:
        stmt = (
            sa.select(m.portfolio_event)
            .where(m.portfolio_event.c.client_id == self._client_id)
            .order_by(m.portfolio_event.c.seq.desc())
            .limit(1)
        )
        if event_type is not None:
            stmt = stmt.where(m.portfolio_event.c.event_type == event_type)
        row = (await self._conn.execute(stmt)).mappings().first()
        return PortfolioEvent(**{k: row[k] for k in PortfolioEvent.__slots__}) if row else None

    async def events(self, limit: int | None = None) -> list[PortfolioEvent]:
        stmt = (
            sa.select(m.portfolio_event)
            .where(m.portfolio_event.c.client_id == self._client_id)
            .order_by(m.portfolio_event.c.seq.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await self._conn.execute(stmt)).mappings().all()
        return [PortfolioEvent(**{k: r[k] for k in PortfolioEvent.__slots__}) for r in rows]

    async def current_portfolio_id(self) -> uuid.UUID | None:
        """The model portfolio the position is measured against.

        A revert carries no portfolio of its own, so the most recent event that
        names one wins.
        """
        return (
            await self._conn.execute(
                sa.select(m.portfolio_event.c.portfolio_id)
                .where(m.portfolio_event.c.client_id == self._client_id)
                .where(m.portfolio_event.c.portfolio_id.isnot(None))
                .order_by(m.portfolio_event.c.seq.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    # --- writes -------------------------------------------------------------
    async def insert_event(
        self,
        *,
        seq: int,
        event_type: str,
        session_date: _dt.date,
        portfolio_id: uuid.UUID | None,
        decision_id: uuid.UUID | None,
        reverts_event_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return (
            await self._conn.execute(
                sa.insert(m.portfolio_event)
                .values(
                    client_id=self._client_id,
                    seq=seq,
                    event_type=event_type,
                    session_date=session_date,
                    portfolio_id=portfolio_id,
                    decision_id=decision_id,
                    reverts_event_id=reverts_event_id,
                )
                .returning(m.portfolio_event.c.id)
            )
        ).scalar_one()

    async def insert_trades(self, event_id: uuid.UUID, trades: list[dict]) -> None:
        if trades:
            await self._conn.execute(
                sa.insert(m.trade), [{"event_id": event_id, **t} for t in trades]
            )

    async def insert_snapshot(
        self, *, event_id: uuid.UUID, seq: int, cash: Decimal, units: dict[uuid.UUID, Decimal]
    ) -> uuid.UUID:
        snapshot_id = (
            await self._conn.execute(
                sa.insert(m.holdings_snapshot)
                .values(event_id=event_id, client_id=self._client_id, seq=seq, cash=cash)
                .returning(m.holdings_snapshot.c.id)
            )
        ).scalar_one()
        lines = [
            {"snapshot_id": snapshot_id, "instrument_id": iid, "units": u}
            for iid, u in units.items()
            if u != 0
        ]
        if lines:
            await self._conn.execute(sa.insert(m.holdings_snapshot_line), lines)
        return snapshot_id

    async def decision_display_no(self, decision_id: uuid.UUID) -> str | None:
        """The client's own decision reference, or None if it is not theirs."""
        return (
            await self._conn.execute(
                sa.select(m.decision.c.display_no)
                .where(m.decision.c.id == decision_id)
                .where(m.decision.c.client_id == self._client_id)
            )
        ).scalar_one_or_none()

    async def trades_for_event(self, event_id: uuid.UUID) -> list[dict]:
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.trade)
                    .join(m.portfolio_event, m.portfolio_event.c.id == m.trade.c.event_id)
                    .where(m.trade.c.event_id == event_id)
                    .where(m.portfolio_event.c.client_id == self._client_id)
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    # --- idempotency (B.6: a retry must not place a second order) -----------
    async def claim_idempotency_key(
        self, key: str, endpoint: str, request_hash: str
    ) -> dict | None:
        """Claim `key`, or return the first call's stored response if already used.

        `on conflict do nothing` makes the claim atomic; a concurrent duplicate
        loses the race and reads the winner's response rather than trading again.
        """
        claimed = (
            await self._conn.execute(
                sa.text(
                    "insert into idempotency_key (key, client_id, endpoint, request_hash)"
                    " values (:k, :cid, :ep, :h) on conflict (key) do nothing returning key"
                ),
                {"k": key, "cid": self._client_id, "ep": endpoint, "h": request_hash},
            )
        ).scalar_one_or_none()
        if claimed is not None:
            return None
        row = (
            (
                await self._conn.execute(
                    sa.select(m.idempotency_key.c.response_json, m.idempotency_key.c.request_hash)
                    .where(m.idempotency_key.c.key == key)
                    .where(m.idempotency_key.c.client_id == self._client_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            # The key exists but belongs to a different client -- treat as taken.
            return {"__conflict__": True}
        if row["request_hash"] != request_hash:
            return {"__conflict__": True}
        return row["response_json"] or {}

    async def store_idempotent_response(self, key: str, response: dict) -> None:
        await self._conn.execute(
            sa.update(m.idempotency_key)
            .where(m.idempotency_key.c.key == key)
            .where(m.idempotency_key.c.client_id == self._client_id)
            .values(response_json=json.loads(json.dumps(response, default=str)))
        )
