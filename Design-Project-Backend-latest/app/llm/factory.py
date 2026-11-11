"""Selects the LLM client for the process, so the pipeline never branches on
backend."""

from __future__ import annotations

import functools
import logging

from app.core.config import get_settings
from app.llm.client import LLMClient
from app.llm.faults import FaultInjectingClient
from app.llm.stub import StubLLMClient

log = logging.getLogger("app.llm.factory")


@functools.lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    settings = get_settings()

    if settings.llm_backend == "stub":
        client: LLMClient = StubLLMClient()
    else:
        # Imported lazily so a stub run needs no provider configuration at all.
        from app.llm.providers import build_client

        client = build_client(settings.llm_backend, settings)
        log.info("llm backend selected", extra={"backend": settings.llm_backend})

    return FaultInjectingClient(client) if settings.allow_fault_injection else client


def reset_llm_client() -> None:
    """Drop the cached client so a backend change takes effect. Tests and the
    admin console use this; nothing in a request path should."""
    get_llm_client.cache_clear()
