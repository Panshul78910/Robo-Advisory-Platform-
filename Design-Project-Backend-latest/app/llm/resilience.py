"""Retry, backoff and circuit breaking around the shared HPC endpoint (B.6).

The endpoint is outside the team's control: it will be slow when other teams are
using it, it will rate-limit, and it will sometimes be unreachable. Each of those
must produce its own distinct, honest state rather than a hang or a stack trace.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.core.clock import WallClock
from app.core.errors import CircuitOpen, LLMRateLimited, LLMTimeout, LLMUnavailable

log = logging.getLogger("app.llm.resilience")

T = TypeVar("T")

#: Only these are worth retrying. A malformed request retried three times is
#: the same failure, three times more slowly.
RETRYABLE = (LLMTimeout, LLMUnavailable, LLMRateLimited)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_s: float = 0.5
    cap_s: float = 8.0

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Exponential backoff with full jitter.

        A server-supplied Retry-After always wins: honouring it is what stops the
        whole cohort from synchronising their retries onto the same instant.
        """
        if retry_after is not None:
            return min(retry_after, self.cap_s)
        window = min(self.base_delay_s * (2 ** (attempt - 1)), self.cap_s)
        return random.uniform(0, window)


class CircuitBreaker:
    """Fails fast while the endpoint is known to be down.

    Without this, an outage makes every request wait out its own timeout-and-retry
    cycle and the app merely appears frozen -- during a live demo that is
    indistinguishable from a crash.
    """

    def __init__(self, failure_threshold: int = 5, cooldown_s: float = 30.0):
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half-open" if self._cooldown_elapsed() else "open"

    def _cooldown_elapsed(self) -> bool:
        assert self._opened_at is not None
        return (WallClock.now().timestamp() - self._opened_at) >= self._cooldown

    def before_call(self) -> None:
        if self._opened_at is None:
            return
        if not self._cooldown_elapsed():
            raise CircuitOpen()
        if self._trial_in_flight:
            raise CircuitOpen()
        # Admit exactly one trial request.
        self._trial_in_flight = True

    def on_success(self) -> None:
        if self._opened_at is not None:
            log.info("llm circuit closed after successful trial request")
        self._consecutive_failures = 0
        self._opened_at = None
        self._trial_in_flight = False

    def on_failure(self) -> None:
        self._trial_in_flight = False
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold and self._opened_at is None:
            self._opened_at = WallClock.now().timestamp()
            log.warning(
                "llm circuit opened", extra={"consecutive_failures": self._consecutive_failures}
            )
        elif self._opened_at is not None:
            # A failed trial restarts the cooldown.
            self._opened_at = WallClock.now().timestamp()


async def call_with_resilience(
    operation: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    breaker: CircuitBreaker,
) -> T:
    """Run `operation(attempt)` under the retry policy and circuit breaker."""
    breaker.before_call()

    last: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = await operation(attempt)
        except RETRYABLE as exc:
            last = exc
            if attempt == policy.max_attempts:
                break
            retry_after = getattr(exc, "retry_after", None)
            delay = policy.delay_for(attempt, retry_after)
            log.warning(
                "llm call failed, retrying",
                extra={
                    "attempt": attempt,
                    "error_code": exc.code,
                    "delay_s": round(delay, 3),
                    "honoured_retry_after": retry_after is not None,
                },
            )
            await asyncio.sleep(delay)
        except Exception:
            # Not retryable: record it against the breaker and surface it.
            breaker.on_failure()
            raise
        else:
            breaker.on_success()
            return result

    breaker.on_failure()
    assert last is not None
    raise last
