"""Invest, rebalance, switch and revert (A.5, A.6, A.7).

Each runs as one transaction containing the position change *and* its decision
row, so the audit log can never disagree with the ledger. Nothing here calls the
LLM: a model outage leaves all four working, which is what the B.6 degradation
ladder rests on.

Idempotency is enforced before any trade is computed, so a retried request
returns the first execution's result rather than placing a second order.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncConnection

from app.audit import service as audit
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, PreviewStale, ValidationError
from app.core.money import round_money, to_json_number
from app.db.repositories.market import MarketRepository, ModelPortfolio
from app.db.repositories.portfolio import PortfolioRepository, Snapshot
from app.domain.trading.arithmetic import (
    Trade,
    apply_trades,
    invest_trades,
    rebalance_trades,
)
from app.domain.trading.valuation import current_portfolio


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    event_id: uuid.UUID | None
    event_type: str
    decision_id: uuid.UUID | None
    decision_no: str | None
    session_date: _dt.date
    portfolio_code: str | None
    trades: list[Trade]
    cash: Decimal
    units: dict[uuid.UUID, Decimal]
    already_on_target: bool = False

    def to_json(self) -> dict:
        """Stored against the idempotency key and returned to a retry verbatim."""
        return {
            "eventId": str(self.event_id) if self.event_id else None,
            "eventType": self.event_type,
            "decisionId": self.decision_no,
            "sessionDate": self.session_date.isoformat(),
            "portfolioCode": self.portfolio_code,
            "cash": to_json_number(self.cash),
            "alreadyOnTarget": self.already_on_target,
            "trades": [
                {
                    "instrumentId": str(t.instrument_id),
                    "side": t.side,
                    "units": to_json_number(t.units),
                    "price": to_json_number(t.price),
                    "grossAmount": to_json_number(t.gross_amount),
                }
                for t in self.trades
            ],
        }


def request_hash(**parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def compute_preview_token(
    *,
    client_id: uuid.UUID,
    snapshot_seq: int,
    session_date: _dt.date,
    portfolio_code: str,
    trades: list[Trade],
) -> str:
    """Bind a confirmation to the exact trade set that was previewed (C.1).

    Hash covers the client, the snapshot the preview was computed against, the
    session date's prices, the target portfolio, and a canonical trade list.
    Recomputing server-side and comparing is what stops a stale on-screen
    preview from authorizing a different execution.
    """
    canonical = [
        {
            "instrumentId": str(t.instrument_id),
            "side": t.side,
            "units": str(t.units),
            "price": str(t.price),
            "grossAmount": str(t.gross_amount),
        }
        for t in sorted(trades, key=lambda t: (str(t.instrument_id), t.side))
    ]
    return request_hash(
        client_id=str(client_id),
        snapshot_seq=snapshot_seq,
        session_date=session_date.isoformat(),
        portfolio_code=portfolio_code,
        trades=canonical,
    )


class ExecutionService:
    def __init__(
        self,
        conn: AsyncConnection,
        portfolios: PortfolioRepository,
        market: MarketRepository,
        client_id: uuid.UUID,
    ):
        self._conn = conn
        self._repo = portfolios
        self._market = market
        self._client_id = client_id
        self._settings = get_settings()

    def _rebalance_kwargs(self, *, band_pct: Decimal | None = None) -> dict:
        return {
            "band_pct": (self._settings.rebalance_band_pct if band_pct is None else band_pct),
            "min_notional": self._settings.min_trade_notional,
        }

    # --- A.5 ---------------------------------------------------------------
    async def invest(
        self,
        *,
        portfolio: ModelPortfolio,
        session_date: _dt.date,
        idempotency_key: str,
        policy_version: str | None = None,
    ) -> ExecutionResult:
        digest = request_hash(op="invest", portfolio=portfolio.code, date=session_date)
        await self._claim(idempotency_key, "invest", digest)

        snapshot = await self._repo.lock_latest_snapshot(self._settings.starting_cash)
        if not snapshot.is_empty:
            raise Conflict(
                "You already hold a portfolio. Use switch to move to a different "
                "model portfolio, or rebalance to restore this one's target weights."
            )

        prices = await self._market.prices_on(session_date, set(portfolio.targets))
        trades = invest_trades(snapshot.cash, portfolio.targets, prices)
        return await self._commit(
            snapshot=snapshot,
            trades=trades,
            event_type="invest",
            session_date=session_date,
            portfolio=portfolio,
            idempotency_key=idempotency_key,
            decision_type="recommendation",
            summary=(
                f"Invested {snapshot.cash} into {portfolio.code} ({portfolio.name}) "
                f"at {session_date.isoformat()} prices."
            ),
            policy_version=policy_version,
        )

    # --- A.6 ---------------------------------------------------------------
    async def rebalance(
        self,
        *,
        session_date: _dt.date,
        idempotency_key: str,
        preview_token: str,
        policy_version: str | None = None,
    ) -> ExecutionResult:
        digest = request_hash(op="rebalance", date=session_date, preview_token=preview_token)
        await self._claim(idempotency_key, "rebalance", digest)

        snapshot = await self._repo.lock_latest_snapshot(self._settings.starting_cash)
        portfolio = await self._require_current_portfolio(snapshot)
        prices = await self._market.prices_on(
            session_date, set(portfolio.targets) | set(snapshot.units)
        )
        trades = rebalance_trades(
            snapshot.units, prices, portfolio.targets, snapshot.cash, **self._rebalance_kwargs()
        )
        self._require_preview_token(
            snapshot=snapshot,
            session_date=session_date,
            portfolio_code=portfolio.code,
            trades=trades,
            preview_token=preview_token,
        )
        if not trades:
            return await self._store_noop(
                snapshot=snapshot,
                session_date=session_date,
                portfolio=portfolio,
                idempotency_key=idempotency_key,
            )
        return await self._commit(
            snapshot=snapshot,
            trades=trades,
            event_type="rebalance",
            session_date=session_date,
            portfolio=portfolio,
            idempotency_key=idempotency_key,
            decision_type="rebalance",
            summary=(
                f"Rebalanced {portfolio.code} back to target weights at "
                f"{session_date.isoformat()} prices; {len(trades)} trade(s)."
            ),
            policy_version=policy_version,
        )

    async def switch(
        self,
        *,
        portfolio: ModelPortfolio,
        session_date: _dt.date,
        idempotency_key: str,
        preview_token: str,
        policy_version: str | None = None,
    ) -> ExecutionResult:
        """Move an existing position onto a different model portfolio.

        Trades are the same arithmetic as a rebalance, pointed at the *new*
        targets, so instruments the destination does not hold are fully sold.
        The band is zero: a switch is a change of mandate, not a drift trim.
        Dust notionals are still dropped.
        """
        digest = request_hash(
            op="switch",
            portfolio=portfolio.code,
            date=session_date,
            preview_token=preview_token,
        )
        await self._claim(idempotency_key, "switch", digest)

        snapshot = await self._repo.lock_latest_snapshot(self._settings.starting_cash)
        current = await self._require_current_portfolio(snapshot)
        if current.code == portfolio.code:
            raise Conflict(
                f"You already hold {portfolio.code}. Use rebalance to restore its target weights."
            )

        prices = await self._market.prices_on(
            session_date, set(portfolio.targets) | set(snapshot.units)
        )
        trades = rebalance_trades(
            snapshot.units,
            prices,
            portfolio.targets,
            snapshot.cash,
            **self._rebalance_kwargs(band_pct=Decimal("0")),
        )
        self._require_preview_token(
            snapshot=snapshot,
            session_date=session_date,
            portfolio_code=portfolio.code,
            trades=trades,
            preview_token=preview_token,
        )
        return await self._commit(
            snapshot=snapshot,
            trades=trades,
            event_type="switch",
            session_date=session_date,
            portfolio=portfolio,
            idempotency_key=idempotency_key,
            decision_type="rebalance",
            summary=(
                f"Switched from {current.code} to {portfolio.code} ({portfolio.name}) "
                f"at {session_date.isoformat()} prices; {len(trades)} trade(s)."
            ),
            policy_version=policy_version,
        )

    # --- A.7 ---------------------------------------------------------------
    async def revert(self, *, session_date: _dt.date, idempotency_key: str) -> ExecutionResult:
        """Undo the most recent rebalance by appending its inverse.

        Nothing is updated or deleted: a new snapshot restores the prior position
        and `reverts_event_id` links the two, so the log shows the rebalance and
        the revert as separate records (A.7 sign-off item 2). A switch is a
        different event type and is not undone here.
        """
        digest = request_hash(op="revert", date=session_date)
        await self._claim(idempotency_key, "revert", digest)

        snapshot = await self._repo.lock_latest_snapshot(self._settings.starting_cash)
        target_event = await self._repo.latest_event("rebalance")
        if target_event is None:
            raise NotFound("There is no rebalance to revert.")

        latest = await self._repo.latest_event()
        if latest is not None and latest.event_type == "revert":
            raise Conflict("The most recent rebalance has already been reverted.")

        restored = await self._repo.snapshot_before_event(target_event.seq)
        if restored is None:  # pragma: no cover - a rebalance always follows an invest
            raise NotFound("No pre-rebalance position was recorded.")

        seq = snapshot.seq + 1
        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type="revert",
            status="auto_executed",
            input=f"Revert rebalance {target_event.id}",
            output=(
                "Restored the holdings snapshot from immediately before the rebalance on "
                f"{target_event.session_date.isoformat()}."
            ),
            session_date=session_date,
        )
        event_id = await self._repo.insert_event(
            seq=seq,
            event_type="revert",
            session_date=session_date,
            portfolio_id=target_event.portfolio_id,
            decision_id=decision.id,
            reverts_event_id=target_event.id,
        )
        await self._repo.insert_snapshot(
            event_id=event_id, seq=seq, cash=restored.cash, units=restored.units
        )

        portfolio_code = None
        if target_event.portfolio_id:
            portfolio_code = (await self._market.portfolio_by_id(target_event.portfolio_id)).code

        result = ExecutionResult(
            event_id=event_id,
            event_type="revert",
            decision_id=decision.id,
            decision_no=decision.display_no,
            session_date=session_date,
            portfolio_code=portfolio_code,
            trades=[],
            cash=restored.cash,
            units=restored.units,
        )
        await self._repo.store_idempotent_response(idempotency_key, result.to_json())
        return result

    # --- shared ------------------------------------------------------------
    def _require_preview_token(
        self,
        *,
        snapshot: Snapshot,
        session_date: _dt.date,
        portfolio_code: str,
        trades: list[Trade],
        preview_token: str,
    ) -> None:
        expected = compute_preview_token(
            client_id=self._client_id,
            snapshot_seq=snapshot.seq,
            session_date=session_date,
            portfolio_code=portfolio_code,
            trades=trades,
        )
        if preview_token != expected:
            raise PreviewStale(
                "That preview is no longer current. Review the updated trades and confirm again."
            )

    async def _store_noop(
        self,
        *,
        snapshot: Snapshot,
        session_date: _dt.date,
        portfolio: ModelPortfolio,
        idempotency_key: str,
    ) -> ExecutionResult:
        """Already on target: persist the idempotent reply, write nothing else.

        An audit row claiming a rebalance happened would be a lie, and would
        leave a revert target that undoes the previous *real* rebalance.
        """
        result = ExecutionResult(
            event_id=None,
            event_type="rebalance",
            decision_id=None,
            decision_no=None,
            session_date=session_date,
            portfolio_code=portfolio.code,
            trades=[],
            cash=snapshot.cash,
            units=snapshot.units,
            already_on_target=True,
        )
        await self._repo.store_idempotent_response(idempotency_key, result.to_json())
        return result

    async def _require_current_portfolio(self, snapshot: Snapshot) -> ModelPortfolio:
        return await current_portfolio(self._repo, self._market, snapshot)

    async def _claim(self, key: str, endpoint: str, digest: str) -> None:
        """Claim the key, or abort with the first execution's stored response.

        Raising rather than returning keeps every caller a single line: there is
        no path that forgets to check and trades twice.
        """
        prior = await self._repo.claim_idempotency_key(key, endpoint, digest)
        if prior is None:
            return
        if prior.get("__conflict__"):
            raise Conflict("That idempotency key was used for a different request.")
        raise _Replay(prior)

    async def _commit(
        self,
        *,
        snapshot: Snapshot,
        trades: list[Trade],
        event_type: str,
        session_date: _dt.date,
        portfolio: ModelPortfolio,
        idempotency_key: str,
        decision_type: str,
        summary: str,
        policy_version: str | None,
    ) -> ExecutionResult:
        if any(t.gross_amount <= Decimal("0") for t in trades):
            raise ValidationError(detail="refusing to persist a zero-notional trade")
        units, cash = apply_trades(snapshot.units, snapshot.cash, trades)
        seq = snapshot.seq + 1

        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type=decision_type,
            status="auto_executed",
            input=f"{event_type} {portfolio.code} on {session_date.isoformat()}",
            output=summary,
            policy_version=policy_version,
            session_date=session_date,
        )
        event_id = await self._repo.insert_event(
            seq=seq,
            event_type=event_type,
            session_date=session_date,
            portfolio_id=portfolio.id,
            decision_id=decision.id,
        )
        await self._repo.insert_trades(
            event_id,
            [
                {
                    "instrument_id": t.instrument_id,
                    "side": t.side,
                    "units": t.units,
                    "price": t.price,
                    "gross_amount": t.gross_amount,
                }
                for t in trades
            ],
        )
        await self._repo.insert_snapshot(
            event_id=event_id, seq=seq, cash=round_money(cash), units=units
        )

        result = ExecutionResult(
            event_id=event_id,
            event_type=event_type,
            decision_id=decision.id,
            decision_no=decision.display_no,
            session_date=session_date,
            portfolio_code=portfolio.code,
            trades=trades,
            cash=round_money(cash),
            units=units,
        )
        await self._repo.store_idempotent_response(idempotency_key, result.to_json())
        return result


class _Replay(Exception):
    """Internal: carries a stored idempotent response back to the caller."""

    def __init__(self, response: dict):
        super().__init__("idempotent replay")
        self.response = response


async def run_idempotent(coro) -> dict:
    """Await an execution, returning the stored response if it was a replay.

    Keeps the replay path out of every call site while still guaranteeing a
    retried POST never trades twice.
    """
    try:
        result = await coro
    except _Replay as replay:
        return replay.response
    if isinstance(result, ExecutionResult):
        return result.to_json()
    raise ValidationError(detail="unexpected execution result")  # pragma: no cover
