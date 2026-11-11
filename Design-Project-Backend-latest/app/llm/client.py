"""The LLM boundary: payload shape, response shape, and the client protocol.

Only `app/orchestration/context.py` constructs a Payload, and only an LLMClient
sends one. Keeping both types here means the resilience, stub and HPC modules
share a contract without importing the orchestration layer -- which is also what
keeps `app.domain.risk.weights` unreachable from anything on this side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable


class Trust(StrEnum):
    """Declared, not inferred.

    Marking trust structurally is the main defence against indirect prompt
    injection (D.5): instruction-like text arriving inside a profile field or a
    retrieved passage sits in a block the model has been told is data.
    """

    INSTRUCTION = "instruction"
    DATA = "data"
    UNTRUSTED_INPUT = "untrusted-input"


#: Fixed block order. The context builder emits exactly these, in this order.
class BlockName(StrEnum):
    SYSTEM = "SYSTEM"
    CLIENT_FACTS = "CLIENT_FACTS"
    RETRIEVED_POLICY = "RETRIEVED_POLICY"
    HISTORY = "HISTORY"
    USER_MESSAGE = "USER_MESSAGE"


@dataclass(frozen=True, slots=True)
class Block:
    name: BlockName
    trust: Trust
    text: str
    token_count: int
    #: Chunk refs included in this block, so emitted citations can be validated.
    chunk_refs: tuple[str, ...] = ()

    def render(self) -> str:
        return f"<<<{self.name} trust={self.trust}>>>\n{self.text}\n<<<END {self.name}>>>"


@dataclass(frozen=True, slots=True)
class Payload:
    blocks: tuple[Block, ...]
    model: str
    context_limit: int
    max_output_tokens: int
    prompt_version: str
    intent: str
    #: Chunk refs dropped under budget pressure, recorded on the llm_call row.
    dropped_chunk_refs: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return sum(b.token_count for b in self.blocks)

    @property
    def block_tokens(self) -> dict[str, int]:
        return {b.name.value: b.token_count for b in self.blocks}

    def block(self, name: BlockName) -> Block | None:
        return next((b for b in self.blocks if b.name is name), None)

    def render(self) -> str:
        return "\n\n".join(b.render() for b in self.blocks)

    def to_messages(self) -> list[dict[str, str]]:
        """Chat-completions form: the system block is the system role, the rest
        is one user turn, so the trust delimiters survive intact."""
        system = self.block(BlockName.SYSTEM)
        rest = [b for b in self.blocks if b.name is not BlockName.SYSTEM]
        return [
            {"role": "system", "content": system.text if system else ""},
            {"role": "user", "content": "\n\n".join(b.render() for b in rest)},
        ]

    def to_log(self) -> dict:
        """Loggable form. Safe to store in full precisely because the scoring
        weights can never be in here (invariant 8)."""
        return {
            "intent": self.intent,
            "model": self.model,
            "promptVersion": self.prompt_version,
            "contextLimit": self.context_limit,
            "blocks": [
                {
                    "name": b.name.value,
                    "trust": b.trust.value,
                    "tokens": b.token_count,
                    "chunkRefs": list(b.chunk_refs),
                    "text": b.text,
                }
                for b in self.blocks
            ],
            "droppedChunkRefs": list(self.dropped_chunk_refs),
        }


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model_version: str
    #: The stub's documented per-intent confidence. Real endpoints leave this
    #: None -- self-reported confidence is not a measurement (C.4).
    suggested_confidence: Decimal | None = None
    finish_reason: str = "stop"
    latency_ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    async def complete(self, payload: Payload, *, correlation_id: str) -> LLMResponse: ...
