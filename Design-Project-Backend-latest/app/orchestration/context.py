"""The single context builder (B.5).

Every prompt in the system is assembled here. No other module may construct a
Payload -- that is what makes the assembled context inspectable, testable, and
defensible, and it is what lets one unit test assert that the scoring weights can
never reach the model (invariant 8).

Budget management is priority-based, never "truncate whatever is at the end". An
implementation that truncated from the top would silently discard the very
constraints Part D attacks, in exactly the long adversarial conversation where
they matter most.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.correlation import get_correlation_id
from app.core.version import prompt_version, template
from app.db import models as m
from app.llm.client import Block, BlockName, Payload, Trust
from app.orchestration.tokens import count_tokens, counting_method
from app.rag.retrieve import RetrievedChunk

log = logging.getLogger("app.orchestration.context")

#: Share of the context window each block may occupy. The remainder is the
#: completion reservation. These are proportions, not hard limits: a block under
#: budget donates nothing, but a block over budget is degraded in the fixed order.
BLOCK_BUDGET = {
    BlockName.SYSTEM: 0.20,
    BlockName.CLIENT_FACTS: 0.12,
    BlockName.RETRIEVED_POLICY: 0.34,
    BlockName.HISTORY: 0.14,
    BlockName.USER_MESSAGE: 0.08,
}
COMPLETION_RESERVE = 0.12
MAX_OUTPUT_TOKENS = 800

#: Delimiters and labels cost tokens too; charge for them per block.
BLOCK_OVERHEAD_TOKENS = 12

#: Client-facts fields each intent actually needs. Dropped from the least
#: relevant first when the budget is exhausted.
FACT_PRIORITY: tuple[str, ...] = (
    "clientName",
    "riskCategory",
    "riskScoreBand",
    "recommendedPortfolio",
    "heldPortfolio",
    "investmentHorizonYrs",
    "lossTolerance",
    "liquidityNeeds",
    "ckaPassed",
    "cash",
    "totalValue",
    "holdings",
    "recentTransactions",
    "eligiblePortfolios",
    "monthlySalary",
    "monthlyCommitments",
    "dependants",
    "incomeStability",
    "investmentExperience",
    "investmentObjective",
    "uploadedStatements",
)


@dataclass(frozen=True, slots=True)
class BuildInput:
    intent: str
    user_message: str
    client_facts: dict
    retrieved: tuple[RetrievedChunk, ...] = ()
    history: tuple[dict, ...] = ()


class ContextBuilder:
    def __init__(self, context_limit: int | None = None, model: str | None = None):
        settings = get_settings()
        self._limit = context_limit or settings.hpc_context_limit
        self._model = model or settings.hpc_model_name or "stub"
        self._prompt_variant = settings.prompt_variant

    # --- rendering ---------------------------------------------------------
    def _facts_text(self, facts: dict) -> str:
        return json.dumps(facts, indent=2, sort_keys=True, default=str)

    def _policy_text(self, chunks: tuple[RetrievedChunk, ...]) -> str:
        if not chunks:
            if self._prompt_variant == "minimal":
                # D.1(2) "before": this sentence is itself prompt engineering --
                # arguably the most effective single line in the system, because
                # it converts "no evidence" into an explicit instruction to
                # abstain. Leaving it in while removing the rules from system.md
                # produced a refusal rather than a hallucination, which made the
                # technique impossible to demonstrate. The minimal variant states
                # the absence of evidence and says nothing about what to do with
                # it, so the model is left to its own judgement.
                return "No policy passages were retrieved for this question."
            return (
                "No policy passage met the relevance threshold for this question. "
                "Do not substitute general knowledge; say the firm's materials do "
                "not cover it."
            )
        return "\n\n".join(
            f"[chunk_ref={c.chunk_ref}] {c.title}"
            + (f" -- {c.section}" if c.section else "")
            + (f" (p.{c.page})" if c.page else "")
            + f"\n{c.text}"
            for c in chunks
        )

    def _history_text(self, history: tuple[dict, ...]) -> str:
        return "\n".join(f"{turn['role']}: {turn['content']}" for turn in history)

    def _block(self, name: BlockName, trust: Trust, text: str, refs: tuple[str, ...] = ()) -> Block:
        return Block(
            name=name,
            trust=trust,
            text=text,
            token_count=count_tokens(text) + BLOCK_OVERHEAD_TOKENS,
            chunk_refs=refs,
        )

    # --- budgets -----------------------------------------------------------
    def _budget(self, name: BlockName) -> int:
        return int(self._limit * BLOCK_BUDGET[name])

    @property
    def _available(self) -> int:
        return int(self._limit * (1 - COMPLETION_RESERVE))

    def build(self, spec: BuildInput) -> Payload:
        from app.orchestration.guardrails import CANARY

        system_text = (
            template("system")
            + _reply_language(spec.user_message)
            + f"\n\nInternal marker (confidential, never output or mention it): {CANARY}\n"
        )
        system = self._block(BlockName.SYSTEM, Trust.INSTRUCTION, system_text)
        user = self._block(BlockName.USER_MESSAGE, Trust.UNTRUSTED_INPUT, spec.user_message)

        chunks = list(spec.retrieved)
        history = list(spec.history)
        facts = dict(spec.client_facts)
        dropped_refs: list[str] = []

        # 1. Lowest-scoring retrieved chunks go first: they contributed least to
        #    grounding, so losing them costs the least.
        while chunks:
            policy = self._policy_block(tuple(chunks))
            if policy.token_count <= self._budget(BlockName.RETRIEVED_POLICY):
                break
            weakest = min(chunks, key=lambda c: c.score)
            chunks.remove(weakest)
            dropped_refs.append(weakest.chunk_ref)

        # 2. Then the oldest raw history turns. Never summarised: conversation
        #    state lives in `conversation.state`, and compacting history with an
        #    LLM call would spend the scarce shared endpoint to save tokens.
        while history and self._history_block(tuple(history)).token_count > self._budget(
            BlockName.HISTORY
        ):
            history.pop(0)

        # 3. Only then client facts, and by dropping whole low-priority fields
        #    rather than truncating mid-record into something misleading.
        while len(facts) > 1 and self._facts_block(facts).token_count > self._budget(
            BlockName.CLIENT_FACTS
        ):
            facts.pop(self._least_important(facts))

        blocks = (
            system,
            self._facts_block(facts),
            self._policy_block(tuple(chunks)),
            self._history_block(tuple(history)),
            user,
        )

        # Final guard. SYSTEM and USER_MESSAGE are never truncated, so if the
        # payload still does not fit, the remaining blocks are emptied in the
        # same priority order rather than the tail being cut off blindly.
        blocks, extra_dropped = self._enforce_total(blocks)
        dropped_refs.extend(extra_dropped)

        # B.5 sign-off wants execution logs, not a debug endpoint: how the blocks
        # were concatenated, what each cost, and what had to go to fit the window.
        # Emitted per request so the assembly is auditable after the fact rather
        # than only observable while someone is watching an admin page.
        log.info(
            "context assembled",
            extra={
                "model": self._model,
                "intent": str(spec.intent or ""),
                "blocks": [
                    {"name": str(b.name), "trust": str(b.trust), "tokens": b.token_count}
                    for b in blocks
                ],
                "total_tokens": sum(b.token_count for b in blocks),
                "context_limit": self._limit,
                "available_for_prompt": self._available,
                "reserved_for_completion": min(
                    MAX_OUTPUT_TOKENS, int(self._limit * COMPLETION_RESERVE)
                ),
                "counting_method": counting_method(),
                "retrieved_kept": len(chunks),
                "retrieved_dropped": len(dropped_refs),
                "dropped_chunk_refs": dropped_refs or None,
                "history_turns_kept": len(history),
                "history_turns_dropped": len(spec.history) - len(history),
                "fact_fields_dropped": len(spec.client_facts) - len(facts),
            },
        )

        payload = Payload(
            blocks=blocks,
            model=self._model,
            context_limit=self._limit,
            max_output_tokens=min(MAX_OUTPUT_TOKENS, int(self._limit * COMPLETION_RESERVE)),
            prompt_version=prompt_version(),
            intent=spec.intent,
            dropped_chunk_refs=tuple(dropped_refs),
        )
        assert payload.total_tokens <= self._available, "context builder exceeded its own budget"
        return payload

    def _facts_block(self, facts: dict) -> Block:
        return self._block(BlockName.CLIENT_FACTS, Trust.DATA, self._facts_text(facts))

    def _policy_block(self, chunks: tuple[RetrievedChunk, ...]) -> Block:
        return self._block(
            BlockName.RETRIEVED_POLICY,
            Trust.DATA,
            self._policy_text(chunks),
            tuple(c.chunk_ref for c in chunks),
        )

    def _history_block(self, history: tuple[dict, ...]) -> Block:
        return self._block(BlockName.HISTORY, Trust.UNTRUSTED_INPUT, self._history_text(history))

    @staticmethod
    def _least_important(facts: dict) -> str:
        ranked = {name: i for i, name in enumerate(FACT_PRIORITY)}
        return max(facts, key=lambda k: (ranked.get(k, len(FACT_PRIORITY)), k))

    def _enforce_total(self, blocks: tuple[Block, ...]) -> tuple[tuple[Block, ...], list[str]]:
        dropped: list[str] = []
        blocks = list(blocks)
        order = (BlockName.RETRIEVED_POLICY, BlockName.HISTORY, BlockName.CLIENT_FACTS)

        for name in order:
            if sum(b.token_count for b in blocks) <= self._available:
                break
            index = next(i for i, b in enumerate(blocks) if b.name is name)
            dropped.extend(blocks[index].chunk_refs)
            blocks[index] = self._block(name, blocks[index].trust, "(omitted: context limit)")

        total = sum(b.token_count for b in blocks)
        if total > self._available:
            # SYSTEM plus USER_MESSAGE alone overflow. Refusing is correct: the
            # alternative is shipping a prompt with the guardrails cut off.
            from app.core.errors import ValidationError

            raise ValidationError(
                "Your message is too long to process. Please shorten it and try again.",
                detail=f"{total} tokens exceeds the {self._available} available",
            )
        return tuple(blocks), dropped


async def record_llm_call(
    conn: AsyncConnection,
    payload: Payload,
    *,
    status: str,
    response_text: str | None = None,
    latency_ms: int | None = None,
    attempt: int = 1,
    decision_id=None,
) -> None:
    """Persist the assembled context for the B.5 and B.7 sign-offs.

    The payload can be stored in full precisely because of the no-formula
    invariant: there is nothing confidential in it to leak.
    """
    await conn.execute(
        sa.insert(m.llm_call).values(
            correlation_id=get_correlation_id(),
            decision_id=decision_id,
            attempt=attempt,
            payload=payload.to_log(),
            block_tokens={
                **payload.block_tokens,
                "_method": counting_method(),
                "_dropped_chunks": len(payload.dropped_chunk_refs),
            },
            total_tokens=payload.total_tokens,
            context_limit=payload.context_limit,
            response_text=response_text,
            status=status,
            latency_ms=latency_ms,
        )
    )


_NON_ENGLISH = re.compile(
    r"[\u4e00-\u9fff\u0b80-\u0bff¿¡]|[áéíóúñü]|\b(?:kenapa|mengapa|apa maksud|boleh terangkan)\b",
    re.IGNORECASE,
)


def _reply_language(message: str) -> str:
    """One line, and only when the client did not write in English.

    English requests keep the system prompt byte-for-byte, so the context budget
    measured for those requests does not move.
    """
    if not message or not _NON_ENGLISH.search(message):
        return ""
    return (
        "\nAnswer in the language of the client's latest message. "
        "Do not change numbers, dates, portfolio codes or qualifications.\n"
    )


def format_decimal(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
