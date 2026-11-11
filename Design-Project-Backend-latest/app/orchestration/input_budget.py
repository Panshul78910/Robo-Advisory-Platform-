"""Bound how much of a user message is decoded, judged, or shown to the model.

Oversized text is rejected with a clear error. Repeated lines and emoji runs are
collapsed for the model only, after security checks have seen the original.
Nothing unique is dropped to make a message fit: if the unique text still cannot
fit the context window, the context builder refuses it.
"""

from __future__ import annotations

import re

from app.core.config import get_settings
from app.core.errors import ValidationError

_EMOJI_RUN = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "]+"
)
_SUSPICIOUS = re.compile(
    r"ignore|disregard|instruction|prompt|system|formula|abaikan|ignora|olvida|"
    r"忽略|无视|வழிமுறை|arahan|reveal|override|jailbreak|其他客户|其他客戶",
    re.IGNORECASE,
)


def ensure_within_limit(message: str) -> None:
    """Reject before decoding or retrieval. The count is characters, not tokens."""
    limit = get_settings().max_user_message_chars
    if len(message) > limit:
        raise ValidationError(
            f"That message is too long to process ({len(message)} characters). "
            f"Please shorten it to {limit} characters or fewer and try again."
        )


def condense_for_model(text: str) -> str:
    """Drop repeated long lines and shrink emoji runs. Unique sentences stay.

    A line is repeated only when the same text already occurred. Short lines are
    kept even when repeated, because "yes" twice can matter. The original message
    is what the security checks and the audit log use.
    """
    collapsed = _EMOJI_RUN.sub(
        lambda match: match.group(0)[:2] + "…" if len(match.group(0)) >= 12 else match.group(0),
        text,
    )
    lines: list[str] = []
    seen: set[str] = set()
    for raw in collapsed.splitlines():
        key = " ".join(raw.split())
        if not key:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if len(key) > 40 and key in seen:
            continue
        seen.add(key)
        lines.append(key)
    return "\n".join(lines).strip()


def judge_excerpt(text: str, limit: int = 1800) -> str:
    """Head, tail, and any suspicious span, inside the judge's existing budget.

    Sending only the first 2,000 characters hid an instruction pasted at the end
    and made the judge slower when the whole essay was attached. The excerpt is
    never longer than `limit`, so this does not add judge tokens.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    parts: list[str] = []
    match = _SUSPICIOUS.search(text)
    if match and match.start() > 400:
        start = max(0, match.start() - 80)
        parts.append(text[start : start + 600])
    parts.append(text[:500])
    parts.append(text[-400:])
    excerpt = "\n...\n".join(parts)
    return excerpt[:limit]
