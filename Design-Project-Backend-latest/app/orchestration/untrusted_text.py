"""Split untrusted document text into financial facts and instructions.

Uploaded files and other external text are data. A paragraph that tries to
override the assistant, reveal the system prompt, or disclose the scoring
model is dropped. The recovered wording is used only to decide that, and is
not returned.

Images are not read. This module does not OCR.
"""

from __future__ import annotations

import json
import pathlib
import re

_MAX_INPUT_CHARS = 20_000
_MAX_PARAGRAPHS = 80
_MAX_KEPT_CHARS = 1_800
_SHORT_LINE = 180

# A line that carries a figure or an account label stays even when a nearby
# short line is an instruction. An instruction that shares a line with a
# figure is still dropped, because the whole line is the instruction.
_FACT_LINE = re.compile(
    r"\d|account|holder|cash|balance|portfolio|units|price|statement|"
    r"penyata|baki|pemegang|akaun|test case|rt-pdf|nav ",
    re.IGNORECASE,
)

_SIDECAR_SUFFIX = ".screened.json"


def partition_untrusted(text: str) -> tuple[str, int]:
    """Return (kept financial text, number of instruction paragraphs removed)."""
    parts = _paragraphs(text)
    if not parts:
        return "", 0
    flagged = [ _is_instruction(part) for part in parts ]
    _flag_split_instructions(parts, flagged)
    kept: list[str] = []
    removed = 0
    for part, bad in zip(parts, flagged, strict=True):
        if bad:
            removed += 1
            continue
        kept.append(part)
    body = "\n".join(kept).strip()
    if len(body) > _MAX_KEPT_CHARS:
        body = body[:_MAX_KEPT_CHARS].rsplit(" ", 1)[0].strip()
    return body, removed


def screen_upload(path: pathlib.Path, content_type: str | None) -> dict:
    """Extract a PDF, drop instruction text, and describe what happened.

    `kept` is safe to place in client facts. Removed wording is not stored.
    """
    if content_type in {"image/png", "image/jpeg"}:
        return {"kept": "", "removed": 0, "extractor": "unsupported-image"}
    if path.suffix.lower() != ".pdf" and content_type != "application/pdf":
        return {"kept": "", "removed": 0, "extractor": "unsupported"}
    body, metadata, extractor = extract_pdf(path)
    kept, removed = partition_untrusted(body)
    if metadata and _is_instruction(metadata):
        removed += 1
    return {"kept": kept, "removed": removed, "extractor": extractor}


def write_screened(upload_path: pathlib.Path, screened: dict) -> pathlib.Path:
    destination = sidecar_path(upload_path)
    destination.write_text(json.dumps(screened), encoding="utf-8")
    return destination


def statements_from_documents(documents: list[dict], *, limit: int = 1) -> str | None:
    """Screened text from the newest upload, capped for the prompt budget."""
    chunks: list[str] = []
    for document in documents[:limit]:
        storage = document.get("storage_path")
        if not storage:
            continue
        path = sidecar_path(pathlib.Path(str(storage)))
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        kept = str(data.get("kept") or "").strip()
        if not kept:
            continue
        name = document.get("filename") or "upload"
        chunks.append(f"{name}: {kept}")
    if not chunks:
        return None
    return "\n".join(chunks)[:_MAX_KEPT_CHARS]


def sidecar_path(upload_path: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(str(upload_path) + _SIDECAR_SUFFIX)


def extract_pdf(path: pathlib.Path) -> tuple[str, str, str]:
    """Return (page text, metadata text, extractor name)."""
    try:
        import pymupdf
    except ImportError:
        return _literal_strings(path), "", "literal"
    try:
        with pymupdf.open(path) as doc:
            meta = doc.metadata or {}
            meta_text = "\n".join(f"{key}: {value}" for key, value in meta.items() if value)
            pages = [page.get_text("text") or "" for page in doc]
    except Exception:
        return _literal_strings(path), "", "literal"
    return "\n\n".join(pages), meta_text, "pymupdf"


def _paragraphs(text: str) -> list[str]:
    clipped = text[:_MAX_INPUT_CHARS]
    parts: list[str] = []
    for block in re.split(r"\n\s*\n", clipped):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        parts.extend(lines or [])
        if len(parts) >= _MAX_PARAGRAPHS:
            break
    return parts[:_MAX_PARAGRAPHS]


def _is_instruction(text: str) -> bool:
    from app.orchestration.guardrails import instruction_channel

    return instruction_channel(text) in {"injection", "leak"}


def _flag_split_instructions(parts: list[str], flagged: list[bool]) -> None:
    """An instruction split across a few short lines is still an instruction.

    Lines that already look like account facts are left in place.
    """
    for start in range(len(parts)):
        window = parts[start : start + 4]
        if len(window) < 2 or any(len(line) > _SHORT_LINE for line in window):
            continue
        suspects = [line for line in window if not _looks_like_fact(line)]
        if len(suspects) < 2 or not _is_instruction(" ".join(suspects)):
            continue
        for offset, line in enumerate(window):
            if line in suspects:
                flagged[start + offset] = True


def _looks_like_fact(line: str) -> bool:
    """A labelled account line, or a line that already carries a figure."""
    if _is_instruction(line):
        return False
    if _FACT_LINE.search(line):
        return True
    return ":" in line and len(line) <= 80


def _literal_strings(path: pathlib.Path) -> str:
    """Readable strings from an uncompressed PDF, when PyMuPDF is absent."""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    found = re.findall(rb"\((?:\\.|[^\\)]){4,}\)\s*Tj", raw)
    lines: list[str] = []
    for token in found[:_MAX_PARAGRAPHS]:
        inner = token.split(b")", 1)[0][1:]
        try:
            lines.append(inner.decode("latin-1"))
        except UnicodeDecodeError:
            continue
    return "\n".join(lines)
