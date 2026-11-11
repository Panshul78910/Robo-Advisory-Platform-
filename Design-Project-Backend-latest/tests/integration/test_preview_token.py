"""B2: confirmation is bound to the previewed trade set (C.1)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.errors import PreviewStale
from tests.integration.conftest import key, preview_token_for_rebalance


async def _invest(execution, market, when):
    portfolio = await market.portfolio_by_code("P4")
    return await execution.invest(
        portfolio=portfolio, session_date=when, idempotency_key=key("invest")
    )


class TestPreviewBinding:
    async def test_executing_at_a_different_date_than_the_preview_is_refused(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        early, later = session_dates
        await _invest(execution, market, early)
        token_at_early = await preview_token_for_rebalance(execution, portfolios, market, early)

        with pytest.raises(PreviewStale):
            await execution.rebalance(
                session_date=later,
                idempotency_key=key("rebal-stale"),
                preview_token=token_at_early,
            )

        events = (
            await conn.execute(
                sa.text("select count(*) from portfolio_event where client_id = :c"),
                {"c": principal.client_id},
            )
        ).scalar_one()
        assert events == 1, "a stale confirm must write no event"

        decisions = (
            await conn.execute(
                sa.text(
                    "select count(*) from decision where client_id = :c"
                    " and decision_type = 'rebalance'"
                ),
                {"c": principal.client_id},
            )
        ).scalar_one()
        assert decisions == 0, "a stale confirm must write no decision"

    async def test_preview_and_execute_at_the_same_date_match(
        self, execution, portfolios, market, session_dates, assessed
    ):
        from app.domain.trading.valuation import preview_rebalance

        early, later = session_dates
        await _invest(execution, market, early)
        preview = await preview_rebalance(portfolios, market, later)
        token = await preview_token_for_rebalance(execution, portfolios, market, later)
        result = await execution.rebalance(
            session_date=later,
            idempotency_key=key("rebal"),
            preview_token=token,
            policy_version="appendix-e-v1",
        )
        previewed = {(t.instrument_id, t.side, t.units, t.price) for t in preview["trades"]}
        executed = {(t.instrument_id, t.side, t.units, t.price) for t in result.trades}
        assert executed == previewed
        if preview["trades"]:
            assert result.event_id is not None
            assert result.already_on_target is False
        else:
            assert result.already_on_target is True
            assert result.event_id is None


class TestNoOpRebalance:
    async def test_rebalance_at_the_invest_date_writes_nothing(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        """B4/B5: zero material drift is not a trade and not an audit row."""
        early, _ = session_dates
        await _invest(execution, market, early)
        result = await execution.rebalance(
            session_date=early,
            idempotency_key=key("rebal-noop"),
            preview_token=await preview_token_for_rebalance(execution, portfolios, market, early),
        )
        assert result.already_on_target is True
        assert result.trades == []
        assert result.event_id is None
        events = (
            await conn.execute(
                sa.text("select count(*) from portfolio_event where client_id = :c"),
                {"c": principal.client_id},
            )
        ).scalar_one()
        assert events == 1
        decisions = (
            await conn.execute(
                sa.text(
                    "select count(*) from decision where client_id = :c"
                    " and decision_type = 'rebalance'"
                ),
                {"c": principal.client_id},
            )
        ).scalar_one()
        assert decisions == 0
