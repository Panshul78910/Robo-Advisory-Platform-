"""The course publishes LLM_ENDPOINT / LLM_API_KEY / LLM_MODELS.

Those names are authoritative, so they win over the older HPC_* spellings. The
published values are also not guaranteed to be tidy -- a missing scheme, shell
quotes, or several models in one variable -- and none of those should surface as
an obscure httpx error at request time.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

COURSE_VARS = ("LLM_ENDPOINT", "LLM_API_KEY", "LLM_MODELS")
LEGACY_VARS = ("HPC_BASE_URL", "HPC_API_KEY", "HPC_MODEL_NAME")


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    for name in (*COURSE_VARS, *LEGACY_VARS, "DB_TARGET", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


class TestAliasPrecedence:
    def test_course_names_are_accepted(self):
        s = settings(
            LLM_ENDPOINT="https://llm.example.sg:8443/v1",
            LLM_API_KEY="k" * 36,
            LLM_MODELS="gemma-3-12b",
        )
        assert s.hpc_base_url == "https://llm.example.sg:8443/v1"
        assert s.hpc_api_key == "k" * 36
        assert s.hpc_model_name == "gemma-3-12b"

    def test_legacy_names_still_work_alone(self):
        s = settings(
            HPC_BASE_URL="https://old.example:8443",
            HPC_API_KEY="j" * 10,
            HPC_MODEL_NAME="mistral-7b",
        )
        assert s.hpc_base_url == "https://old.example:8443"
        assert s.hpc_model_name == "mistral-7b"

    def test_course_names_win_when_both_are_set(self):
        """A .env carrying both must resolve to the authoritative values."""
        s = settings(
            LLM_ENDPOINT="https://course.example:8443",
            HPC_BASE_URL="https://stale.example:9999",
            LLM_MODELS="gemma-3-12b",
            HPC_MODEL_NAME="stale-model",
        )
        assert s.hpc_base_url == "https://course.example:8443"
        assert s.hpc_model_name == "gemma-3-12b"


class TestEndpointNormalisation:
    def test_a_bare_host_and_port_gains_a_scheme(self):
        assert settings(LLM_ENDPOINT="llm.example.sg:8443").hpc_base_url == (
            "https://llm.example.sg:8443"
        )

    def test_shell_quotes_are_stripped(self):
        assert settings(LLM_ENDPOINT='"https://q.example:8443"').hpc_base_url == (
            "https://q.example:8443"
        )
        assert settings(LLM_ENDPOINT="'https://q.example:8443'").hpc_base_url == (
            "https://q.example:8443"
        )

    def test_surrounding_whitespace_is_stripped(self):
        assert settings(LLM_ENDPOINT="  https://w.example:8443  ").hpc_base_url == (
            "https://w.example:8443"
        )

    def test_a_full_url_is_left_alone(self):
        url = "http://plain.example:8080/v1"
        assert settings(LLM_ENDPOINT=url).hpc_base_url == url

    def test_unset_stays_empty_rather_than_becoming_a_bare_scheme(self):
        assert settings().hpc_base_url == ""

    def test_the_normalised_url_survives_the_v1_collapse(self):
        """Both fixes compose: a bare host with /v1 still yields one /v1 path."""
        from app.llm.hpc import CHAT_PATH, normalise_base_url

        resolved = settings(LLM_ENDPOINT="llm.example.sg:8443/v1").hpc_base_url
        assert (normalise_base_url(resolved) + CHAT_PATH).count("/v1") == 1


class TestModelSelection:
    @pytest.mark.parametrize(
        "value",
        ["gemma-3-12b,llama-3-70b", "gemma-3-12b;llama-3-70b", "gemma-3-12b llama-3-70b"],
    )
    def test_the_first_of_several_models_is_used(self, value):
        """A request names exactly one model, however many are published."""
        assert settings(LLM_MODELS=value).hpc_model_name == "gemma-3-12b"

    def test_spacing_around_separators_is_tolerated(self):
        assert settings(LLM_MODELS=" gemma-3-12b , llama-3-70b ").hpc_model_name == "gemma-3-12b"

    def test_a_single_model_is_unchanged(self):
        assert settings(LLM_MODELS="gemma-3-12b").hpc_model_name == "gemma-3-12b"

    def test_an_explicit_legacy_name_cannot_override_the_course_list(self):
        """Documented consequence of the precedence: LLM_MODELS wins outright."""
        s = settings(LLM_MODELS="gemma-3-12b,llama-3-70b", HPC_MODEL_NAME="llama-3-70b")
        assert s.hpc_model_name == "gemma-3-12b"
