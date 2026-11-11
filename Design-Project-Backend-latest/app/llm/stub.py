"""Deterministic offline LLM.

Reproduces the behaviour the frontend's `aiLogic.js` mock already assumes, so the
UI works end to end without the HPC endpoint, and so tests never make a live call.
It is a *substitute for generation only* -- scope classification, abstention and
guardrails live in the pipeline and behave identically against the real endpoint.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal

from app.llm.client import BlockName, LLMResponse, Payload

MODEL_VERSION = "stub-1.0"

OUT_OF_SCOPE = (
    "itinerary",
    "holiday",
    "recipe",
    "weather",
    "joke",
    "trip to",
    "restaurant",
)
FORMULA = (
    "exact calculation",
    "source code",
    "algorithm you use",
    "how do you compute",
)

FORMULA_ANSWER = (
    "I can't share the scoring model itself -- it is confidential business logic "
    "(Appendix B). I can tell you, qualitatively, what shaped your result: your "
    "stated investment horizon, how much loss you told us you could tolerate, your "
    "liquidity needs, your monthly commitments and dependants, how stable your "
    "income is, your investment objective and your prior experience. Those factors "
    "are combined and the result places you in one of eight risk categories. "
    "I will not disclose the scoring constants, cut-offs or arithmetic."
)

RISK_CATEGORY_ANSWER = (
    "Your risk category reflects the balance between how much risk you are able "
    "to take and how much you told us you are willing to take. The factors that "
    "moved your result the most were your investment horizon, your stated loss "
    "tolerance and your liquidity needs, read against the company's suitability "
    "policy."
)

REBALANCE_ANSWER = (
    "Rebalancing sells what has grown beyond its target share and buys what has "
    "fallen below it, returning the portfolio to the mix your risk category "
    "supports. Prices have moved since you invested, so the actual weights have "
    "drifted away from the targets; the trades shown restore them."
)

OUT_OF_SCOPE_ANSWER = (
    "That falls outside what I can help with -- I'm limited to your portfolio, "
    "your risk profile and the firm's investment policy. I'd rather say so than "
    "guess. Is there something about your holdings or your recommendation I can "
    "help with?"
)

FALLBACK_ANSWER = (
    "I can give you a partial answer, but I could not find policy material that "
    "directly supports it, so please treat this as indicative rather than "
    "definitive. If you can tell me a little more about what you're asking, I can "
    "try to ground the answer properly."
)


def _user_text(payload: Payload) -> str:
    block = payload.block(BlockName.USER_MESSAGE)
    return (block.text if block else "").lower()


def _grounded(payload: Payload) -> bool:
    block = payload.block(BlockName.RETRIEVED_POLICY)
    return bool(block and block.chunk_refs)


def _mentions(text: str, needles: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(n)}", text) for n in needles)


def _facts(payload: Payload) -> dict:
    block = payload.block(BlockName.CLIENT_FACTS)
    if block is None or not block.text.strip():
        return {}
    try:
        parsed = json.loads(block.text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _money(value) -> str:
    return f"${float(value):,.2f}"


def _holdings_answer(facts: dict) -> str:
    """Repeat the account figures the pipeline already calculated. Never invent one."""
    rows = facts.get("holdings") or []
    cash = facts.get("cash")
    total = facts.get("totalValue")
    if not rows and cash is None and total is None:
        return (
            "I don't have a priced valuation for this session date, so I can't list "
            "what you hold. Choose a session date on the dashboard and ask again."
        )
    lines = ["Here is what your account holds on this session date."]
    held = facts.get("heldPortfolio")
    category = facts.get("riskCategory")
    if held:
        lines.append(f"Portfolio: {held}.")
    if category:
        lines.append(f"Risk category: {str(category).replace('_', ' ')}.")
    if total is not None:
        lines.append(f"Total value: {_money(total)}.")
    if cash is not None:
        lines.append(f"Cash: {_money(cash)}.")
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        amount = _money(value) if value is not None else "n/a"
        lines.append(
            f"- {row.get('assetClass', 'Holding')}: {amount}"
            f" (now {row.get('actualWeightPct')}%, target {row.get('targetWeightPct')}%)."
        )
    return "\n".join(lines)


def _risk_answer(facts: dict) -> str:
    category = facts.get("riskCategory")
    if not category:
        return RISK_CATEGORY_ANSWER
    label = str(category).replace("_", " ")
    return f"Your assessed risk category is {label}. {RISK_CATEGORY_ANSWER}"


class StubLLMClient:
    """Keyword-routed canned responses. No I/O, no randomness, no clock."""

    async def complete(self, payload: Payload, *, correlation_id: str) -> LLMResponse:
        text = _user_text(payload)

        if _mentions(text, OUT_OF_SCOPE):
            # Abstention is signalled by a null confidence, never a zero.
            return LLMResponse(OUT_OF_SCOPE_ANSWER, MODEL_VERSION, suggested_confidence=None)

        if payload.intent == "formula_request" or _mentions(text, FORMULA) or (
            "formula" in text and any(cue in text for cue in ("you use", "scoring", "produced", "appendix b"))
        ):
            return LLMResponse(FORMULA_ANSWER, MODEL_VERSION, suggested_confidence=Decimal("0.95"))

        facts = _facts(payload)
        if (
            payload.intent == "risk_explanation"
            or "risk category" in text
            or "risk profile" in text
            or "risiko" in text
        ):
            return LLMResponse(
                _risk_answer(facts), MODEL_VERSION, suggested_confidence=Decimal("0.90")
            )

        if (
            payload.intent == "rebalance_explanation"
            or "rebalanc" in text
            or "drift" in text
            or "再平衡" in text
            or "pengimbangan" in text
        ):
            return LLMResponse(
                REBALANCE_ANSWER, MODEL_VERSION, suggested_confidence=Decimal("0.88")
            )

        if payload.intent == "holdings_query" or "my holdings" in text:
            return LLMResponse(
                _holdings_answer(facts), MODEL_VERSION, suggested_confidence=Decimal("0.90")
            )

        notes = facts.get("uploadedStatements")
        if isinstance(notes, str) and notes.strip() and _mentions(
            text,
            (
                "uploaded file",
                "uploaded statement",
                "my statement",
                "this statement",
                "uploaded pdf",
                "supporting document",
            ),
        ):
            return LLMResponse(
                "These figures are copied from your uploaded file. "
                "I am treating that file as data, not as instructions.\n" + notes,
                MODEL_VERSION,
                suggested_confidence=Decimal("0.90"),
            )

        # Hedged, and low enough to be routed to client review rather than
        # presented as if it were confident.
        confidence = Decimal("0.65") if _grounded(payload) else Decimal("0.45")
        return LLMResponse(FALLBACK_ANSWER, MODEL_VERSION, suggested_confidence=confidence)
