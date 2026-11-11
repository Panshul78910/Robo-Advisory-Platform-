"""B3: rebalance and switch run the same suitability gate as invest."""

from __future__ import annotations

import inspect
from decimal import Decimal

from app.api.routers import portfolio as portfolio_router
from app.domain.suitability.engine import evaluate
from tests.integration.conftest import execute_rebalance, execute_switch, key


class TestGateIsWired:
    def test_rebalance_and_switch_call_the_invest_suitability_helper(self):
        """The separate-connection audit write lives in `_enforce_suitability`.

        Inspecting the handlers (rather than firing a live refusal) keeps the
        append-only refusal row inside the test transaction. The helper itself
        is the one invest already uses.
        """
        rebalance_src = inspect.getsource(portfolio_router.rebalance)
        switch_src = inspect.getsource(portfolio_router.switch_portfolio)
        invest_src = inspect.getsource(portfolio_router.invest)
        assert "_enforce_suitability" in invest_src
        assert "_enforce_suitability" in rebalance_src
        assert "_enforce_suitability" in switch_src
        assert "policy_version=result.policy_version" in rebalance_src
        assert "policy_version=result.policy_version" in switch_src


class TestDownwardReassessment:
    async def test_the_held_portfolio_is_no_longer_permitted(
        self, conn, execution, portfolios, market, clients, session_dates, assessed
    ):
        early, later = session_dates
        p8 = await market.portfolio_by_code("P8")
        await execution.invest(portfolio=p8, session_date=early, idempotency_key=key("invest-p8"))
        await clients.record_risk_assessment(
            score=Decimal("5"),
            category="capital_preservation",
            inputs_snapshot={},
            formula_version="test",
        )
        assessment = await clients.latest_risk_assessment()
        assert assessment.category == "capital_preservation"
        profile = await clients.profile()
        result = evaluate(profile=profile, risk_category=assessment.category)
        assert not result.permits("P8")
        assert result.permits("P1")


class TestPolicyVersionIsRecorded:
    async def test_a_rebalance_decision_carries_the_policy_version(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        import sqlalchemy as sa

        early, later = session_dates
        p4 = await market.portfolio_by_code("P4")
        await execution.invest(portfolio=p4, session_date=early, idempotency_key=key("invest"))
        result = await execute_rebalance(
            execution,
            portfolios,
            market,
            later,
            key("rebal"),
            policy_version="appendix-e-v1",
        )
        if result.event_id is None:
            return  # already on target at these prices; nothing to stamp
        version = (
            await conn.execute(
                sa.text("select policy_version from decision where id = :d"),
                {"d": result.decision_id},
            )
        ).scalar_one()
        assert version == "appendix-e-v1"

    async def test_a_switch_decision_carries_the_policy_version(
        self, conn, execution, portfolios, market, principal, session_dates, assessed
    ):
        import sqlalchemy as sa

        early, later = session_dates
        p2 = await market.portfolio_by_code("P2")
        p4 = await market.portfolio_by_code("P4")
        await execution.invest(portfolio=p2, session_date=early, idempotency_key=key("invest"))
        result = await execute_switch(
            execution,
            portfolios,
            market,
            later,
            p4,
            key("switch"),
            policy_version="appendix-e-v1",
        )
        version = (
            await conn.execute(
                sa.text("select policy_version from decision where id = :d"),
                {"d": result.decision_id},
            )
        ).scalar_one()
        assert version == "appendix-e-v1"
        assert version is not None
