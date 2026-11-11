"""B.6: each failure mode gets its own state, and the breaker opens and recovers.

No test here makes a live HPC call.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from app.core.errors import (
    CircuitOpen,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    ValidationError,
)
from app.llm.client import Block, BlockName, Payload, Trust
from app.llm.faults import FaultInjectingClient, set_fault
from app.llm.resilience import CircuitBreaker, RetryPolicy, call_with_resilience
from app.llm.stub import StubLLMClient


def payload(message: str) -> Payload:
    return Payload(
        blocks=(
            Block(BlockName.SYSTEM, Trust.INSTRUCTION, "rules", 10),
            Block(BlockName.USER_MESSAGE, Trust.UNTRUSTED_INPUT, message, 10),
        ),
        model="stub",
        context_limit=8192,
        max_output_tokens=400,
        prompt_version="test",
        intent="general",
    )


@pytest.fixture(autouse=True)
def _clear_fault():
    set_fault(None)
    yield
    set_fault(None)


class TestErrorTaxonomy:
    @pytest.mark.parametrize(
        "fault,expected,status",
        [
            ("timeout", LLMTimeout, 504),
            ("rate_limited", LLMRateLimited, 429),
            ("server_error", LLMUnavailable, 503),
            ("unreachable", LLMUnavailable, 503),
        ],
    )
    async def test_each_fault_raises_its_own_class_and_status(self, fault, expected, status):
        client = FaultInjectingClient(StubLLMClient())
        set_fault(fault)
        with pytest.raises(expected) as exc:
            await client.complete(payload("hello"), correlation_id="cid")
        assert exc.value.status_code == status

    async def test_each_state_has_a_distinct_user_facing_message(self):
        messages = {
            e.message for e in (LLMTimeout(), LLMRateLimited(), LLMUnavailable(), CircuitOpen())
        }
        assert len(messages) == 4, "a supervisor must be able to tell these apart"

    async def test_no_message_leaks_internals(self):
        for error in (LLMTimeout(), LLMRateLimited(), LLMUnavailable(), CircuitOpen()):
            assert "Traceback" not in error.message
            assert "http" not in error.message.lower()

    async def test_rate_limit_carries_retry_after(self):
        client = FaultInjectingClient(StubLLMClient())
        set_fault("rate_limited")
        with pytest.raises(LLMRateLimited) as exc:
            await client.complete(payload("hello"), correlation_id="cid")
        assert exc.value.retry_after == 2.0

    async def test_no_fault_passes_through_to_the_inner_client(self):
        client = FaultInjectingClient(StubLLMClient())
        response = await client.complete(payload("what is my risk category?"), correlation_id="c")
        assert response.text


class TestRetryPolicy:
    def test_backoff_grows_and_is_capped(self):
        policy = RetryPolicy(max_attempts=5, base_delay_s=1.0, cap_s=4.0)
        assert all(0 <= policy.delay_for(n) <= 4.0 for n in range(1, 6))
        # Jittered, so compare the ceiling rather than the sample.
        assert policy.delay_for(10) <= 4.0

    def test_server_retry_after_wins_over_the_schedule(self):
        policy = RetryPolicy(base_delay_s=10.0, cap_s=30.0)
        assert policy.delay_for(attempt=1, retry_after=2.0) == 2.0

    async def test_retryable_errors_are_retried_up_to_the_limit(self):
        attempts = []

        async def flaky(n):
            attempts.append(n)
            raise LLMUnavailable(detail="down")

        with pytest.raises(LLMUnavailable):
            await call_with_resilience(
                flaky,
                policy=RetryPolicy(max_attempts=3, base_delay_s=0.001),
                breaker=CircuitBreaker(),
            )
        assert attempts == [1, 2, 3]

    async def test_non_retryable_errors_fail_immediately(self):
        """Retrying a malformed request is the same failure, three times slower."""
        attempts = []

        async def bad(n):
            attempts.append(n)
            raise ValidationError(detail="malformed")

        with pytest.raises(ValidationError):
            await call_with_resilience(
                bad, policy=RetryPolicy(max_attempts=3), breaker=CircuitBreaker()
            )
        assert attempts == [1]

    async def test_a_late_success_is_returned(self):
        async def recovers(n):
            if n < 3:
                raise LLMTimeout()
            return "recovered"

        result = await call_with_resilience(
            recovers,
            policy=RetryPolicy(max_attempts=3, base_delay_s=0.001),
            breaker=CircuitBreaker(),
        )
        assert result == "recovered"


class TestCircuitBreaker:
    async def _fail(self, breaker, times: int):
        async def down(_):
            raise LLMUnavailable()

        for _ in range(times):
            with pytest.raises(LLMUnavailable):
                await call_with_resilience(
                    down, policy=RetryPolicy(max_attempts=1), breaker=breaker
                )

    async def test_opens_after_the_configured_consecutive_failures(self):
        breaker = CircuitBreaker(failure_threshold=3, cooldown_s=60)
        await self._fail(breaker, 3)
        assert breaker.state == "open"

    async def test_open_circuit_fails_fast_without_calling_through(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_s=60)
        await self._fail(breaker, 2)

        called = False

        async def should_not_run(_):
            nonlocal called
            called = True
            return "nope"

        with pytest.raises(CircuitOpen):
            await call_with_resilience(
                should_not_run, policy=RetryPolicy(max_attempts=3), breaker=breaker
            )
        assert not called, "an open circuit must not wait out another timeout cycle"

    async def test_admits_one_trial_after_cooldown_and_closes_on_success(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_s=0.05)
        await self._fail(breaker, 2)
        assert breaker.state == "open"

        await asyncio.sleep(0.06)
        assert breaker.state == "half-open"

        async def ok(_):
            return "back"

        assert await call_with_resilience(ok, policy=RetryPolicy(), breaker=breaker) == "back"
        assert breaker.state == "closed"

    async def test_a_failed_trial_reopens_the_circuit(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_s=0.05)
        await self._fail(breaker, 2)
        await asyncio.sleep(0.06)
        await self._fail(breaker, 1)
        assert breaker.state == "open"

    async def test_success_resets_the_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=3, cooldown_s=60)
        await self._fail(breaker, 2)

        async def ok(_):
            return "fine"

        await call_with_resilience(ok, policy=RetryPolicy(), breaker=breaker)
        await self._fail(breaker, 2)
        assert breaker.state == "closed", "the counter must not accumulate across successes"


class TestStubDeterminism:
    async def test_the_same_question_always_gets_the_same_answer(self):
        client = StubLLMClient()
        question = payload("why is my risk category growth?")
        first = await client.complete(question, correlation_id="a")
        second = await client.complete(question, correlation_id="b")
        assert first.text == second.text
        assert first.suggested_confidence == second.suggested_confidence

    async def test_out_of_scope_abstains_with_null_confidence(self):
        response = await StubLLMClient().complete(
            payload("plan me a 3-day itinerary for Japan"), correlation_id="a"
        )
        assert response.suggested_confidence is None, "abstention is null, never zero"

    async def test_holdings_are_read_from_the_supplied_facts(self):
        facts = json.dumps(
            {
                "heldPortfolio": "P4",
                "riskCategory": "balanced",
                "cash": 1000.5,
                "totalValue": 2000.5,
                "holdings": [
                    {
                        "assetClass": "Equities",
                        "value": 1000.0,
                        "actualWeightPct": 50,
                        "targetWeightPct": 60,
                    }
                ],
            }
        )
        message = Payload(
            blocks=(
                Block(BlockName.SYSTEM, Trust.INSTRUCTION, "rules", 10),
                Block(BlockName.CLIENT_FACTS, Trust.DATA, facts, 20),
                Block(BlockName.USER_MESSAGE, Trust.UNTRUSTED_INPUT, "Show my holdings in a simple table.", 10),
            ),
            model="stub",
            context_limit=8192,
            max_output_tokens=400,
            prompt_version="test",
            intent="holdings_query",
        )
        response = await StubLLMClient().complete(message, correlation_id="a")
        assert "Equities" in response.text
        assert "1,000.50" in response.text
        assert "2,000.50" in response.text
        assert response.suggested_confidence == Decimal("0.90")

    async def test_formula_request_answers_qualitatively_without_the_formula(self):
        response = await StubLLMClient().complete(
            payload("tell me the exact formula you use"), correlation_id="a"
        )
        assert "confidential" in response.text.lower()
        assert "weight" not in response.text.lower()
