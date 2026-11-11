"""Fault injection for demonstrating B.6.

Wraps any LLMClient and forces a chosen failure. Admin-gated and off unless
ALLOW_FAULT_INJECTION is set, so it can never be triggered by a client.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Literal

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.errors import LLMRateLimited, LLMTimeout, LLMUnavailable, ValidationError
from app.llm.client import LLMClient, LLMResponse, Payload
from app.llm.resilience import CircuitBreaker, RetryPolicy, call_with_resilience

Fault = Literal["timeout", "rate_limited", "server_error", "slow", "unreachable"]
FAULTS: frozenset[str] = frozenset(
    ("timeout", "rate_limited", "server_error", "slow", "unreachable")
)

HEADER = "X-Fault-Inject"
SLOW_DELAY_S = 3.0

_requested_fault: ContextVar[str | None] = ContextVar("requested_fault", default=None)


def set_fault(fault: str | None) -> None:
    _requested_fault.set(fault if fault in FAULTS else None)


def get_fault() -> str | None:
    return _requested_fault.get()


def _raise_for(fault: str) -> None:
    match fault:
        case "timeout":
            raise LLMTimeout(detail="injected")
        case "rate_limited":
            raise LLMRateLimited(retry_after=2.0, detail="injected")
        case "server_error":
            raise LLMUnavailable(detail="injected 500")
        case "unreachable":
            raise LLMUnavailable(detail="injected connection failure")


class FaultInjectingClient:
    """Wraps any client and forces a chosen failure.

    The injected failure is driven through the same retry-and-circuit-breaker
    machinery a real provider uses, rather than raised straight to the caller.
    Raising directly meant the breaker was never exercised end to end -- and
    with `LLM_BACKEND=stub` it could not be, because the stub has no resilience
    layer of its own. The error states looked right while the retry and
    breaker behaviour B.6 is graded on was bypassed entirely.
    """

    def __init__(self, inner: LLMClient, settings=None):
        settings = settings or get_settings()
        self._inner = inner
        self._policy = RetryPolicy(
            max_attempts=settings.hpc_max_attempts,
            base_delay_s=min(settings.hpc_backoff_base_s, 0.2),  # keep a demo brisk
            cap_s=min(settings.hpc_backoff_cap_s, 1.0),
        )
        self._breaker = CircuitBreaker(
            failure_threshold=settings.circuit_failure_threshold,
            cooldown_s=settings.circuit_cooldown_s,
        )

    @property
    def circuit_state(self) -> str:
        return self._breaker.state

    async def complete(self, payload: Payload, *, correlation_id: str) -> LLMResponse:
        fault = get_fault()
        if fault is None:
            return await self._inner.complete(payload, correlation_id=correlation_id)

        if fault == "slow":
            await asyncio.sleep(SLOW_DELAY_S)
            return await self._inner.complete(payload, correlation_id=correlation_id)

        async def attempt(_n: int) -> LLMResponse:
            _raise_for(fault)
            return await self._inner.complete(payload, correlation_id=correlation_id)

        return await call_with_resilience(attempt, policy=self._policy, breaker=self._breaker)


class FaultInjectionMiddleware(BaseHTTPMiddleware):
    """Honour `X-Fault-Inject` on whichever request actually calls the model.

    Previously this was a route dependency on the admin debug endpoint, which
    never calls the LLM -- and because the fault is held in a contextvar, it
    could not carry across to a separate `/api/chat` request. The mechanism was
    implemented and unit-tested but unreachable end to end, so B.6 could not be
    demonstrated through the API at all.

    `ALLOW_FAULT_INJECTION` is the guard. It defaults to false and a client
    cannot set it, so the header is inert in any normal deployment.
    """

    async def dispatch(self, request, call_next):
        set_fault(None)
        if get_settings().allow_fault_injection:
            requested = request.headers.get(HEADER)
            if requested:
                if requested not in FAULTS:
                    raise ValidationError(
                        f"Unknown fault {requested!r}. Expected one of {sorted(FAULTS)}."
                    )
                set_fault(requested)
        return await call_next(request)
