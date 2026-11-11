"""Confidence and uncertainty (C.4).

Derived from observable signals, never asked of the model: a self-reported number
is not a measurement. Four signals, each a different failure mode:

* retrieval quality  -- nothing cleared the relevance floor
* scope              -- the question is outside the advisory domain
* profile complete   -- the answer rests on fields we do not have
* ambiguity          -- we *can* answer, but not which question was asked

A response crossing the low-confidence threshold is routed into the C.5 review
surface rather than left as a banner. A warning a client can scroll past is not
routing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.config import get_settings
from app.orchestration.guardrails import OWN_FIELD as _FIELD
from app.orchestration.guardrails import _enabled
from app.orchestration.intent import (
    REQUIRES_GROUNDING,
    Classification,
    Intent,
)
from app.rag.retrieve import RetrievalResult

#: Applied per missing profile field the answer would have relied on.
INCOMPLETENESS_PENALTY = Decimal("0.05")
MAX_INCOMPLETENESS_PENALTY = Decimal("0.25")
#: Ceiling for an answer with no policy grounding behind it.
UNGROUNDED_CEILING = Decimal("0.55")


#: Asking for a field of the client's own profile on file. Answered by reading
#: the record back, so a missing policy passage says nothing about its accuracy.
_OWN_RECORD = re.compile(
    rf"\bmy\s+(?:\w+\s+){{0,2}}?{_FIELD}\b"
    # "the salary of me": how the pipeline resolves "the user with mail id <own email>".
    rf"|\b{_FIELD}\s+of\s+me\b"
    r"|\bhow many (?:dependants|dependents|children|kids) do i have\b"
    r"|\bwhat (?:does|do) my portfolio hold"
    r"|\bwhen i invested\b"
    r"|\bwhat did i (?:buy|sell|invest)",
    re.IGNORECASE,
)
#: Words that turn a lookup into an explanation, which does need policy behind it.
_EXPLANATION = re.compile(
    r"\b(?:why|explain|affects?|impacts?|means?|should|recommend|change|improve|if|"
    r"compare|suitable|eligible|enough|influence)\b",
    re.IGNORECASE,
)


def is_own_record_lookup(message: str) -> bool:
    """'What is my monthly salary?' -- a read-back, not advice."""
    return bool(_OWN_RECORD.search(message)) and not _EXPLANATION.search(message)


@dataclass(frozen=True, slots=True)
class Confidence:
    #: None means abstained. Never 0 -- that would be indistinguishable from a
    #: confident negative.
    value: Decimal | None
    abstained: bool
    reasons: tuple[str, ...] = ()
    ambiguity_options: tuple[str, ...] = ()
    requires_review: bool = False
    cautions: tuple[str, ...] = field(default=())

    @property
    def label(self) -> str:
        if self.abstained:
            return "abstained"
        assert self.value is not None
        if self.value >= Decimal("0.85"):
            return "high"
        return "medium" if self.value >= get_settings().low_confidence_threshold else "low"


def assess(
    *,
    classification: Classification,
    retrieval: RetrievalResult,
    missing_profile_fields: tuple[str, ...] = (),
    model_hint: Decimal | None = None,
    message: str = "",
) -> Confidence:
    settings = get_settings()

    if classification.intent is Intent.OUT_OF_SCOPE:
        return Confidence(
            value=None,
            abstained=True,
            reasons=("out_of_scope",),
            cautions=("This falls outside what this platform advises on.",),
        )

    if classification.is_ambiguous:
        # Not a shortage of information -- a surplus of readings. Ask.
        return Confidence(
            value=None,
            abstained=True,
            reasons=("ambiguous_intent",),
            ambiguity_options=classification.ambiguity_options,
            requires_review=True,
            cautions=("That question has more than one reasonable reading.",),
        )

    record_lookup = is_own_record_lookup(message)

    # `abstain` is a switch only so D.1 can show the ungrounded 'before'; always on in use.
    if (
        classification.intent in REQUIRES_GROUNDING
        and not retrieval.grounded
        and not record_lookup
        and _enabled("abstain")
    ):
        return Confidence(
            value=None,
            abstained=True,
            reasons=("no_supporting_source",),
            requires_review=True,
            cautions=(
                "No passage in the firm's materials supports an answer to this, "
                "so it would be wrong to give you one.",
            ),
        )

    reasons: list[str] = []
    cautions: list[str] = []

    # Holdings and profile fields are account records, not a policy passage.
    # Missing a policy chunk is not a reason to call the balance or salary unsupported.
    if record_lookup or (
        classification.intent in (Intent.HOLDINGS_QUERY, Intent.TRANSACTION_HISTORY)
        and not retrieval.grounded
    ):
        value = Decimal("0.90")
        if model_hint is not None:
            value = min(value, model_hint)
        value = max(Decimal("0.01"), min(value, Decimal("1")))
        low = value < settings.low_confidence_threshold
        return Confidence(
            value=value.quantize(Decimal("0.0001")),
            abstained=False,
            reasons=("account_records",) if not low else ("account_records", "below_threshold"),
            requires_review=low,
            cautions=("Please review this before acting on it.",) if low else (),
        )

    # Start from the retrieval evidence, not from the model's opinion of itself.
    if retrieval.grounded and retrieval.chunks:
        value = max(c.score for c in retrieval.chunks)
    else:
        value = UNGROUNDED_CEILING
        reasons.append("ungrounded")
        cautions.append("This answer is not backed by a specific policy passage.")

    if model_hint is not None:
        # The stub's documented per-intent value acts as a ceiling, so a canned
        # hedge cannot be presented as if it were well grounded.
        value = min(value, model_hint)

    if missing_profile_fields:
        penalty = min(
            INCOMPLETENESS_PENALTY * len(missing_profile_fields), MAX_INCOMPLETENESS_PENALTY
        )
        value -= penalty
        reasons.append("incomplete_profile")
        cautions.append(
            "Some of your profile is still blank, so this is based on partial information: "
            + ", ".join(missing_profile_fields[:4])
            + "."
        )

    value = max(Decimal("0.01"), min(value, Decimal("1")))
    low = value < settings.low_confidence_threshold
    if low:
        reasons.append("below_threshold")
        cautions.append("Please review this before acting on it.")

    return Confidence(
        value=value.quantize(Decimal("0.0001")),
        abstained=False,
        reasons=tuple(reasons),
        requires_review=low,
        cautions=tuple(cautions),
    )
