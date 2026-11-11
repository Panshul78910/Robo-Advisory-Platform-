"""The welfare check runs first, needs no model, and is logged as a welfare concern."""

from __future__ import annotations

import uuid

import pytest

from app.audit import service as audit
from app.llm.stub import StubLLMClient
from app.orchestration import guardrails
from app.orchestration.pipeline import ChatPipeline
from app.orchestration.welfare import WELFARE_REPLY, is_self_harm_message


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def one(self):
        return self._row

    def first(self):
        return self._row

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None


class FakeConn:
    async def execute(self, statement, params=None):
        return _Result({"id": uuid.uuid4(), "display_no": "D-2401", "status": "pending_review"})


class FakeClients:
    async def history(self, conversation_id, limit=20):
        return []

    async def append_message(self, *args, **kwargs):
        return None


class ExplodingLLM(StubLLMClient):
    async def complete(self, *args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("the welfare path must not call a model")


def pipeline(llm=None) -> ChatPipeline:
    return ChatPipeline(
        FakeConn(), llm=llm or ExplodingLLM(), clients=FakeClients(), client_id=uuid.uuid4()
    )


@pytest.mark.parametrize(
    "message",
    [
        "I want to kill myself",
        "i dont want to live anymore",
        "Thinking about suicide",
        "k1ll myself",
        "I keep wanting to hurt myself",
        "I want to die",
        "no reason to live",
        "everyone would be better off without me",
        "我想自杀",
        "saya nak bunuh diri",
        "I'm planning to end my life",
    ],
)
def test_self_harm_wording_matches(message):
    assert is_self_harm_message(message)


@pytest.mark.parametrize(
    "message",
    [
        "What is my risk category?",
        "Why did you rebalance my portfolio?",
        "Should I kill the position in my bond fund?",
        "My portfolio is dying, what should I do?",
        "Explain the 60/40 allocation",
        "",
    ],
)
def test_ordinary_messages_do_not_match(message):
    assert not is_self_harm_message(message)


async def test_welfare_reply_is_fixed_logged_as_welfare_and_not_a_block(monkeypatch):
    seen: dict = {}

    async def capture(conn, **kwargs):
        seen.update(kwargs)
        return audit.Decision(id=uuid.uuid4(), display_no="D-2401", status=kwargs["status"])

    monkeypatch.setattr("app.orchestration.pipeline.audit.record_decision", capture)

    result = await pipeline().run("I want to kill myself", client_facts={"clientName": "Lim"})

    assert result.text == WELFARE_REPLY
    assert "1767" in result.text and "9151 1767" in result.text
    assert "6389 2222" in result.text and "995" in result.text
    assert "confidential" not in result.text.lower()
    assert seen["status"] == "pending_review"  # not "refused"
    assert seen["escalate"].reason_code == "welfare_concern:self_harm"
    assert not seen["escalate"].reason_code.startswith("ai_safety")
    assert "AI safety" not in seen["output"]
    assert result.requires_review
    assert result.disclosure_applied is False


async def test_welfare_check_runs_before_every_other_check(monkeypatch):
    async def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("a later check ran before the welfare check")

    async def capture(conn, **kwargs):
        return audit.Decision(id=uuid.uuid4(), display_no="D-2401", status=kwargs["status"])

    monkeypatch.setattr("app.orchestration.pipeline.audit.record_decision", capture)
    monkeypatch.setattr(guardrails, "screen_input", boom)
    monkeypatch.setattr(guardrails, "evasion", boom)
    monkeypatch.setattr(guardrails, "is_attack_glossary_question", boom)

    # Over-long input would otherwise be rejected by the input limit first.
    result = await pipeline().run(
        "I want to end my life " + "x" * 50_000, client_facts={"clientName": "Lim"}
    )
    assert result.text == WELFARE_REPLY
