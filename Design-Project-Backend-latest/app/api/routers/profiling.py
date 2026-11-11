"""The A.2 risk-profiling conversation.

Scoring happens here, in Python, from a normalised profile -- never from model
output. The conversation collects; the scorer decides.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import client_repo
from app.api.dto.common import Dto, OptMoney
from app.api.dto.requests import ProfilingMessageRequest
from app.audit import service as audit
from app.db.repositories.client import ClientRepository
from app.db.session import get_connection
from app.domain.profile.conversation import advance, opening
from app.domain.profile.validation import coherence_check
from app.domain.risk.scorer import ScoringInputs, score
from app.domain.suitability.engine import evaluate
from app.orchestration.intent import FORMULA_REPLY, Intent, classify

router = APIRouter(tags=["profiling"])

#: The factors the qualitative explanation may name. Deliberately the factor
#: *names* only -- never their weights (A.2 confidentiality, C.3).
FACTOR_LABEL = {
    "loss_tolerance": "how much loss you told us you could tolerate",
    "investment_objective": "what you said you are trying to achieve",
    "investment_horizon": "how long you plan to stay invested",
    "investment_experience": "how much investing you have done before",
    "liquidity_needs": "how likely you are to need the money at short notice",
    "income_stability": "how steady your income is",
    "surplus_ratio": "how much of your income is left after commitments",
    "dependants": "how many people depend on your income",
}


class ProfilingTurnDto(Dto):
    reply: str
    awaiting: str | None = None
    complete: bool
    missingFields: list[str]
    inconsistencies: list[dict]
    riskCategory: str | None = None
    riskScore: OptMoney = None
    explanation: str | None = None
    eligiblePortfolios: list[str] = []
    conversationId: str


async def _state(repo: ClientRepository) -> tuple[uuid.UUID, dict]:
    conversation_id = await repo.open_conversation("profiling")
    return conversation_id, await repo.conversation_state(conversation_id)


async def _respond(
    conn: AsyncConnection,
    repo: ClientRepository,
    profile,
    conversation_id: uuid.UUID,
    turn,
) -> ProfilingTurnDto:
    """Turn a conversation step into a response, scoring if it finished.

    Shared by both routes. `start` previously hardcoded `complete=False`, so a
    client whose A.1 profile was already complete -- which every seeded client
    is -- was told the conversation was unfinished while no question was
    pending, and could only progress by sending a throwaway message.
    """
    if not turn.complete:
        return ProfilingTurnDto(
            reply=turn.reply,
            awaiting=turn.awaiting,
            complete=False,
            missingFields=profile.missing_fields(),
            inconsistencies=_problems(turn),
            conversationId=str(conversation_id),
        )
    return await _score_and_record(conn, repo, profile, conversation_id, turn.reply)


@router.post("/profiling/start", response_model=ProfilingTurnDto)
async def start(
    repo: ClientRepository = Depends(client_repo),
    conn: AsyncConnection = Depends(get_connection),
) -> ProfilingTurnDto:
    conversation_id, _ = await _state(repo)
    profile = await repo.profile()
    turn = opening(profile)

    await repo.save_conversation_state(conversation_id, {"awaiting": turn.awaiting})
    await repo.append_message(conversation_id, "assistant", turn.reply)
    return await _respond(conn, repo, profile, conversation_id, turn)


@router.post("/profiling/message", response_model=ProfilingTurnDto)
async def message(
    body: ProfilingMessageRequest,
    repo: ClientRepository = Depends(client_repo),
    conn: AsyncConnection = Depends(get_connection),
) -> ProfilingTurnDto:
    conversation_id, state = await _state(repo)
    profile = await repo.profile()

    await repo.append_message(conversation_id, "user", body.message)
    if classify(body.message).intent is Intent.FORMULA_REQUEST:
        await repo.append_message(conversation_id, "assistant", FORMULA_REPLY)
        return ProfilingTurnDto(
            reply=FORMULA_REPLY,
            awaiting=state.get("awaiting"),
            complete=False,
            missingFields=profile.missing_fields(),
            inconsistencies=_problems_from_profile(profile),
            conversationId=str(conversation_id),
        )
    turn = advance(profile, awaiting=state.get("awaiting"), message=body.message)

    if turn.captured:
        profile = await repo.update_profile(turn.captured)
    await repo.save_conversation_state(conversation_id, {"awaiting": turn.awaiting})
    await repo.append_message(conversation_id, "assistant", turn.reply)

    return await _respond(conn, repo, profile, conversation_id, turn)


async def _score_and_record(
    conn: AsyncConnection,
    repo: ClientRepository,
    profile,
    conversation_id: uuid.UUID,
    reply: str,
) -> ProfilingTurnDto:
    inputs = ScoringInputs(
        monthly_salary=profile.monthly_salary,
        monthly_commitments=profile.monthly_commitments,
        dependants=profile.dependants,
        investment_objective=profile.investment_objective,
        investment_horizon_yrs=profile.investment_horizon_yrs,
        loss_tolerance=profile.loss_tolerance,
        liquidity_needs=profile.liquidity_needs,
        income_stability=profile.income_stability,
        investment_experience=profile.investment_experience,
    )
    result = score(inputs)
    await repo.record_risk_assessment(
        score=result.score,
        category=result.category,
        inputs_snapshot=inputs.as_snapshot(),
        formula_version=result.formula_version,
        conversation_id=conversation_id,
    )

    suitability = evaluate(profile=profile, risk_category=result.category)
    explanation = _explain(result, suitability)

    await audit.record_decision(
        conn,
        client_id=profile.client_id,
        decision_type="risk_assessment",
        status="completed",
        input="Completed risk profiling conversation",
        output=explanation,
        confidence=None,
        policy_version=suitability.policy_version,
    )
    await repo.append_message(conversation_id, "assistant", explanation)

    return ProfilingTurnDto(
        reply=f"{reply}\n\n{explanation}",
        awaiting=None,
        complete=True,
        missingFields=[],
        inconsistencies=[],
        riskCategory=result.category,
        riskScore=None,
        explanation=explanation,
        eligiblePortfolios=list(suitability.eligible_portfolios),
        conversationId=str(conversation_id),
    )


def _explain(result, suitability) -> str:
    """C.3 / A.2(v): explain the outcome without revealing how it was computed."""
    top = [FACTOR_LABEL[f] for f in result.factor_ranking[:3] if f in FACTOR_LABEL]
    label = result.category.replace("_", " ")
    text = (
        f"Your assessed risk category is **{label}**. The factors that mattered most "
        f"here were {', '.join(top[:-1])} and {top[-1]}."
        if len(top) > 1
        else f"Your assessed risk category is **{label}**."
    )
    text += (
        f" Under the firm's suitability policy that makes portfolios "
        f"{', '.join(suitability.eligible_portfolios)} available to you."
    )
    if suitability.capped_by is not None:
        text += f" {suitability.capped_by.explanation}"
    return text


def _problems(turn) -> list[dict]:
    return [
        {"code": i.code, "fields": list(i.fields), "question": i.question}
        for i in turn.inconsistencies
    ]


def _problems_from_profile(profile) -> list[dict]:
    return [
        {"code": i.code, "fields": list(i.fields), "question": i.question}
        for i in coherence_check(profile)
    ]
