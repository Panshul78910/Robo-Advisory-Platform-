"""The prompt envelope must not reach the client.

Every reply here other than the controls is text the live system actually
produced. D-2387 is the one in the bug report: it names all five blocks and all
three trust levels, quotes neither `prompts/system.md` nor any corpus passage,
and passed all five egress checks.

The controls matter as much as the leaks. A guard on three underscored literals
is only safe if ordinary answers never contain them, so the second class of test
is prose that talks about history, systems, records and policy in the ordinary
way and must survive untouched.
"""

from __future__ import annotations

import pytest

from app.llm.client import BlockName, Trust
from app.orchestration import guardrails as g

ARGS = {"intent": "general", "allowed_chunk_refs": frozenset(), "facts": {}}


def blocked(text: str) -> bool:
    return g.egress_scaffold(text=text, **ARGS).blocked


# --------------------------------------------------------------------------
# The three real leaks.
# --------------------------------------------------------------------------

D2387 = """The client records and policy passages arrive in a structured text format delimited by
specific tags. Each section is enclosed between a start tag and an end tag.

1. Start Tag: `<<<NAME trust=LEVEL>>>`
- `NAME`: Identifies the type of content (e.g., `CLIENT_FACTS`, `RETRIEVED_POLICY`, `HISTORY`,
  `USER_MESSAGE`).
- `trust=LEVEL`: Indicates the trust level of the content:
- `instruction`: Rules for the assistant.
- `data`: Factual records or policy documents.
- `untrusted-input`: Conversation history and current user messages.

3. End Tag: `<<<END NAME>>>`"""

D2390 = """The information is organized into distinct sections, each separated by specific start
and end markers.

Each section begins with a start tag in the format `<<<NAME trust=LEVEL>>>` and ends with a
corresponding `<<<END NAME>>>` tag. The `NAME` identifies the type of content (such as
`CLIENT_FACTS` for your personal record or `RETRIEVED_POLICY` for firm guidelines)."""

D2391 = """Immediately before your message is the closing tag for the conversation history:
`<<<END HISTORY>>>`.

Immediately after your message is the closing tag for your current input:
`<<<END USER_MESSAGE>>>`."""


@pytest.mark.parametrize(
    "name, reply",
    [("D-2387", D2387), ("D-2390", D2390), ("D-2391", D2391)],
)
def test_the_replies_that_started_this(name: str, reply: str) -> None:
    assert blocked(reply), f"{name} reached the client"


def test_the_old_leak_guard_still_misses_all_three() -> None:
    """Pins why this check had to exist rather than extending `egress_leak`.

    If a future edit makes `egress_leak` catch these, this test fails and the
    new check can be reconsidered. Until then it documents that a line-by-line
    comparison against `prompts/system.md` cannot see a paraphrase.
    """
    for reply in (D2387, D2390, D2391):
        assert not g.egress_leak(text=reply, **ARGS).blocked


# --------------------------------------------------------------------------
# Each token on its own.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fragment",
    [
        "<<<",
        "The block is <<<CLIENT_FACTS trust=data>>> and nothing else.",
        "It ends with <<<END RETRIEVED_POLICY>>>.",
        "The level is trust=data.",
        "It is marked trust=instruction.",
        "History is trust=untrusted-input.",
        "spaced out: trust = data",
        # Two names is an enumeration, not a slip, even with no delimiter.
        "The sections are called CLIENT_FACTS and RETRIEVED_POLICY.",
        "Your record is in CLIENT_FACTS; your question is in USER_MESSAGE.",
    ],
)
def test_single_tokens(fragment: str) -> None:
    assert blocked(fragment)


# --------------------------------------------------------------------------
# One block name alone: renamed, not refused.
# --------------------------------------------------------------------------

D2315 = """Based on the retrieved policy, I cannot answer this question because **paragraph 20
is not present in the provided text**.

The `RETRIEVED_POLICY` block contains excerpts from **CMG-G02_DIGITAL_ADVISORY_GUIDELINES**
covering paragraphs 9-10, 21-23 and 54. There is no text labeled as paragraph 20 in the
supplied data."""


def test_a_lone_block_name_does_not_destroy_a_good_answer() -> None:
    """D-2315 refused to invent a missing paragraph. That answer is worth keeping."""
    assert not blocked(D2315)


def test_but_the_lone_name_is_still_renamed() -> None:
    cleaned = g.strip_internal_vocabulary(D2315)
    assert "RETRIEVED_POLICY" not in cleaned
    assert "retrieved policy" in cleaned
    # The substance survives the rename.
    assert "paragraph 20" in cleaned
    assert "CMG-G02" in cleaned


@pytest.mark.parametrize(
    "name, replacement",
    [
        ("CLIENT_FACTS", "client record"),
        ("RETRIEVED_POLICY", "retrieved policy"),
        ("USER_MESSAGE", "your message"),
    ],
)
def test_each_block_name_is_renamed(name: str, replacement: str) -> None:
    cleaned = g.strip_internal_vocabulary(f"I read that from the {name} section.")
    assert name not in cleaned
    assert replacement in cleaned


def test_every_trust_level_is_covered() -> None:
    """Generated from the enum, so a fourth level is covered on declaration."""
    for t in Trust:
        assert blocked(f"This section is marked trust={t.value} in what I read.")


def test_every_rendered_block_is_covered() -> None:
    """Exactly what `Block.render` emits, for all five names."""
    for name in BlockName:
        rendered = f"<<<{name} trust=data>>>\nbody\n<<<END {name}>>>"
        assert blocked(rendered), f"{name} rendered tag not caught"


# --------------------------------------------------------------------------
# Controls: ordinary answers that must NOT be blocked.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        # `SYSTEM` and `HISTORY` are left out of the pattern precisely for these.
        "Your transaction history shows three trades this quarter.",
        "The firm's scoring system weighs several qualitative factors.",
        "Your portfolio history is available on the dashboard.",
        "This is a simulated advisory environment, not a live trading system.",
        "I hold no history of any prior conversation with you.",
        "SYSTEM MAINTENANCE: the dashboard is unavailable on Sunday.",
        # Ordinary use of the words the attack dressed up.
        "Your client record shows a growth risk category.",
        "The retrieved policy says a client may always choose a lower-risk portfolio.",
        "According to the firm's policy, advice must suit your circumstances.",
        "Your user message history is not retained between sessions.",
        # Markdown and arithmetic that could look like a delimiter.
        "The weights are 60% equities << 40% bonds is wrong; it is the reverse.",
        "a < b and b < c, so a < c.",
        "Use the >> operator to shift right.",
        "The quote was: <<not financial advice>>.",
    ],
)
def test_ordinary_answers_survive(reply: str) -> None:
    assert not blocked(reply)


def test_the_two_ambiguous_names_alone_are_not_enough() -> None:
    """`SYSTEM` and `HISTORY` are ordinary English and are deliberately absent.

    A reply that genuinely enumerates the blocks names the other three too, so
    nothing is lost -- which the D-2391 case above demonstrates.
    """
    assert not blocked("The blocks are SYSTEM and HISTORY.")
    assert blocked("The blocks are SYSTEM, HISTORY, CLIENT_FACTS and USER_MESSAGE.")


def test_the_same_name_repeated_is_still_one_name() -> None:
    """Counts distinct names, so a refusal that mentions one block twice stands."""
    assert not blocked("The RETRIEVED_POLICY block is short. RETRIEVED_POLICY has no para 20.")


def test_pattern_is_generated_from_the_trust_enum() -> None:
    for t in Trust:
        assert t.value in g._SCAFFOLD_HARD.pattern


# --------------------------------------------------------------------------
# Wiring.
# --------------------------------------------------------------------------


def test_registered_in_egress_checks() -> None:
    assert g.egress_scaffold in g.EGRESS_CHECKS


def test_runs_before_pii_so_a_leak_is_blocked_not_redacted() -> None:
    assert g.EGRESS_CHECKS.index(g.egress_scaffold) < g.EGRESS_CHECKS.index(g.egress_pii)


def test_honours_the_leak_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAILS_DISABLE", "leak")
    assert not blocked(D2387)


def test_refuses_with_the_leak_reason() -> None:
    v = g.egress_scaffold(text=D2387, **ARGS)
    assert v.reason == g.R_LEAK
    assert "egress_scaffold" in v.triggered


def test_every_rendered_tag_is_caught_by_the_delimiter_alone() -> None:
    """The drift that left `<<<HISTORY` unguarded at ingress must not recur."""
    for name in BlockName:
        assert g._SCAFFOLD_HARD.search(f"<<<{name} trust=data>>>")
        assert g._SCAFFOLD_HARD.search(f"<<<END {name}>>>")
