"""B1: a client can move from one model portfolio to another."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.core.errors import Conflict
from app.core.money import ZERO
from app.domain.trading.arithmetic import actual_weights, value_holdings
from tests.integration.conftest import execute_switch, key


async def _invest(execution, market, when, code: str = "P2"):
    portfolio = await market.portfolio_by_code(code)
    return await execution.invest(
        portfolio=portfolio, session_date=when, idempotency_key=key(f"invest-{code}")
    )


class TestSwitch:
    async def test_holdings_match_the_destination_targets(
        self, execution, portfolios, market, session_dates, assessed
    ):
        early, later = session_dates
        await _invest(execution, market, early, "P2")
        dest = await market.portfolio_by_code("P4")
        await execute_switch(execution, portfolios, market, later, dest, key("switch"))

        assert await portfolios.current_portfolio_id() == dest.id

        snapshot = await portfolios.latest_snapshot(execution._settings.starting_cash)
        prices = await market.prices_on(later, set(snapshot.units) | set(dest.targets))
        weights = actual_weights(value_holdings(snapshot.units, prices), snapshot.cash)
        for iid, target in dest.targets.items():
            if target == ZERO:
                continue
            assert abs(weights.get(iid, ZERO) - target) <= 1, f"{iid} off P4 target"

        leftover = set(snapshot.units) - set(dest.targets)
        assert not leftover, "instruments P4 does not target must be fully sold"

    async def test_a_switch_event_and_linked_decision_are_recorded(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        early, later = session_dates
        await _invest(execution, market, early, "P2")
        dest = await market.portfolio_by_code("P4")
        result = await execute_switch(
            execution,
            portfolios,
            market,
            later,
            dest,
            key("switch"),
            policy_version="appendix-e-v1",
        )

        assert result.event_type == "switch"
        assert result.portfolio_code == "P4"
        assert result.decision_id is not None

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
        assert events == ["invest", "switch"]

        row = (
            (
                await conn.execute(
                    sa.text("select decision_type, policy_version from decision where id = :d"),
                    {"d": result.decision_id},
                )
            )
            .mappings()
            .one()
        )
        assert row["decision_type"] == "rebalance"
        assert row["policy_version"] == "appendix-e-v1"

    async def test_invest_is_still_refused_once_a_position_exists(
        self, execution, market, session_dates, assessed
    ):
        early, _ = session_dates
        await _invest(execution, market, early, "P2")
        other = await market.portfolio_by_code("P4")
        with pytest.raises(Conflict, match="switch"):
            await execution.invest(
                portfolio=other, session_date=early, idempotency_key=key("invest-again")
            )
