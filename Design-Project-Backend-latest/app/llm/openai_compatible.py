"""One client for any OpenAI-compatible chat-completions endpoint.

The shared HPC service and OpenAI itself speak the same protocol, so they differ
only in base URL, key and model name. Keeping a single implementation means the
resilience behaviour Part B is graded on -- timeouts, bounded retries, honoured
`Retry-After`, the circuit breaker -- is identical whichever one is in use, and
cannot drift between them.

Credentials come from the environment and are never logged, committed, or sent
to the browser.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from app.core.errors import (
    ConfigurationError,
    LLMRateLimited,
    LLMTimeout,
    LLMUnavailable,
    ValidationError,
)
from app.llm.client import LLMResponse, Payload
from app.llm.resilience import CircuitBreaker, RetryPolicy, call_with_resilience

log = logging.getLogger("app.llm.provider")

CHAT_PATH = "/v1/chat/completions"
MODELS_PATH = "/v1/models"

#: Zero, because this is an advisory product: the same question should not get a
#: materially different answer on a second asking.
#:
#: It was 0.2, which is low but not none, and that was enough to make the system
#: non-deterministic in a way that reached the client. The egress faithfulness
#: judge grades each answer against the evidence, so when the wording varies the
#: verdict can vary with it: asking "why was I assigned this risk category?"
#: three times gave one refusal and two identical answers. A guardrail that
#: fires on the phrasing rather than the substance is worse than one that never
#: fires, because it is unreproducible -- for the client, and for anyone trying
#: to capture before/after evidence of it working.
TEMPERATURE = 0.0


def normalise_base_url(url: str) -> str:
    """Return the server root, with any trailing `/v1` removed.

    An endpoint is commonly published either as `https://host:8443` or as
    `https://host:8443/v1`. Paths here carry their own `/v1`, so both forms must
    collapse to the same root or requests land on `/v1/v1/...`.
    """
    trimmed = url.strip().rstrip("/")
    return trimmed[: -len("/v1")] if trimmed.endswith("/v1") else trimmed


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Everything that differs between one endpoint and another."""

    name: str
    base_url: str
    api_key: str
    model: str
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 60.0
    max_attempts: int = 3
    backoff_base_s: float = 0.5
    backoff_cap_s: float = 8.0
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    #: Extra request-body fields this endpoint understands. Kept per-provider
    #: because they are not portable: a key one server requires is a 400 from
    #: another, so it must never leak into a request to a different provider.
    extra_body: dict = field(default_factory=dict)

    def require_usable(self) -> None:
        if not self.base_url:
            raise ConfigurationError(f"{self.name}: base URL is not configured")
        if not self.model:
            raise ConfigurationError(f"{self.name}: model name is not configured")


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None  # HTTP-date form; fall back to the backoff schedule


class OpenAICompatibleClient:
    def __init__(self, config: ProviderConfig, client: httpx.AsyncClient | None = None):
        config.require_usable()
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=normalise_base_url(config.base_url),
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=config.read_timeout_s,
                write=config.connect_timeout_s,
                pool=config.connect_timeout_s,
            ),
        )
        self._policy = RetryPolicy(
            max_attempts=config.max_attempts,
            base_delay_s=config.backoff_base_s,
            cap_s=config.backoff_cap_s,
        )
        self._breaker = CircuitBreaker(
            failure_threshold=config.failure_threshold, cooldown_s=config.cooldown_s
        )

    @property
    def provider(self) -> str:
        return self._config.name

    @property
    def circuit_state(self) -> str:
        return self._breaker.state

    def _headers(self, correlation_id: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "X-Correlation-Id": correlation_id}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def complete(self, payload: Payload, *, correlation_id: str) -> LLMResponse:
        async def attempt(n: int) -> LLMResponse:
            return await self._post(payload, correlation_id=correlation_id, attempt=n)

        return await call_with_resilience(attempt, policy=self._policy, breaker=self._breaker)

    async def _post(self, payload: Payload, *, correlation_id: str, attempt: int) -> LLMResponse:
        body = {
            # The payload names a model only when something overrode it; the
            # provider's own configured model is the normal case.
            "model": self._config.model,
            "messages": payload.to_messages(),
            "max_tokens": payload.max_output_tokens,
            "temperature": TEMPERATURE,
            "stream": False,
            **self._config.extra_body,
        }
        started = time.perf_counter()
        try:
            response = await self._client.post(
                CHAT_PATH, json=body, headers=self._headers(correlation_id)
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(detail=f"attempt {attempt}: {type(exc).__name__}") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable(detail=f"attempt {attempt}: {type(exc).__name__}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        provider = self._config.name

        if response.status_code == 429:
            raise LLMRateLimited(retry_after=_retry_after(response), detail=f"429 from {provider}")
        if response.status_code >= 500:
            raise LLMUnavailable(detail=f"{provider} returned {response.status_code}")
        if response.status_code >= 400:
            # A client-side error is our bug: surfacing it beats retrying it.
            # 401 is called out because it is almost always a missing or expired
            # key, and "bad request" sends people looking in the wrong place.
            log.error(
                "provider rejected request",
                extra={"provider": provider, "status": response.status_code, "attempt": attempt},
            )
            reason = (
                "rejected the API key" if response.status_code == 401 else "rejected the request"
            )
            raise ValidationError(detail=f"{provider} {reason} ({response.status_code})")

        return self._parse(response, latency_ms)

    def _parse(self, response: httpx.Response, latency_ms: int) -> LLMResponse:
        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMUnavailable(
                detail=f"unparseable response from {self._config.name}: {type(exc).__name__}"
            ) from exc

        if not (text or "").strip():
            # A reasoning model that spends its whole budget thinking returns
            # `finish_reason: length` with empty content, and HTTP 200. Treated as
            # success it produced an answer consisting only of the appended
            # disclosure -- confident-looking, with sources and a decision id, and
            # nothing said. Failing here retries, then surfaces honestly.
            finish = choice.get("finish_reason")
            reasoning = (data.get("usage") or {}).get("reasoning_tokens")
            log.warning(
                "provider returned an empty completion",
                extra={
                    "provider": self._config.name,
                    "finish_reason": finish,
                    "reasoning_tokens": reasoning,
                },
            )
            raise LLMUnavailable(
                detail=(
                    f"{self._config.name} returned no content "
                    f"(finish_reason={finish}, reasoning_tokens={reasoning})"
                )
            )

        return LLMResponse(
            text=text,
            model_version=data.get("model") or self._config.model,
            # Deliberately None: a model's self-reported confidence is not a
            # measurement. Confidence is derived from observable signals (C.4).
            suggested_confidence=None,
            finish_reason=choice.get("finish_reason", "stop"),
            latency_ms=latency_ms,
            usage=data.get("usage") or {},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
