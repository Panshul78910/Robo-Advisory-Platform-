"""Conversational risk profiling (A.2).

A single chatbot conversation, not a questionnaire -- but the *extraction* of
answers is deterministic. Each slot has a question and a parser, so the same
answers always normalise to the same values and therefore the same score,
however the conversation was phrased or ordered (A.2 reproducibility).

The engine reuses whatever A.1 already captured and asks only for what is
missing, and it refuses to score an incoherent profile, asking instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.profile.validation import Inconsistency, coherence_check

WORD_NUMBERS = {
    "none": 0,
    "zero": 0,
    "no": 0,
    "nil": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _number(text: str) -> Decimal | None:
    cleaned = text.lower().replace(",", "").replace("$", "")
    if (match := re.search(r"\d+(?:\.\d+)?\s*k\b", cleaned)) is not None:
        return Decimal(match.group().rstrip("k ").strip()) * 1000
    if (match := re.search(r"\d+(?:\.\d+)?", cleaned)) is not None:
        try:
            return Decimal(match.group())
        except InvalidOperation:
            return None
    for word, value in WORD_NUMBERS.items():
        if re.search(rf"\b{word}\b", cleaned):
            return Decimal(value)
    return None


def _integer(text: str) -> int | None:
    value = _number(text)
    return int(value) if value is not None and value == value.to_integral_value() else None


def _choice(mapping: dict[str, tuple[str, ...]]) -> Callable[[str], str | None]:
    """Match an answer against per-option keyword sets, longest keyword first.

    Longest-first matters: "not very experienced" must not match on "experienced".
    """
    ordered = sorted(
        ((kw, value) for value, keywords in mapping.items() for kw in keywords),
        key=lambda kv: -len(kv[0]),
    )

    def parse(text: str) -> str | None:
        lowered = f" {text.lower().strip()} "
        for keyword, value in ordered:
            if re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", lowered):
                return value
        return None

    return parse


@dataclass(frozen=True, slots=True)
class Slot:
    field: str
    question: str
    parse: Callable[[str], object | None]
    #: Shown when the answer could not be understood.
    retry: str


SLOTS: tuple[Slot, ...] = (
    Slot(
        "monthly_salary",
        "To start, roughly what is your monthly income before deductions?",
        _number,
        "I did not catch a figure there. Could you give me an approximate monthly "
        "amount, for example 6500?",
    ),
    Slot(
        "monthly_commitments",
        "And roughly how much goes out each month on fixed commitments -- rent or "
        "mortgage, loans, insurance, dependants' costs?",
        _number,
        "Could you give me an approximate monthly figure for your fixed outgoings?",
    ),
    Slot(
        "dependants",
        "How many people depend on your income financially?",
        _integer,
        "Just a number is fine -- for example 0, 1 or 2.",
    ),
    Slot(
        "investment_objective",
        "What are you mainly trying to achieve with this money: preserving your "
        "capital, generating income, a balance of both, growth, or aggressive growth?",
        _choice(
            {
                "aggressive_growth": (
                    "aggressive growth",
                    "maximum growth",
                    "as much as possible",
                    "aggressive",
                ),
                "growth": ("growth", "grow", "capital appreciation", "build wealth"),
                "balanced": ("balanced", "balance", "mix", "both", "moderate"),
                "income": ("income", "dividends", "yield", "regular payout"),
                "preservation": (
                    "preservation",
                    "preserve",
                    "protect",
                    "safe",
                    "capital preservation",
                    "not lose",
                ),
            }
        ),
        "Which comes closest: preservation, income, balanced, growth, or aggressive growth?",
    ),
    Slot(
        "investment_horizon_yrs",
        "Roughly how many years before you expect to need this money back?",
        _integer,
        "A rough number of years is fine -- for example 3, 10 or 20.",
    ),
    Slot(
        "loss_tolerance",
        "If this portfolio fell in value over a year, how much of a fall could you "
        "live with: none at all, a small fall, a moderate one, or a large one?",
        _choice(
            {
                "zero": (
                    "none at all",
                    "none",
                    "zero",
                    "no loss",
                    "nothing",
                    "cannot lose",
                    "can't lose",
                    "not any",
                ),
                "low": ("small", "little", "slight", "minimal", "low", "5%", "a bit"),
                "medium": ("moderate", "medium", "some", "average", "10%", "15%"),
                "high": (
                    "large",
                    "big",
                    "significant",
                    "high",
                    "substantial",
                    "20%",
                    "30%",
                    "a lot",
                ),
            }
        ),
        "Would you say none at all, a small fall, a moderate one, or a large one?",
    ),
    Slot(
        "liquidity_needs",
        "How likely is it that you will need to withdraw a meaningful part of this "
        "money at short notice -- unlikely, possibly, or quite likely?",
        _choice(
            {
                "high": (
                    "quite likely",
                    "very likely",
                    "likely",
                    "often",
                    "high",
                    "yes",
                    "frequently",
                    "short notice",
                ),
                "medium": (
                    "possibly",
                    "possible",
                    "maybe",
                    "sometimes",
                    "medium",
                    "moderate",
                    "occasionally",
                ),
                "low": ("unlikely", "no", "rarely", "never", "low", "not really", "won't need"),
            }
        ),
        "Would you say unlikely, possibly, or quite likely?",
    ),
    Slot(
        "income_stability",
        "How steady is your income -- stable, variable, or unpredictable?",
        _choice(
            {
                "unstable": (
                    "unpredictable",
                    "unstable",
                    "irregular",
                    "unreliable",
                    "between jobs",
                    "unemployed",
                ),
                "variable": (
                    "variable",
                    "varies",
                    "commission",
                    "freelance",
                    "self employed",
                    "self-employed",
                    "seasonal",
                    "fluctuates",
                ),
                "stable": (
                    "stable",
                    "steady",
                    "fixed",
                    "salaried",
                    "regular",
                    "reliable",
                    "permanent",
                ),
            }
        ),
        "Would you describe it as stable, variable, or unpredictable?",
    ),
    Slot(
        "investment_experience",
        "Finally, how much investing have you done before -- none, a little, a fair "
        "amount, or a great deal?",
        _choice(
            {
                "extensive": (
                    "great deal",
                    "extensive",
                    "a lot",
                    "very experienced",
                    "professional",
                    "many years",
                ),
                "moderate": (
                    "fair amount",
                    "moderate",
                    "some experience",
                    "reasonable",
                    "few years",
                    "fairly",
                ),
                "limited": ("a little", "limited", "some", "a bit", "little", "beginner", "new to"),
                "none": ("none", "no experience", "never", "nothing", "first time"),
            }
        ),
        "Would you say none, a little, a fair amount, or a great deal?",
    ),
)

SLOTS_BY_FIELD = {s.field: s for s in SLOTS}


@dataclass(frozen=True, slots=True)
class Turn:
    """One step of the conversation."""

    reply: str
    #: Field the reply is asking about, if any.
    awaiting: str | None
    #: Values parsed this turn, to be written to the profile.
    captured: dict
    complete: bool = False
    inconsistencies: tuple[Inconsistency, ...] = ()


class _Merged:
    """Profile plus values captured this turn, for coherence checking."""

    def __init__(self, profile, captured: dict):
        self._profile, self._captured = profile, captured

    def __getattr__(self, name):
        if name in self._captured:
            return self._captured[name]
        return getattr(self._profile, name)


def opening(profile) -> Turn:
    """First turn: acknowledge what A.1 already told us, then ask for the rest."""
    known = _known_summary(profile)
    missing = profile.missing_fields()
    if not missing:
        return _finish_or_clarify(profile, {}, prefix=known)

    slot = SLOTS_BY_FIELD[missing[0]]
    return Turn(reply=f"{known}{slot.question}", awaiting=slot.field, captured={})


def advance(profile, *, awaiting: str | None, message: str) -> Turn:
    """Parse the answer to `awaiting`, then ask for the next missing field."""
    captured: dict = {}
    if awaiting:
        slot = SLOTS_BY_FIELD[awaiting]
        value = slot.parse(message)
        if value is None:
            return Turn(reply=slot.retry, awaiting=awaiting, captured={})
        captured[awaiting] = value

    merged = _Merged(profile, captured)
    remaining = [f for f in profile.missing_fields() if f not in captured]
    if remaining:
        slot = SLOTS_BY_FIELD[remaining[0]]
        return Turn(reply=slot.question, awaiting=slot.field, captured=captured)

    return _finish_or_clarify(merged, captured)


def _finish_or_clarify(profile, captured: dict, *, prefix: str = "") -> Turn:
    """Score only if the profile hangs together; otherwise ask about the conflict."""
    problems = coherence_check(profile)
    if problems:
        # A.2(iv): do not force a category. Ask about the contradiction.
        return Turn(
            reply=prefix + problems[0].question,
            awaiting=None,
            captured=captured,
            complete=False,
            inconsistencies=tuple(problems),
        )
    return Turn(
        reply=prefix + "Thank you -- that is everything I need. Let me work out your risk profile.",
        awaiting=None,
        captured=captured,
        complete=True,
    )


def _known_summary(profile) -> str:
    """Reference what is already on file rather than re-asking for it (A.2 ii)."""
    parts = []
    if profile.monthly_salary is not None:
        parts.append(f"a monthly income of ${profile.monthly_salary:,.0f}")
    if profile.dependants is not None:
        parts.append(f"{profile.dependants} dependant(s)")
    if profile.investment_horizon_yrs is not None:
        parts.append(f"a horizon of about {profile.investment_horizon_yrs} years")
    if not parts:
        return ""
    return f"I already have {', '.join(parts)} on your file, so I will not ask again. "
