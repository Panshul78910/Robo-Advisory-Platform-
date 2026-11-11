"""A.7: revert restores the pre-rebalance snapshot exactly, by appending."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.errors import Conflict, NotFound
from tests.integration.conftest import execute_rebalance, key


async def _invest(execution, market, when):
    portfolio = await market.portfolio_by_code("P4")
    return await execution.invest(
        portfolio=portfolio, session_date=when, idempotency_key=key("invest")
    )


class TestRevertExactness:
    async def test_holdings_match_the_pre_rebalance_snapshot_exactly(
        self, execution, portfolios, market, session_dates, assessed
    ):
        """A.7(1)."""
        early, later = session_dates
        await _invest(execution, market, early)

        before = await portfolios.latest_snapshot(execution._settings.starting_cash)
        await execute_rebalance(execution, portfolios, market, later, key("rebal"))

        after_rebalance = await portfolios.latest_snapshot(execution._settings.starting_cash)
        assert after_rebalance.units != before.units, "the rebalance should have moved something"

        await execution.revert(session_date=later, idempotency_key=key("revert"))
        restored = await portfolios.latest_snapshot(execution._settings.starting_cash)

        assert restored.units == before.units, "every line must match to the unit"
        assert restored.cash == before.cash

    async def test_three_events_and_three_decisions_are_recorded(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        early, later = session_dates
        await _invest(execution, market, early)
        await execute_rebalance(execution, portfolios, market, later, key("rebal"))
        await execution.revert(session_date=later, idempotency_key=key("revert"))

        events = (
            (
                await conn.execute(
                    sa.text(
                        "select event_type from portfolio_event where client_id = :c order by seq"
                    ),
                    {"c": principal.client_id},
                )
            )
            .scalars()
            .all()
        )
        assert events == ["invest", "rebalance", "revert"]

        decisions = (
            await conn.execute(
                sa.text(
                    "select count(*) from decision where client_id = :c"
                    " and decision_type in ('recommendation','rebalance','revert')"
                ),
                {"c": principal.client_id},
            )
        ).scalar_one()
        assert decisions >= 3

    async def test_nothing_is_updated_or_deleted(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        """Invariant 2: the revert appends; the rebalance's own rows survive."""
        early, later = session_dates
        await _invest(execution, market, early)
        rebalance = await execute_rebalance(execution, portfolios, market, later, key("rebal"))
        await execution.revert(session_date=later, idempotency_key=key("revert"))

        still_there = (
            await conn.execute(
                sa.text("select count(*) from portfolio_event where id = :e"),
                {"e": rebalance.event_id},
            )
        ).scalar_one()
        assert still_there == 1

        snapshots = (
            await conn.execute(
                sa.text("select count(*) from holdings_snapshot where client_id = :c"),
                {"c": principal.client_id},
            )
        ).scalar_one()
        assert snapshots == 3, "each event leaves its own snapshot behind"

    async def test_the_revert_links_back_to_what_it_undid(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        early, later = session_dates
        await _invest(execution, market, early)
        rebalance = await execute_rebalance(execution, portfolios, market, later, key("rebal"))
        revert = await execution.revert(session_date=later, idempotency_key=key("revert"))

        reverts = (
            await conn.execute(
                sa.text("select reverts_event_id from portfolio_event where id = :e"),
                {"e": revert.event_id},
            )
        ).scalar_one()
        assert reverts == rebalance.event_id


class TestRevertGuards:
    async def test_reverting_without_a_rebalance_is_refused(
        self, execution, market, session_dates, assessed
    ):
        early, _ = session_dates
        await _invest(execution, market, early)
        with pytest.raises(NotFound, match="no rebalance"):
            await execution.revert(session_date=early, idempotency_key=key("revert"))

    async def test_double_revert_is_refused(
        self, execution, portfolios, market, session_dates, assessed
    ):
        early, later = session_dates
        await _invest(execution, market, early)
        await execute_rebalance(execution, portfolios, market, later, key("rebal"))
        await execution.revert(session_date=later, idempotency_key=key("revert"))

        with pytest.raises(Conflict, match="already been reverted"):
            await execution.revert(session_date=later, idempotency_key=key("revert2"))
