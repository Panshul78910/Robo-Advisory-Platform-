"""Primary key, then backup key, then whatever the caller falls back to.

The case this exists for is a free tier's rate limit. It arrives as a fast 429,
so the second key costs about a hundred milliseconds and the assistant keeps
working. An expired or revoked key fails just as fast and takes the same path.

What must not happen is a chain that looks like it works: a backup configured
but never reached, or reached and then not reported, so a run that quietly spent
itself on the second key is indistinguishable from one that did not.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.llm import hosted

BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _configure(monkeypatch, **env: str) -> None:
    defaults = {
        "JUDGE_API_KEY": "primary-key",
        "JUDGE_BASE_URL": "https://primary.example/v1",
        "BACKUP_API_KEY": "",
        "BACKUP_BASE_URL": "",
    }
    for key, value in {**defaults, **env}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


class _Recorder:
    """Replays one outcome per attempt and records what was sent."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def install(self, monkeypatch):
        recorder = self

        class _Client:
            def __init__(self, **_k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_a):
                return False

            async def post(self, url, *, json=None, headers=None):
                recorder.calls.append({"url": url, "key": (headers or {}).get("Authorization", "")})
                outcome = recorder.outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome

                class _R:
                    def raise_for_status(self):
                        return None

                    def json(self):
                        return {"choices": [{"message": {"content": outcome}}]}

                return _R()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        return self


def _rate_limited() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://primary.example/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)


class TestTheChain:
    async def test_the_primary_answers_and_the_backup_is_untouched(self, monkeypatch):
        _configure(monkeypatch, BACKUP_API_KEY="backup-key")
        rec = _Recorder("ok").install(monkeypatch)
        content, _ms, credential = await hosted.chat(BODY, timeout=5, purpose="t")
        assert content == "ok"
        assert credential == "primary"
        assert len(rec.calls) == 1

    async def test_a_rate_limited_primary_falls_to_the_backup(self, monkeypatch):
        _configure(monkeypatch, BACKUP_API_KEY="backup-key")
        rec = _Recorder(_rate_limited(), "ok").install(monkeypatch)
        content, _ms, credential = await hosted.chat(BODY, timeout=5, purpose="t")
        assert content == "ok"
        assert credential == "backup", "the caller must be told which key answered"
        assert [c["key"] for c in rec.calls] == ["Bearer primary-key", "Bearer backup-key"]

    @pytest.mark.parametrize(
        ("label", "failure"),
        [
            ("rate limit", _rate_limited()),
            ("timeout", httpx.ConnectTimeout("timed out")),
            ("unforeseen", RuntimeError("boom")),
            ("empty completion", None),
        ],
    )
    async def test_any_primary_failure_reaches_the_backup(self, monkeypatch, label, failure):
        """Not only 429. A revoked key, a 500 or a hung provider all mean the
        same thing to the caller: this key cannot answer right now."""
        _configure(monkeypatch, BACKUP_API_KEY="backup-key")
        first = "   " if failure is None else failure  # blank content is a failure too
        _Recorder(first, "ok").install(monkeypatch)
        content, _ms, credential = await hosted.chat(BODY, timeout=5, purpose="t")
        assert (content, credential) == ("ok", "backup"), label

    async def test_both_failing_raises_so_the_caller_can_fall_back(self, monkeypatch):
        _configure(monkeypatch, BACKUP_API_KEY="backup-key")
        rec = _Recorder(_rate_limited(), _rate_limited()).install(monkeypatch)
        with pytest.raises(hosted.HostedUnavailable):
            await hosted.chat(BODY, timeout=5, purpose="t")
        assert len(rec.calls) == 2, "both keys must be tried before giving up"


class TestConfiguration:
    async def test_no_backup_configured_tries_once(self, monkeypatch):
        _configure(monkeypatch)
        rec = _Recorder(_rate_limited()).install(monkeypatch)
        with pytest.raises(hosted.HostedUnavailable):
            await hosted.chat(BODY, timeout=5, purpose="t")
        assert len(rec.calls) == 1

    async def test_no_key_at_all_raises_without_a_request(self, monkeypatch):
        _configure(monkeypatch, JUDGE_API_KEY="")

        def _never(**_k):
            raise AssertionError("nothing should be sent with no credential")

        monkeypatch.setattr(httpx, "AsyncClient", _never)
        with pytest.raises(hosted.HostedUnavailable):
            await hosted.chat(BODY, timeout=5, purpose="t")

    async def test_the_backup_defaults_to_the_same_provider(self, monkeypatch):
        """A second key on the same account is the usual arrangement."""
        _configure(monkeypatch, BACKUP_API_KEY="backup-key")
        rec = _Recorder(_rate_limited(), "ok").install(monkeypatch)
        await hosted.chat(BODY, timeout=5, purpose="t")
        assert rec.calls[1]["url"].startswith("https://primary.example/v1")

    async def test_the_backup_may_be_a_different_provider(self, monkeypatch):
        _configure(
            monkeypatch,
            BACKUP_API_KEY="backup-key",
            BACKUP_BASE_URL="https://other.example/v1",
        )
        rec = _Recorder(_rate_limited(), "ok").install(monkeypatch)
        await hosted.chat(BODY, timeout=5, purpose="t")
        assert rec.calls[1]["url"] == "https://other.example/v1/chat/completions"

    def test_the_judges_local_switch_does_not_disable_the_chain(self, monkeypatch):
        """`HOSTED_JUDGE_ENABLED=false` forces the *judges* local. The refusal
        rewriter and vagueness check have no local model, so reading it here
        would switch them off rather than redirect them."""
        _configure(monkeypatch, BACKUP_API_KEY="backup-key")
        monkeypatch.setenv("HOSTED_JUDGE_ENABLED", "false")
        get_settings.cache_clear()
        assert [c.name for c in hosted.credentials()] == ["primary", "backup"]
