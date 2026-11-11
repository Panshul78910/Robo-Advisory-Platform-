"""Recover attacks hidden by encoding or by another language.

Two Week 10 techniques land here, and nowhere else:

- Obfuscation and encoding: look-alike letters, missing spaces, leetspeak,
  Caesar ciphers including ROT13, ROT47, Atbash, reversed text, hex, binary,
  URL-encoding, HTML entities, Base32, or Base64.
- A different input language: the same attack written in another language.
  The phrase list lives in `languages.py`.

Recovered English is handed back to the existing checks, including out-of-scope
requests and questions that ask for a figure the platform does not have.
A normal question in another language is not on these lists, so it is left alone.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import functools
import html
import re
import unicodedata
import urllib.parse

from app.orchestration.languages import phrase_channel

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060\u00ad]")

# Letters that look like ASCII and are used to slip past an English word list.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "і": "i",
        "А": "A",
        "Е": "E",
        "О": "O",
        "Р": "P",
        "С": "C",
        "У": "Y",
        "Х": "X",
        "І": "I",
        "ο": "o",
        "α": "a",
        "ε": "e",
    }
)

_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)

# Single letters broken up by spaces or dots: "i g n o r e", "i.g.n.o.r.e".
_SPACED = re.compile(r"\b(?:[A-Za-z][.\s]+){3,}[A-Za-z]\b")

_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_B32 = re.compile(r"[A-Z2-7]{16,}={0,6}")
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{16,}\b")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})")
_BINARY = re.compile(r"(?:[01]{8}[ \t]*){4,}")
_COMMON_WORDS = frozenset(
    {"the", "what", "my", "is", "and", "why", "how", "your", "portfolio", "risk", "this", "that"}
)

# =====================================================================
# ADVERSARIAL PATTERNS & BENIGN WHITELIST (MINIMIZE FALSE POSITIVES)
# =====================================================================
# Require imperative action verbs paired with system entities to prevent
# blocking benign queries that mention policies, rules, or CKA requirements.
RE_SYSTEM_OVERRIDE = re.compile(
    r"\b(ignore|bypass|override|forget)\s+(all\s+)?(previous\s+)?(instructions?|prompts?|system\s+rules?)\b",
    re.IGNORECASE,
)

RE_PROMPT_LEAK = re.compile(
    r"\b(repeat|print|show|display|reveal)\s+(your\s+)?(system\s+prompt|initial\s+instructions?)\b",
    re.IGNORECASE,
)

RE_ROLEPLAY_JAILBREAK = re.compile(
    r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+(an?\s+)?(unrestricted|dan|developer\s+mode|jailbroken)\b",
    re.IGNORECASE,
)

# Explicitly permit legitimate domain inquiries and account checks
BENIGN_FINANCIAL_PATTERNS = [
    r"\b(asset\s+allocation|portfolio\s+balance|rebalanc\w+)\b",
    r"\b(customer\s+knowledge\s+assessment|cka(\s+requirement)?)\b",
    r"\b(risk\s+(profile|score|assessment)|suitability\s+policy)\b",
    r"\b(which|what|how|do\s+i\s+need|can\s+you\s+explain|show\s+me)\b",
]

RE_BENIGN_WHITELIST = re.compile("|".join(BENIGN_FINANCIAL_PATTERNS), re.IGNORECASE)


def is_whitelisted_query(text: str) -> bool:
    """Check if the text matches benign domain terminology."""
    return bool(RE_BENIGN_WHITELIST.search(text))


#: The attack channel each pattern belongs to, named as the plain-English checks
#: name it. Reporting a generic "evasion" instead cost the audit trail its reason
#: code -- every block, whatever it was, reached the officer queue as
#: `ai_safety:evasion`, so a reviewer could not tell a prompt-leak probe from an
#: override attempt. A roleplay jailbreak is an injection by another route, and
#: gets the same reply the injection check gives.
_PATTERN_CHANNELS: tuple[tuple[re.Pattern[str], str], ...] = (
    (RE_SYSTEM_OVERRIDE, "injection"),
    (RE_PROMPT_LEAK, "leak"),
    (RE_ROLEPLAY_JAILBREAK, "injection"),
)


def disguised_channel(user_input: str) -> str | None:
    """Attack channel of a message once obfuscation is undone, or None.

    What this adds over the plain-English checks is the de-obfuscation: the
    patterns are matched against homoglyph-folded text and every recovered
    decoding, not just what was typed. The channel it returns is the caller's to
    gate and to word -- this function decides what was attempted, not what the
    client is told or whether the check is switched on.
    """
    cleaned = _readable(user_input)

    # Allow whitelisted benign questions immediately
    if is_whitelisted_query(cleaned):
        return None

    # Inspect all recovered/disguised texts alongside the raw input
    for candidate in [cleaned, *disguised_texts(user_input)]:
        for pattern, channel in _PATTERN_CHANNELS:
            if pattern.search(candidate):
                return channel

    return None


def language_channel(message: str) -> str | None:
    """Attack channel when a known phrase in another language is present.

    One of 'injection', 'leak', 'scope', or 'hallucination'.
    Homoglyph folding is not applied here: Cyrillic letters that also look
    like Latin are real letters in Russian and Ukrainian.
    """
    text = unicodedata.normalize("NFKC", message)
    text = _ZERO_WIDTH.sub("", text)
    return phrase_channel(text)


# One extra unwrap catches a cipher inside Base64 or percent-encoding.
# The cap stops nested decoding from trying every Caesar shift of every Caesar shift.
_MAX_RECOVERED = 32


def disguised_texts(message: str) -> list[str]:
    """English sentences recovered from an obfuscated message.

    The original message is not returned. Callers already check it directly.
    Recovered text is only for detection. It is never sent to the model as an
    instruction. The same message is often scanned more than once in a request
    (the text itself, then each history turn), so the result is cached.
    """
    if len(message) > 20000:
        message = message[:8000] + "\n" + message[-8000:]
    return list(_disguised_cached(message))


@functools.lru_cache(maxsize=64)
def _disguised_cached(message: str) -> tuple[str, ...]:
    return tuple(_disguised_uncached(message))


def _disguised_uncached(message: str) -> list[str]:
    original = " ".join(message.split()).lower()
    seen: set[str] = set()
    found: list[str] = []
    state = {"limit": 20}

    def add(text: str | None) -> None:
        if len(found) >= state["limit"] or not text:
            return
        cleaned = " ".join(text.split())
        key = cleaned.lower()
        if len(key) < 8 or key in seen or key == original:
            return
        seen.add(key)
        found.append(cleaned)

    _collect(message, add)
    # Prefer a decoded sentence over the raw token. The first pass stops at 20
    # so this second unwrap still has room to recover the English underneath.
    state["limit"] = _MAX_RECOVERED
    seeds = [text for text in found if " " in text and _worth_second_pass(text)][:4]
    for seed in seeds:
        if len(found) >= _MAX_RECOVERED:
            break
        _collect(seed, add)
    return found


def _worth_second_pass(text: str) -> bool:
    """True when a recovered string may itself still be wrapped."""
    if _B64.search(text) or _HEX.search(text) or "%" in text or "&" in text:
        return True
    return _cipherish(text)


def _collect(message: str, add) -> None:
    base = _readable(message)
    add(base)
    # Encodings before ciphers. A Caesar sweep of a Base64 blob used to fill the
    # recovery cap and the real decode never got a slot.
    add(_leet(base))
    add(_collapse_spaced(base))
    add(_leet(_collapse_spaced(base)))
    add(_unescape(message))
    if "%" in message:
        add(urllib.parse.unquote(message))
        add(_readable(urllib.parse.unquote(message)))
    if "&" in message:
        add(html.unescape(message))
    add(_rot13(base))
    tokens = re.findall(r"[A-Za-z]{8,}", base)
    # A long ordinary sentence does not need every word rotated.
    if len(tokens) <= 24 or _cipherish(base):
        for token in tokens[:24]:
            add(_rot13(token))
    for token in _B64.findall(message):
        add(_b64(token))
    for token in _B32.findall(message):
        add(_b32(token))
    for token in _HEX.findall(message):
        add(_hex(token))
    add(_binary(message))
    for target in _cipher_targets(base):
        add(_reverse(target))
        add(_atbash(target))
        add(_rot47(target))
        for shift in range(1, 26):
            if shift == 13:
                continue
            decoded = _caesar(target, shift)
            add(decoded)
            if _reads_as_english(decoded):
                break


def _reads_as_english(text: str) -> bool:
    words = [word.strip("?.!,").lower() for word in text.split()]
    hints = _COMMON_WORDS | {
        "ignore",
        "print",
        "system",
        "prompt",
        "instructions",
        "previous",
        "reveal",
        "formula",
    }
    return any(word in hints for word in words)


def carry_forward(
    history: tuple[str, ...],
    message: str,
    *,
    is_attack,
    is_benign,
) -> list[str]:
    """Earlier fragments of a split attack, excluding turns already decided.

    A finished attack, or a real advisory question, ends the run. Otherwise the
    last few unfinished turns are kept so the next message can complete them.
    """
    pending: list[str] = []
    for turn in history[-6:]:
        if is_attack(turn) or is_benign(turn):
            pending.clear()
            continue
        piece = " ".join(turn.split())
        if piece:
            pending.append(piece)
        pending = pending[-3:]
    if not pending or is_benign(message):
        return []
    return pending


def _readable(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    return text.translate(_HOMOGLYPHS)


def _leet(text: str) -> str:
    return text.translate(_LEET)


def _collapse_spaced(text: str) -> str:
    return _SPACED.sub(lambda match: re.sub(r"[.\s]+", "", match.group(0)), text)


def _unescape(text: str) -> str | None:
    if r"\u" not in text and r"\x" not in text:
        return None

    def repl(match: re.Match) -> str:
        hex_digits = match.group(1) or match.group(2)
        return chr(int(hex_digits, 16))

    return _UNICODE_ESCAPE.sub(repl, text)


def _rot13(text: str) -> str:
    return codecs.decode(text, "rot_13")


def _b64(token: str) -> str | None:
    if len(token) % 4 == 1:
        return None
    token = token.replace("-", "+").replace("_", "/")
    padded = token + ("=" * ((4 - len(token) % 4) % 4))
    try:
        raw = base64.b64decode(padded, validate=True)
        text = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    letters = sum(ch.isalpha() for ch in text)
    if letters < 8 or (" " not in text and "\n" not in text):
        return None
    if letters / max(len(text), 1) < 0.5:
        return None
    return text


def _hex(token: str) -> str | None:
    blob = token[2:] if token.lower().startswith("0x") else token
    if len(blob) % 2:
        return None
    try:
        text = bytes.fromhex(blob).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if sum(ch.isalpha() for ch in text) < 8 or " " not in text:
        return None
    return text


def _b32(token: str) -> str | None:
    padded = token + ("=" * ((8 - len(token) % 8) % 8))
    try:
        text = base64.b32decode(padded, casefold=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if sum(ch.isalpha() for ch in text) < 8 or " " not in text:
        return None
    return text


def _binary(text: str) -> str | None:
    chunks = _BINARY.findall(text)
    if not chunks:
        return None
    bits = "".join(ch for ch in chunks[0] if ch in "01")
    try:
        raw = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits) - 7, 8))
        decoded = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None
    if sum(ch.isalpha() for ch in decoded) < 8:
        return None
    return decoded


def _cipher_targets(text: str) -> list[str]:
    """Short stretches worth a Caesar/Atbash pass. Never the whole essay."""
    if len(text) <= 480 and _cipherish(text):
        return [text]
    found: list[str] = []
    if len(text) > 480 and _cipherish(text):
        mid = len(text) // 2
        for chunk in (text[:400], text[max(0, mid - 200) : mid + 200], text[-400:]):
            if len(chunk) >= 12:
                found.append(chunk)
    for piece in re.split(r"[\n.?!。]+", text):
        piece = piece.strip()
        if 12 <= len(piece) <= 400 and _cipherish(piece) and piece not in found:
            found.append(piece)
        if len(found) >= 6:
            break
    return found[:6]


def _cipherish(text: str) -> bool:
    """True when the text does not already read as an ordinary English question."""
    words = [word.strip("?.!,").lower() for word in text.split()]
    if len(text) < 12:
        return False
    if len(words) <= 1:
        return True
    return not any(word in _COMMON_WORDS for word in words)


def _reverse(text: str) -> str:
    return text[::-1]


def _caesar(text: str, shift: int) -> str:
    def shift_char(ch: str) -> str:
        if "a" <= ch <= "z":
            return chr((ord(ch) - 97 - shift) % 26 + 97)
        if "A" <= ch <= "Z":
            return chr((ord(ch) - 65 - shift) % 26 + 65)
        return ch

    return "".join(shift_char(ch) for ch in text)


def _atbash(text: str) -> str:
    def flip(ch: str) -> str:
        if "a" <= ch <= "z":
            return chr(122 - (ord(ch) - 97))
        if "A" <= ch <= "Z":
            return chr(90 - (ord(ch) - 65))
        return ch

    return "".join(flip(ch) for ch in text)


def _rot47(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 33 <= code <= 126:
            out.append(chr(33 + (code - 33 - 47) % 94))
        else:
            out.append(ch)
    return "".join(out)
