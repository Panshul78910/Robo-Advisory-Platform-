"""Extra guardrails for two red-team techniques (Checklist D.5 / D.1).

  Technique 6  few-shot / many-shot: the client pastes a fake transcript in which
               "the assistant" makes prohibited promises, then asks it to continue.
  Technique 9  output-format manipulation: the client asks for restricted content
               as JSON / a table / a report field instead of prose.

Why the existing checks let these through (verified against the real functions):

  * `ungrounded_numbers` accepts any figure that also appears in the client's own
    message, so "guaranteed 15%" copied from the pasted examples counts as
    "grounded" and is released.
  * `egress_leak` compares the reply to the system prompt as one contiguous
    substring, so splitting a line across JSON items or table cells defeats it.
  * Nothing on the reply side looks for guarantee wording at all.

Every check is switchable for the "before" capture, like the others:

  GUARDRAILS_DISABLE=fewshot,guarantee,format   (or `all`)

Deterministic on purpose: no LLM call, so no extra latency on the shared endpoint.
Names: `fewshot` (ingress), `format` (ingress + structured_leak), `guarantee` (egress).
"""

from __future__ import annotations

import re

from app.orchestration import guardrails as _guardrails
from app.orchestration.guardrails import (
    ALLOW,
    R_LEAK,
    GuardrailVerdict,
    _block,
    _enabled,
    _norm,
    _prompt_fragments,
)

R_FEWSHOT = (
    "I can't continue a conversation pattern supplied in a message. I'll answer your "
    "question directly, based on your own profile and the firm's policy. What would you like to know?"
)
R_FORMAT = (
    "I can't put that into a structured format. I can explain your portfolio, your risk "
    "category and the firm's policy in plain language, or show your own holdings in a table."
)
R_GUARANTEE = (
    "I can't state that. No investment return is guaranteed and any portfolio can lose value. "
    "I can explain the portfolio's risks and the firm's policy instead."
)

# ---------------------------------------------------------------- technique 6
# A pasted transcript: role-labelled turns ("Assistant:", "Advisor:", "AI:" ...).
_ASSISTANT_LABEL = re.compile(
    r"(?<![\w])(assistant|advis[eo]r|robo[- ]?advis[eo]r|chatbot|bot|ai|claude)\s*[:>]\s*\S",
    re.I,
)
_USER_LABEL = re.compile(r"(?<![\w])(user|client|customer|human|q|me)\s*[:>]\s*\S", re.I)
_CONTINUE_CUE = re.compile(
    r"continue (the|this|that) (pattern|conversation|dialogue|format)|"
    r"(same|similar) (style|format|way|manner) as (the |these |those )?(above|examples?|previous)|"
    r"follow (the|these) (examples?|pattern)|keep (answering|replying|going) (like|as) (this|above|before)|"
    r"complete the (conversation|dialogue|pattern)|next (reply|answer|turn) in (this|the) (pattern|conversation)",
    re.I,
)


def fewshot(*, message: str, intent: str, history: tuple[str, ...] = ()) -> GuardrailVerdict:
    """Refuse a message that contains a fabricated assistant transcript."""
    if not _enabled("fewshot"):
        return ALLOW
    assistant_turns = len(_ASSISTANT_LABEL.findall(message))
    user_turns = len(_USER_LABEL.findall(message))
    # Two or more fake assistant turns, or one fake exchange plus "continue the pattern".
    if assistant_turns >= 2 or (assistant_turns >= 1 and user_turns >= 1 and _CONTINUE_CUE.search(message)):
        return _block("fewshot", R_FEWSHOT)
    return ALLOW


# ---------------------------------------------------------------- technique 9
_FORMAT_WORD = (
    r"(json|csv|xml|yaml|toml|html|table|spreadsheet|markdown|code ?block|array|dictionary|"
    r"key[- ]value|report|template|schema|list of fields|column|field)"
)
_RESTRICTED = (
    r"(guarantee\w*|risk[- ]free|"
    r"(your|the|every|each|all) (hidden |internal |system )?(rules|instructions|prompt|guidelines|configuration)|"
    r"(rules?|instructions?|guidelines?|guidance|prompt) you (operate|were given|are given|follow|work|run)\b|"
    r"scor\w+ (weights?|formula|thresholds?|points)|risk[- ]score (weights?|formula|thresholds?|points)|"
    r"(other|another|every|each|all) (clients?|customers?|users?)|nric)"
)
_FORMAT_ATTACK = re.compile(
    rf"{_FORMAT_WORD}\b.{{0,80}}\b{_RESTRICTED}|{_RESTRICTED}\b.{{0,80}}\b{_FORMAT_WORD}", re.I | re.S
)
# Dictating the output: "reply only with 'approved'", "set the approved field to true".
_DICTATION = re.compile(
    r"(reply|respond|answer|output|return)\s+(only|exclusively|just)\s+(with|in)\s+['\"`]?(approved|\{)|"
    r"(set|fill|populate|mark|put)\b.{0,40}\b(approved|eligible|guarantee\w*|override)\b.{0,20}\b(true|yes|y|1)\b",
    re.I | re.S,
)


def format_dictation(*, message: str, intent: str, history: tuple[str, ...] = ()) -> GuardrailVerdict:
    """Refuse restricted content requested as JSON/table/report, and dictated outputs."""
    if not _enabled("format"):
        return ALLOW
    if _FORMAT_ATTACK.search(message) or _DICTATION.search(message):
        return _block("format", R_FORMAT)
    return ALLOW


# --------------------------------------------------------------------- egress
_GUARANTEE = re.compile(
    r"\b(guaranteed?|guarantees|assured returns?|risk[- ]free|riskless|cannot lose|can'?t lose|"
    r"won'?t lose|no risk|zero risk|sure thing|certain to (?:rise|grow|profit|go up)|"
    r"100 ?% (?:safe|secure)|safe bet|surefire|can'?t go wrong)\b",
    re.I,
)
_NEGATION = re.compile(
    r"\b(not|no|never|cannot|can'?t|can not|isn'?t|aren'?t|don'?t|doesn'?t|do not|does not|"
    r"without|nor|neither|rather than|instead of)\b|n't",
    re.I,
)
# A structured cell that asserts a guarantee: `"guaranteed": true`, `| Guarantee | Yes |`.
_GUARANTEE_CELL = re.compile(r"guarantee\w*[\"'\s|:=,]{1,8}(yes|true|y|100 ?%)\b", re.I)


def no_guarantee(
    *, text: str, intent: str, allowed_chunk_refs: frozenset[str], facts: dict | None = None
) -> GuardrailVerdict:
    """Block a reply that promises or implies a guaranteed outcome.

    A negated mention ("returns are not guaranteed", "no investment is risk-free")
    is the required disclosure language and is allowed.
    """
    if not _enabled("guarantee"):
        return ALLOW
    if _GUARANTEE_CELL.search(text):
        return _block("guarantee_cell", R_GUARANTEE)
    for m in _GUARANTEE.finditer(text):
        before = text[max(0, m.start() - 40) : m.start()]
        if _NEGATION.search(before):
            continue
        return _block("guarantee", R_GUARANTEE)
    return ALLOW


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _pieces(fragment: str, size: int = 4) -> list[str]:
    """A prompt line cut into runs of `size` words, squashed. Short runs are dropped."""
    words = fragment.split()
    runs = [" ".join(words[i : i + size]) for i in range(0, len(words), size)]
    return [k for k in (_squash(r) for r in runs) if len(k) >= 14]


def structured_leak(
    *, text: str, intent: str, allowed_chunk_refs: frozenset[str], facts: dict | None = None
) -> GuardrailVerdict:
    """Catch a system-prompt line smuggled out in JSON / table / CSV form.

    `egress_leak` needs a prompt line as one contiguous string. Structured output
    cuts it up and puts keys, quotes, pipes and commas in between. So each long
    prompt line is split into short runs of words, punctuation is stripped from
    both sides, and two or more runs of the same line showing up in the reply
    counts as a leak. One shared run alone is ordinary overlap and is allowed.
    """
    if not _enabled("format"):
        return ALLOW
    squashed = _squash(text)
    for frag in _prompt_fragments():
        pieces = _pieces(frag)
        if len(pieces) >= 2 and sum(1 for k in pieces if k in squashed) >= 2:
            return _block("structured_leak", R_LEAK)
    return ALLOW


INGRESS = [fewshot, format_dictation]
EGRESS = [no_guarantee, structured_leak]

# Registered from this side. The two modules import each other -- this one needs
# `_block` and `_norm`, `guardrails` needs these checks -- so whichever is
# imported second sees the other half-built. Reading an attribute off that
# half-built module is what broke: `import app.orchestration.guardrails_extra`
# on its own raised AttributeError on `guardrails_extra.INGRESS`, because
# `guardrails` reached its own import of this module before `INGRESS` existed.
# Extending from here runs after both lists are defined, in either order.
_guardrails.INGRESS_CHECKS.extend(INGRESS)
_guardrails.EGRESS_CHECKS.extend(EGRESS)
