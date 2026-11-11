"""B7: the fact bundle carries the ledger, not just profile fields."""

from __future__ import annotations

import inspect

from app.api.routers import chat as chat_router
from app.api.views import client_facts
from app.orchestration.context import FACT_PRIORITY
from app.orchestration.intent import NEEDS_MARKET_EVIDENCE, Intent


class _Profile:
    full_name = "Priya Sharma"
    investment_horizon_yrs = 15
    loss_tolerance = "high"
    liquidity_needs = "low"
    income_stability = "stable"
    investment_experience = "moderate"
    investment_objective = "growth"
    dependants = 0
    cka_passed = True
    monthly_salary = 8000
    monthly_commitments = 2000


def test_recent_transactions_are_in_the_bundle():
    facts = client_facts(
        profile=_Profile(),
        risk_category="very_aggressive",
        eligible=("P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"),
        held_portfolio="P2",
        valuation=None,
        recent_transactions=[
            {
                "eventType": "invest",
                "sessionDate": "2026-06-19",
                "portfolioCode": "P2",
                "decisionId": "D-1001",
                "trades": [
                    {
                        "symbol": "AGG",
                        "side": "buy",
                        "units": 10.0,
                        "price": 100.0,
                        "grossAmount": 1000.0,
                    }
                ],
            }
        ],
    )
    assert facts["recentTransactions"][0]["portfolioCode"] == "P2"
    assert facts["recentTransactions"][0]["trades"][0]["symbol"] == "AGG"


def test_recent_transactions_degrade_under_budget_pressure():
    assert "recentTransactions" in FACT_PRIORITY
    assert FACT_PRIORITY.index("recentTransactions") > FACT_PRIORITY.index("heldPortfolio")


def test_market_evidence_intents_include_the_ledger():
    assert Intent.TRANSACTION_HISTORY in NEEDS_MARKET_EVIDENCE
    assert Intent.HOLDINGS_QUERY in NEEDS_MARKET_EVIDENCE


def test_the_chat_router_actually_consults_needs_market_evidence():
    src = inspect.getsource(chat_router.chat)
    assert "NEEDS_MARKET_EVIDENCE" in src
    assert "recent_transactions" in src
