"""Switching between the shared HPC endpoint and a personal OpenAI key.

Both speak OpenAI chat-completions, so they share one client. What must be true
is that the choice is explicit, that misconfiguration fails loudly rather than
silently falling back, and that no test run can ever bill a real account.
"""

from __future__ import annotations

import os

import pytest

#: Captured before the isolating fixture can clear it.
SUITE_BACKEND = os.environ.get("LLM_BACKEND")

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.errors import ConfigurationError  # noqa: E402
from app.llm.openai_compatible import (  # noqa: E402
    CHAT_PATH,
    OpenAICompatibleClient,
    normalise_base_url,
)
from app.llm.providers import build_client, hpc_config, openai_config  # noqa: E402
from app.llm.stub import StubLLMClient  # noqa: E402

HPC_ENV = {
    "LLM_ENDPOINT": "https://llm.example.sg:8443/v1",
    "LLM_API_KEY": "hpc-key",
    "LLM_MODELS": "gemma-3-12b",
}
OPENAI_ENV = {"OPENAI_API_KEY": "sk-test-key", "OPENAI_MODEL": "gpt-4o-mini"}

PROVIDER_VARS = (
    *HPC_ENV,
    *OPENAI_ENV,
    "OPENAI_BASE_URL",
    "LLM_BACKEND",
    "HPC_BASE_URL",
    "HPC_API_KEY",
    "HPC_MODEL_NAME",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)


def settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


class TestBackendSelection:
    def test_stub_is_the_default(self):
        assert settings().llm_backend == "stub"

    @pytest.mark.parametrize("backend", ["stub", "hpc", "openai"])
    def test_all_three_backends_are_accepted(self, backend):
        assert settings(LLM_BACKEND=backend).llm_backend == backend

    def test_an_unknown_backend_is_rejected(self):
        with pytest.raises(ValueError):
            settings(LLM_BACKEND="anthropic")

    def test_the_factory_returns_the_stub_without_any_provider_config(self, monkeypatch):
        """A stub run must need no endpoint, key or model at all."""
        from app.llm.factory import get_llm_client, reset_llm_client

        # Pin the backend the test is about. `_isolated_env` clears LLM_BACKEND
        # from the environment, which unmasks whatever `.env` says -- so without
        # this the factory builds a real provider client and the test reports a
        # failure that is only a local configuration setting.
        monkeypatch.setenv("LLM_BACKEND", "stub")
        get_settings.cache_clear()

        reset_llm_client()
        try:
            client = get_llm_client()
            # ALLOW_FAULT_INJECTION wraps the chosen client, so assert through
            # the wrapper rather than on its type -- otherwise this test passes
            # or fails depending on a .env setting unrelated to what it checks.
            assert isinstance(getattr(client, "_inner", client), StubLLMClient)
        finally:
            reset_llm_client()


class TestProviderConfig:
    def test_hpc_config_reads_the_course_variables(self):
        config = hpc_config(settings(**HPC_ENV))
        assert config.name == "HPC endpoint"
        assert config.base_url == "https://llm.example.sg:8443/v1"
        assert config.model == "gemma-3-12b"
        assert config.api_key == "hpc-key"

    def test_openai_config_reads_its_own_variables(self):
        config = openai_config(settings(**OPENAI_ENV))
        assert config.name == "OpenAI"
        assert config.base_url == "https://api.openai.com"
        assert config.model == "gpt-4o-mini"
        assert config.api_key == "sk-test-key"

    def test_a_custom_openai_base_url_is_honoured(self):
        """So an Azure deployment or a local proxy can stand in."""
        config = openai_config(settings(**OPENAI_ENV, OPENAI_BASE_URL="http://localhost:1234/v1"))
        assert config.base_url == "http://localhost:1234/v1"

    def test_both_providers_share_the_resilience_settings(self):
        """B.6 behaviour must not change with the backend."""
        s = settings(**HPC_ENV, **OPENAI_ENV)
        hpc, openai = hpc_config(s), openai_config(s)
        for field in (
            "max_attempts",
            "backoff_base_s",
            "backoff_cap_s",
            "failure_threshold",
            "cooldown_s",
            "read_timeout_s",
        ):
            assert getattr(hpc, field) == getattr(openai, field), field

    def test_the_url_fix_applies_to_both(self):
        for config in (hpc_config(settings(**HPC_ENV)), openai_config(settings(**OPENAI_ENV))):
            url = normalise_base_url(config.base_url) + CHAT_PATH
            assert url.count("/v1") == 1, url


class TestMisconfigurationFailsLoudly:
    def test_openai_without_a_key_is_refused(self):
        """Silently falling back to the stub would make a demo look fine while
        answering from canned text."""
        with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
            build_client("openai", settings(LLM_BACKEND="openai"))

    def test_hpc_without_an_endpoint_is_refused(self):
        with pytest.raises(ConfigurationError, match="base URL"):
            build_client("hpc", settings(LLM_BACKEND="hpc"))

    def test_a_provider_without_a_model_is_refused(self):
        with pytest.raises(ConfigurationError, match="model name"):
            build_client("hpc", settings(LLM_ENDPOINT="https://h:8443", LLM_API_KEY="k"))

    def test_a_usable_config_builds(self):
        client = build_client("openai", settings(**OPENAI_ENV))
        assert isinstance(client, OpenAICompatibleClient)
        assert client.provider == "OpenAI"
        assert client.circuit_state == "closed"


class TestNoAccidentalSpend:
    def test_the_suite_is_pinned_to_the_stub(self):
        """tests/conftest.py forces this, so no run can bill a real account."""
        assert SUITE_BACKEND == "stub"

    def test_the_default_config_carries_no_openai_key(self):
        assert settings().openai_api_key == ""
