"""A story *about the client's own data* is an in-scope question (D.3).

The out-of-scope list matches phrases rather than bare words, which was enough
to separate "tell me a story about dragons" from "what's the story with my
portfolio?" -- but not enough to separate it from "tell me a story about my risk
category", a legitimate request for an explanation in a narrative framing. That
refusal is the over-refusal half of D.3, so both populations are pinned here:
loosening the rule must not re-admit the off-topic cases.
"""

from __future__ import annotations

import pytest

from app.orchestration.intent import Intent, classify


def _is_refused(message: str) -> bool:
    return classify(message).intent is Intent.OUT_OF_SCOPE


@pytest.mark.parametrize(
    "message",
    [
        "Tell me a story about my risk category",
        "Tell me a story about my portfolio",
        "Write a story about rebalancing",
        "Write a poem about my holdings",
        # Colloquial, and the reason the list never matched bare "story".
        "What's the story with my portfolio?",
        "Tell me what my portfolio means",
    ],
)
def test_narrative_framing_of_an_in_scope_question_is_answered(message):
    assert not _is_refused(message)


@pytest.mark.parametrize(
    "message",
    [
        "Tell me a story about dragons.",
        "Write me a poem about the sea.",
        "Write me a story about a knight.",
        # The subject is autumn. A portfolio mentioned three words later does not
        # make this a portfolio question -- this is the case that stops the
        # subject window from simply being widened.
        "Could you draft a short poem about autumn for my portfolio newsletter? "
        "Actually just the poem.",
    ],
)
def test_narrative_framing_of_an_off_topic_subject_is_refused(message):
    assert _is_refused(message)


@pytest.mark.parametrize(
    "message",
    [
        "What's a good pasta recipe? Asking before I check my holdings.",
        "What's the weather like in Tokyo this week for my trip?",
        "Plan me a 2-day itinerary for Paris.",
        "Write me a Python script to sort a list.",
    ],
)
def test_off_topic_subjects_are_refused_whatever_else_the_message_mentions(message):
    """Topic terms are unconditional: a recipe stays off-topic even when the
    message also mentions holdings, which `narrative_is_off_topic` never sees."""
    assert _is_refused(message)
