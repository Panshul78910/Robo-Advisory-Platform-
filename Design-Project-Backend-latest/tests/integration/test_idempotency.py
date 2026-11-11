"""B.6: a retried transactional request must never place a second order."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.errors import Conflict
from app.domain.trading.execution import run_idempotent
from tests.integration.conftest import execute_rebalance, key, preview_token_for_rebalance


async def _count_events(conn, client_id) -> int:
    return (
        await conn.execute(
            sa.text("select count(*) from portfolio_event where client_id = :c"),
            {"c": client_id},
        )
    ).scalar_one()


class TestDoublePost:
    async def test_the_same_key_replays_instead_of_trading_again(
        self, conn, execution, market, principal, session_dates, assessed
    ):
        early, _ = session_dates
        portfolio = await market.portfolio_by_code("P4")
        k = key("invest")

        first = await run_idempotent(
            execution.invest(portfolio=portfolio, session_date=early, idempotency_key=k)
        )
        second = await run_idempotent(
            execution.invest(portfolio=portfolio, session_date=early, idempotency_key=k)
        )

        assert first == second, "a retry must return the original outcome verbatim"
        assert await _count_events(conn, principal.client_id) == 1

    async def test_a_different_key_is_a_genuinely_new_request(
        self, execution, market, session_dates, assessed
    ):
        early, _ = session_dates
        portfolio = await market.portfolio_by_code("P4")
        await execution.invest(portfolio=portfolio, session_date=early, idempotency_key=key("a"))
        # A second invest is refused on its own merits, not silently replayed.
        with pytest.raises(Conflict, match="already hold"):
            await execution.invest(
                portfolio=portfolio, session_date=early, idempotency_key=key("b")
            )

    async def test_reusing_a_key_for_a_different_request_is_rejected(
        self, execution, portfolios, market, session_dates, assessed
    ):
        """Silently replaying the old answer would hide the client's real intent."""
        early, later = session_dates
        portfolio = await market.portfolio_by_code("P4")
        k = key("shared")

        await execution.invest(portfolio=portfolio, session_date=early, idempotency_key=k)
        token = await preview_token_for_rebalance(execution, portfolios, market, later)
        with pytest.raises(Conflict, match="different request"):
            await execution.rebalance(session_date=later, idempotency_key=k, preview_token=token)

    async def test_rebalance_retry_does_not_trade_twice(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        early, later = session_dates
        portfolio = await market.portfolio_by_code("P4")
        await execution.invest(
            portfolio=portfolio, session_date=early, idempotency_key=key("invest")
        )

        k = key("rebal")
        token = await preview_token_for_rebalance(execution, portfolios, market, later)
        first = await run_idempotent(
            execution.rebalance(session_date=later, idempotency_key=k, preview_token=token)
        )
        second = await run_idempotent(
            execution.rebalance(session_date=later, idempotency_key=k, preview_token=token)
        )

        assert first == second
        assert await _count_events(conn, principal.client_id) == 2

    async def test_revert_retry_does_not_revert_twice(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        early, later = session_dates
        portfolio = await market.portfolio_by_code("P4")
        await execution.invest(
            portfolio=portfolio, session_date=early, idempotency_key=key("invest")
        )
        await execute_rebalance(execution, portfolios, market, later, key("rebal"))

        k = key("revert")
        first = await run_idempotent(execution.revert(session_date=later, idempotency_key=k))
        second = await run_idempotent(execution.revert(session_date=later, idempotency_key=k))

        assert first == second
        assert await _count_events(conn, principal.client_id) == 3
