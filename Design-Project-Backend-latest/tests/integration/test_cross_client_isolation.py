"""A.1(4) / D.4: one client's repository can never see another's data.

`tests/unit/test_scoping.py` proves the *signatures* offer no way to ask for
another client. This proves the queries actually filter, against real rows.
"""

from __future__ import annotations

import pytest

from app.core.errors import Forbidden, NotFound
from app.db.repositories.admin import AdminReadRepository
from app.db.repositories.client import ClientRepository
from app.db.repositories.portfolio import PortfolioRepository


class TestClientIsolation:
    async def test_each_client_sees_only_their_own_profile(self, conn, principal, other_principal):
        mine = await ClientRepository(conn, principal).profile()
        theirs = await ClientRepository(conn, other_principal).profile()

        assert mine.client_id != theirs.client_id
        assert mine.full_name != theirs.full_name

    async def test_holdings_do_not_bleed_between_clients(
        self, conn, principal, other_principal, market, session_dates, assessed
    ):
        from decimal import Decimal

        from app.domain.trading.execution import ExecutionService
        from tests.integration.conftest import key

        early, _ = session_dates
        mine = PortfolioRepository(conn, principal)
        theirs = PortfolioRepository(conn, other_principal)

        service = ExecutionService(conn, mine, market, principal.client_id)
        portfolio = await market.portfolio_by_code("P4")
        await service.invest(portfolio=portfolio, session_date=early, idempotency_key=key("invest"))

        assert (await mine.latest_snapshot(Decimal("50000"))).units
        assert not (await theirs.latest_snapshot(Decimal("50000"))).units

    async def test_the_audit_trail_is_scoped(self, conn, principal, other_principal):
        from app.audit import service as audit

        await audit.record_decision(
            conn,
            client_id=principal.client_id,
            decision_type="chat_qa",
            status="completed",
            input="my question",
            output="my answer",
        )
        theirs = await ClientRepository(conn, other_principal).decisions()
        assert all(d["input"] != "my question" for d in theirs)

    async def test_conversations_are_scoped(self, conn, principal, other_principal):
        mine = ClientRepository(conn, principal)
        theirs = ClientRepository(conn, other_principal)

        conversation_id = await mine.open_conversation("advisory")
        await mine.append_message(conversation_id, "user", "a secret only I said")

        # Even handed the id, another client's repository yields nothing.
        assert await theirs.history(conversation_id) == []
        with pytest.raises(NotFound):
            await theirs.conversation_state(conversation_id)

    async def test_decision_sources_are_scoped(self, conn, principal, other_principal):
        from decimal import Decimal

        from app.audit import service as audit

        decision = await audit.record_decision(
            conn,
            client_id=principal.client_id,
            decision_type="chat_qa",
            status="completed",
            input="q",
            output="a",
            sources=[
                audit.SourceRef("C#1", "POL-001", "Policy", "3.2", 4, Decimal("0.9"), "passage")
            ],
        )
        theirs = ClientRepository(conn, other_principal)
        assert await theirs.decision_sources([decision.id]) == {}

    async def test_overrides_are_scoped(self, conn, principal, other_principal, admin_principal):
        from app.audit import service as audit

        decision = await audit.record_decision(
            conn,
            client_id=principal.client_id,
            decision_type="chat_qa",
            status="pending_review",
            input="q",
            output="a",
        )
        await audit.record_override(
            conn,
            decision_id=decision.id,
            reviewer_user_id=admin_principal.user_id,
            action="edit",
            final_output="corrected",
        )
        theirs = ClientRepository(conn, other_principal)
        assert await theirs.overrides_for([decision.id]) == {}
        mine = ClientRepository(conn, principal)
        assert decision.id in await mine.overrides_for([decision.id])


class TestRoleSeparation:
    async def test_an_admin_cannot_construct_a_client_repository(self, conn, admin_principal):
        with pytest.raises(Forbidden):
            ClientRepository(conn, admin_principal)

    async def test_a_client_cannot_construct_an_admin_repository(self, conn, principal):
        with pytest.raises(Forbidden):
            AdminReadRepository(conn, principal)

    async def test_an_admin_sees_every_client(self, conn, admin_principal):
        """A.1(5)."""
        rows = await AdminReadRepository(conn, admin_principal).clients()
        assert len(rows) >= 5
        assert {r["client_code"] for r in rows} >= {"C001", "C002", "C003", "C004", "C005"}
