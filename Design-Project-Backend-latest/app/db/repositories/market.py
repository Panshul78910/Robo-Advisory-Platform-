"""Reference data: instruments, model portfolios, prices.

Not client-scoped -- none of it is client data. Prices always resolve against an
explicit session date (invariant 4); there is no "latest price" accessor.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import NotFound, PriceUnavailable
from app.db import models as m


@dataclass(frozen=True, slots=True)
class Instrument:
    id: uuid.UUID
    symbol: str
    name: str
    asset_class: str
    is_satellite: bool


@dataclass(frozen=True, slots=True)
class ModelPortfolio:
    id: uuid.UUID
    code: str
    name: str
    risk_category: str
    requires_cka: bool
    targets: dict[uuid.UUID, Decimal]  # instrument id -> percentage weight


class MarketRepository:
    def __init__(self, conn: AsyncConnection):
        self._conn = conn

    async def instruments(self) -> dict[uuid.UUID, Instrument]:
        rows = (await self._conn.execute(sa.select(m.instrument))).mappings().all()
        return {r["id"]: Instrument(**dict(r)) for r in rows}

    async def portfolio_by_code(self, code: str) -> ModelPortfolio:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.model_portfolio).where(m.model_portfolio.c.code == code)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"No model portfolio with code {code}.")
        return await self._with_targets(row)

    async def portfolio_by_id(self, portfolio_id: uuid.UUID) -> ModelPortfolio:
        row = (
            (
                await self._conn.execute(
                    sa.select(m.model_portfolio).where(m.model_portfolio.c.id == portfolio_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound("No such model portfolio.")
        return await self._with_targets(row)

    async def all_portfolios(self) -> list[ModelPortfolio]:
        rows = (
            (
                await self._conn.execute(
                    sa.select(m.model_portfolio).order_by(m.model_portfolio.c.code)
                )
            )
            .mappings()
            .all()
        )
        return [await self._with_targets(r) for r in rows]

    async def _with_targets(self, row) -> ModelPortfolio:
        targets = (
            await self._conn.execute(
                sa.select(
                    m.model_portfolio_target.c.instrument_id,
                    m.model_portfolio_target.c.target_weight,
                ).where(m.model_portfolio_target.c.portfolio_id == row["id"])
            )
        ).all()
        return ModelPortfolio(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            risk_category=row["risk_category"],
            requires_cka=row["requires_cka"],
            targets={iid: Decimal(w) for iid, w in targets},
        )

    async def prices_on(
        self, session_date: _dt.date, instrument_ids: set[uuid.UUID] | None = None
    ) -> dict[uuid.UUID, Decimal]:
        """Closing prices for exactly `session_date`.

        A missing price is an error, never a carry-forward or a zero: substituting
        a price would make the trade arithmetic in A.5-A.7 silently wrong.
        """
        stmt = sa.select(m.instrument_price.c.instrument_id, m.instrument_price.c.price).where(
            m.instrument_price.c.price_date == session_date
        )
        if instrument_ids is not None:
            stmt = stmt.where(m.instrument_price.c.instrument_id.in_(instrument_ids))
        rows = (await self._conn.execute(stmt)).all()
        prices = {iid: Decimal(p) for iid, p in rows}

        if instrument_ids is not None and (missing := instrument_ids - prices.keys()):
            raise PriceUnavailable(
                f"No price on {session_date.isoformat()} for {len(missing)} instrument(s).",
                detail=f"missing={sorted(str(i) for i in missing)}",
            )
        return prices

    async def price_history(
        self, instrument_ids: set[uuid.UUID], start: _dt.date, end: _dt.date
    ) -> dict[_dt.date, dict[uuid.UUID, Decimal]]:
        rows = (
            await self._conn.execute(
                sa.select(
                    m.instrument_price.c.price_date,
                    m.instrument_price.c.instrument_id,
                    m.instrument_price.c.price,
                )
                .where(m.instrument_price.c.instrument_id.in_(instrument_ids))
                .where(m.instrument_price.c.price_date.between(start, end))
                .order_by(m.instrument_price.c.price_date)
            )
        ).all()
        out: dict[_dt.date, dict[uuid.UUID, Decimal]] = {}
        for day, iid, price in rows:
            out.setdefault(day, {})[iid] = Decimal(price)
        return out

    async def available_dates(self) -> tuple[_dt.date | None, _dt.date | None]:
        row = (
            await self._conn.execute(
                sa.select(
                    sa.func.min(m.instrument_price.c.price_date),
                    sa.func.max(m.instrument_price.c.price_date),
                )
            )
        ).first()
        return (row[0], row[1]) if row else (None, None)
