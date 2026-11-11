"""A hosted judge must never be the only path to a verdict.

`judge_fail_open` is False, so a judge that cannot answer blocks the request.
Moving the two large judges to Groq buys latency; it must not also buy a way for
a free-tier rate limit to take the assistant offline. Every hosted failure falls
back to the local model, and the guard never leaves the machine.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.llm import judge as J


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


#: Every hosted setting, pinned on each test. Without this the suite reads the
#: developer's own .env: setting HOSTED_JUDGE_ENABLED=false there to try a local
#: run turned four of these red, which is a test that reports the machine it ran
#: on rather than the code.
_DEFAULTS = {
    "HOSTED_JUDGE_ENABLED": "true",
    "JUDGE_API_KEY": "",
    "HOSTED_JUDGE_MODEL": "",
    "HOSTED_FAITH_MODEL": "",
}


def _configure(monkeypatch, **env: str) -> None:
    for key, value in {**_DEFAULTS, **env}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


class TestRouting:
    def test_the_master_switch_forces_local_without_clearing_anything(self, monkeypatch):
        """The point of the flag: a hosted/local comparison should not require
        moving the key and model names in and out of .env."""
        _configure(
            monkeypatch,
            HOSTED_JUDGE_ENABLED="false",
            JUDGE_API_KEY="k",
            HOSTED_JUDGE_MODEL="m1",
            HOSTED_FAITH_MODEL="m2",
        )
        assert J._hosted_model("input") == ""
        assert J._hosted_model("fields") == ""
        assert J._hosted_model("faith") == ""

    def test_the_switch_defaults_on_so_a_configured_key_is_used(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="m1")
        assert J._hosted_model("input") == "m1"

    def test_no_key_means_everything_stays_local(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="", HOSTED_JUDGE_MODEL="qwen/qwen3-32b")
        assert J._hosted_model("input") == ""
        assert J._hosted_model("faith") == ""

    def test_no_model_means_everything_stays_local(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="", HOSTED_FAITH_MODEL="")
        assert J._hosted_model("input") == ""
        assert J._hosted_model("faith") == ""

    def test_input_and_fields_share_the_judge_model(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="m1")
        assert J._hosted_model("input") == "m1"
        assert J._hosted_model("fields") == "m1"

    def test_faithfulness_can_differ_and_otherwise_follows(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="m1", HOSTED_FAITH_MODEL="m2")
        assert J._hosted_model("faith") == "m2"
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="m1", HOSTED_FAITH_MODEL="")
        assert J._hosted_model("faith") == "m1"

    def test_the_guard_never_goes_hosted(self, monkeypatch):
        """Harm classification stays on the local Llama Guard, always."""
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="m1", HOSTED_FAITH_MODEL="m2")
        assert J._hosted_model("guard") == ""


class TestFallback:
    @pytest.mark.parametrize(
        "failure",
        [
            httpx.ConnectTimeout("timed out"),
            httpx.HTTPStatusError("429", request=None, response=None),
            ValueError("empty completion"),
            RuntimeError("something unforeseen"),
        ],
    )
    async def test_any_hosted_failure_falls_back_to_local(self, monkeypatch, failure):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="hosted-model")

        async def _boom(*_a, **_k):
            raise failure

        async def _local(model, system, user, *, json_mode):
            return '{"ok":true}', 7

        monkeypatch.setattr(J, "_hosted_chat", _boom)
        monkeypatch.setattr(J, "_chat", _local)

        raw, ms, served_by = await J._complete(
            "input", "local-model", "sys", "user", json_mode=True
        )
        assert raw == '{"ok":true}'
        assert served_by == "local-model", "a hosted failure must not surface as a hosted verdict"

    async def test_a_hosted_success_is_reported_as_hosted(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="hosted-model")

        async def _ok(model, system, user, *, json_mode):
            return '{"ok":true}', 42, "primary"

        async def _never(*_a, **_k):
            raise AssertionError("local model must not be called when the hosted one answers")

        monkeypatch.setattr(J, "_hosted_chat", _ok)
        monkeypatch.setattr(J, "_chat", _never)

        raw, ms, served_by = await J._complete(
            "input", "local-model", "sys", "user", json_mode=True
        )
        # The key is named as well as the model: a run that quietly spent itself
        # on the backup key should be visible in the log, not inferred later.
        assert served_by == "hosted:hosted-model@primary"
        assert ms == 42

    async def test_the_backup_key_is_named_in_the_verdict(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="k", HOSTED_JUDGE_MODEL="hosted-model")

        async def _ok(model, system, user, *, json_mode):
            return '{"ok":true}', 42, "backup"

        monkeypatch.setattr(J, "_hosted_chat", _ok)
        _raw, _ms, served_by = await J._complete(
            "input", "local-model", "sys", "user", json_mode=True
        )
        assert served_by == "hosted:hosted-model@backup"

    async def test_a_local_failure_still_raises(self, monkeypatch):
        """Fallback is for the hosted leg. If the local model fails too, the
        request must fail closed exactly as it did before any of this."""
        _configure(monkeypatch, JUDGE_API_KEY="", HOSTED_JUDGE_MODEL="")

        async def _boom(*_a, **_k):
            raise J.JudgeUnavailable("ollama down")

        monkeypatch.setattr(J, "_chat", _boom)

        with pytest.raises(J.JudgeUnavailable):
            await J._complete("input", "local-model", "sys", "user", json_mode=True)
