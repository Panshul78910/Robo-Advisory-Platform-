"""Where each endpoint's address, key and model come from.

Both providers speak the same protocol (see `openai_compatible.py`); only these
values differ. Resilience settings are shared deliberately -- the behaviour Part
B is graded on should not change with the backend.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError
from app.llm.openai_compatible import OpenAICompatibleClient, ProviderConfig


def _shared(settings: Settings) -> dict:
    return {
        "connect_timeout_s": settings.hpc_connect_timeout_s,
        "read_timeout_s": settings.hpc_read_timeout_s,
        "max_attempts": settings.hpc_max_attempts,
        "backoff_base_s": settings.hpc_backoff_base_s,
        "backoff_cap_s": settings.hpc_backoff_cap_s,
        "failure_threshold": settings.circuit_failure_threshold,
        "cooldown_s": settings.circuit_cooldown_s,
    }


#: Course models that reason before answering. They spend completion tokens on a
#: hidden chain of thought first, so with a modest `max_tokens` the budget is gone
#: before any answer is written: HTTP 200, `finish_reason: length`, empty content.
#: Measured on qwen3.6-35b-a3b-fp8 -- 800 tokens of reasoning, 0 characters of
#: answer. Turning thinking off returned a full answer using none.
#:
#: `chat_template_kwargs` is a vLLM/SGLang extension, which is why it is set here
#: rather than in the shared client: OpenAI rejects unknown body fields.
REASONING_MODELS = ("qwen3", "qwen3.6")


def _suppresses_thinking(model: str) -> bool:
    return any(model.lower().startswith(prefix) for prefix in REASONING_MODELS)


def hpc_config(settings: Settings | None = None) -> ProviderConfig:
    """The shared course endpoint. Published as LLM_ENDPOINT / LLM_API_KEY / LLM_MODELS."""
    settings = settings or get_settings()
    model = settings.hpc_model_name
    extra: dict = {}
    if _suppresses_thinking(model):
        extra["chat_template_kwargs"] = {"enable_thinking": False}
    return ProviderConfig(
        name="HPC endpoint",
        base_url=settings.hpc_base_url,
        api_key=settings.hpc_api_key,
        model=model,
        extra_body=extra,
        **_shared(settings),
    )


def openai_config(settings: Settings | None = None) -> ProviderConfig:
    """A personal OpenAI account.

    A fallback for when the shared endpoint is unreachable. Note this sends
    prompts to a third party and bills a real account -- see README.
    """
    settings = settings or get_settings()
    return ProviderConfig(
        name="OpenAI",
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        **_shared(settings),
    )


def build_client(backend: str, settings: Settings | None = None) -> OpenAICompatibleClient:
    settings = settings or get_settings()
    config = {"hpc": hpc_config, "openai": openai_config}[backend](settings)
    if backend == "openai" and not config.api_key:
        raise ConfigurationError("LLM_BACKEND=openai but OPENAI_API_KEY is not set")
    return OpenAICompatibleClient(config)
