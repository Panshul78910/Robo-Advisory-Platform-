"""Welfare check: self-harm and suicide wording (runs before every other check).

This is deliberately the first thing `ChatPipeline.run` does -- before the input
limit, the attack glossary, the evasion pass, ingress guardrails and the
semantic judge. A person in distress must never be answered by a refusal that
reads like a security block, never wait on a model call, and never be routed
through a classifier that could fail closed on them.

It is pure and synchronous: regex only, no model, no network.

False positives are accepted over false negatives. A message that trips this
check gets a kind, fixed reply and a welfare flag for the officer, which costs
little; a missed one costs a lot.
"""

from __future__ import annotations

import re
import unicodedata

#: Fixed reply. No model call, no promise of confidentiality (it cannot be
#: guaranteed). Numbers: SOS 1767 and CareText 9151 1767 (WhatsApp), IMH Mental
#: Health Helpline 6389 2222, emergency 995 / 999.
WELFARE_REPLY = (
    "I\u2019m a robo-advisor, so I can\u2019t help with this, but your safety matters. "
    "If you\u2019re thinking about harming yourself, please call the Samaritans of "
    "Singapore (SOS) 24-hour hotline on 1767, or message CareText on WhatsApp at "
    "9151 1767. You can also call the IMH Mental Health Helpline on 6389 2222 at "
    "any hour. If you are in immediate danger, call 995 or 999. I\u2019m still here "
    "for your portfolio questions whenever you\u2019d like to come back to them."
)

#: Escalation reason code. Not `ai_safety:*` -- the officer queue must be able
#: to tell a welfare concern apart from a blocked attack.
WELFARE_REASON_CODE = "welfare_concern:self_harm"
WELFARE_RULE = "welfare_self_harm"

# Scan at most this many characters from each end, so a very long message
# cannot make the check slow. The input limit runs after this check.
_SCAN_EDGE = 8000

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})

_SELF = r"(?:my\s*self|me|my\s+(?:own\s+)?(?:life|self))"

_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bsuicid(?:e|al)\b",
        r"\bself[\s-]*harm",
        r"\bself[\s-]*injur",
        r"\bkill(?:ing)?\s+(?:my\s*self|myself)\b",
        r"\bkms\b",
        r"\bend(?:ing)?\s+(?:my\s+(?:own\s+)?life|it\s+all|everything\s+for\s+good)\b",
        r"\btake\s+my\s+own\s+life\b",
        r"\btak(?:e|ing)\s+my\s+life\b",
        r"\b(?:want|wanna|wish|going|plan(?:ning)?|hope|need|decided)\s+(?:to\s+|2\s+)?(?:die|be\s+dead|disappear\s+forever)\b",
        r"\bi\s*(?:'|\u2019)?(?:m|am)\s+going\s+to\s+die\b.*\b(?:myself|by\s+my)\b",
        r"\b(?:don'?t|do\s+not|no\s+longer|never)\s+want\s+to\s+(?:live|be\s+alive|be\s+here|go\s+on|exist)\b",
        r"\bnot\s+want\s+to\s+(?:live|be\s+alive|go\s+on)\s+(?:any\s*more|anymore)\b",
        r"\bno\s+(?:reason|point)\s+(?:in\s+)?(?:to\s+)?(?:live|living|going\s+on|being\s+alive)\b",
        r"\b(?:better|best)\s+off\s+(?:dead|without\s+me)\b",
        r"\beveryone\s+(?:would\s+be|is)\s+better\s+off\s+without\s+me\b",
        r"\b(?:hurt|harm|cut|injure|burn|punish)(?:ing)?\s+(?:my\s*self|myself)\b",
        r"\bcut(?:ting)?\s+my\s+(?:wrists?|arms?|thighs?)\b",
        r"\bslit(?:ting)?\s+my\s+(?:wrists?|throat)\b",
        r"\bhang(?:ing)?\s+my\s*self\b",
        r"\bjump(?:ing)?\s+(?:off|from)\s+(?:a\s+|the\s+|my\s+)?(?:building|bridge|roof|rooftop|hdb|block|flat|balcony|window|cliff|height)",
        r"\b(?:overdos(?:e|ing)\s+on\s+(?:purpose|pills|my)|swallow(?:ing)?\s+(?:all\s+)?(?:my\s+)?pills\s+to)\b",
        r"\bthinking\s+(?:of|about)\s+(?:ending|killing|hurting)\s+(?:it|my\s*self|myself|my\s+life)\b",
        # Malay
        r"\bbunuh\s+diri\b",
        r"\btak\s+nak\s+hidup\s+lagi\b",
        r"\bnak\s+mati\b",
        r"\bcederakan\s+diri\b",
        # Chinese (simplified / traditional)
        r"自杀|自殺|轻生|輕生|想死|不想活|结束生命|結束生命|伤害自己|傷害自己|自残|自殘",
        # Tamil
        r"தற்கொலை",
    )
)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return " ".join(text.split())


def _variants(text: str) -> tuple[str, ...]:
    base = _normalise(text)
    # Digit/symbol substitution ("k1ll myself", "su1c1de") as a second read.
    # Only the substituted copy is added, so real numbers in the first read are
    # untouched.
    leet = base.translate(_LEET)
    return (base,) if leet == base else (base, leet)


def is_self_harm_message(message: str) -> bool:
    """True when the message contains self-harm or suicide wording."""
    if not message:
        return False
    if len(message) > 2 * _SCAN_EDGE:
        message = message[:_SCAN_EDGE] + " " + message[-_SCAN_EDGE:]
    return any(p.search(v) for v in _variants(message) for p in _PATTERNS)
