"""Heading-aware chunking with whole-table preservation (B.3).

The policy corpus is substantially tabular: the eligibility matrix, the override
conditions and the per-category satellite caps are all tables. A fixed-window
chunker splits those mid-row, producing chunks that retrieve well on keywords but
are semantically empty -- three category names with no corresponding caps is
worse than no chunk at all, because it grounds an explanation in a fragment.

So tables are extracted whole, with their caption, even when that exceeds the
target size. Prose is split on headings and then packed to the target with
overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.orchestration.tokens import count_tokens

TARGET_TOKENS = 500
OVERLAP_RATIO = 0.15

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
#: A markdown table row: at least two pipe-delimited cells.
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
#: Caption conventions seen in policy documents.
CAPTION = re.compile(r"^\s*(table|figure|exhibit|appendix)\s+[\w.\-]+[:.]?\s*", re.IGNORECASE)


@dataclass(slots=True)
class Chunk:
    text: str
    ordinal: int
    heading: str | None = None
    section: str | None = None
    page: int | None = None
    is_table: bool = False
    token_count: int = 0


@dataclass(slots=True)
class _Segment:
    """A run of lines that must not be split: either a table or a prose paragraph."""

    lines: list[str] = field(default_factory=list)
    is_table: bool = False
    heading: str | None = None
    page: int | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


PAGE_MARKER = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$")


def _detach_caption(lines: list[str]) -> list[str]:
    """Remove a caption sitting just above a table so it can move with it.

    Captions are usually separated from their table by a blank line, so blanks
    are skipped before looking. `lines` is mutated: whatever is returned has been
    taken out of the prose segment.
    """
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and CAPTION.match(lines[-1]):
        return [lines.pop()]
    return []


def _segment(text: str) -> list[_Segment]:
    """Split into indivisible segments, tracking the current heading and page."""
    segments: list[_Segment] = []
    heading: str | None = None
    page: int | None = None
    current = _Segment(heading=heading, page=page)
    pending_caption: list[str] = []

    def flush() -> None:
        nonlocal current
        if current.text:
            segments.append(current)
        current = _Segment(heading=heading, page=page)

    for line in text.splitlines():
        if (marker := PAGE_MARKER.match(line)) is not None:
            page = int(marker.group(1))
            current.page = current.page if current.lines else page
            continue

        if (match := HEADING.match(line)) is not None:
            flush()
            heading = match.group(2).strip()
            current.heading = heading
            current.lines = [line]
            flush()
            continue

        is_table_line = bool(TABLE_ROW.match(line))
        if is_table_line != current.is_table and current.text:
            # A table starts or ends: close the run. A caption immediately above
            # travels with the table rather than being stranded in the prose.
            if is_table_line:
                pending_caption = _detach_caption(current.lines)
            flush()

        if is_table_line and not current.lines and pending_caption:
            current.lines.extend(pending_caption)
            pending_caption = []

        current.is_table = is_table_line or (current.is_table and not line.strip())
        if line.strip() or current.lines:
            current.lines.append(line)

    flush()
    return [s for s in segments if s.text]


def _overlap_tail(text: str, tokens: int) -> str:
    """Approximate the last `tokens` worth of text, on a sentence boundary."""
    if tokens <= 0:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    tail: list[str] = []
    for sentence in reversed(sentences):
        tail.insert(0, sentence)
        if count_tokens(" ".join(tail)) >= tokens:
            break
    return " ".join(tail)


def chunk_document(
    text: str, *, target_tokens: int | None = None, overlap_ratio: float | None = None
) -> list[Chunk]:
    """Split `text` into retrievable chunks.

    A table is always exactly one chunk. Prose is packed up to `target_tokens`
    with `overlap_ratio` carried into the next chunk, so a passage that straddles
    a boundary still appears whole in one of the two neighbours.

    Both default to `CHUNK_TARGET_TOKENS` / `CHUNK_OVERLAP_RATIO` in the
    environment, read per call rather than bound at import so a change takes
    effect on the next ingest without editing code. Explicit arguments still
    win, which is what the chunker's own tests pass.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if target_tokens is None:
        target_tokens = settings.chunk_target_tokens
    if overlap_ratio is None:
        overlap_ratio = settings.chunk_overlap_ratio
    overlap_tokens = int(target_tokens * overlap_ratio)
    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_tokens = 0
    buffer_heading: str | None = None
    buffer_page: int | None = None

    def emit(body: str, *, heading, page, is_table: bool) -> None:
        body = body.strip()
        if not body:
            return
        chunks.append(
            Chunk(
                text=body,
                ordinal=len(chunks),
                heading=heading,
                section=heading,
                page=page,
                is_table=is_table,
                token_count=count_tokens(body),
            )
        )

    def flush_prose() -> None:
        nonlocal buffer, buffer_tokens
        if buffer:
            emit("\n\n".join(buffer), heading=buffer_heading, page=buffer_page, is_table=False)
        buffer, buffer_tokens = [], 0

    for segment in _segment(text):
        if segment.is_table:
            # Whole, oversized or not -- a half table grounds nothing.
            flush_prose()
            emit(segment.text, heading=segment.heading, page=segment.page, is_table=True)
            continue

        tokens = count_tokens(segment.text)
        if buffer and (buffer_tokens + tokens > target_tokens or segment.heading != buffer_heading):
            carried = _overlap_tail("\n\n".join(buffer), overlap_tokens)
            flush_prose()
            if carried and segment.heading == buffer_heading:
                buffer, buffer_tokens = [carried], count_tokens(carried)

        if not buffer:
            buffer_heading, buffer_page = segment.heading, segment.page
        buffer.append(segment.text)
        buffer_tokens += tokens

        # A single prose segment larger than the target is split on sentences.
        while buffer_tokens > target_tokens * 1.5:
            merged = "\n\n".join(buffer)
            head, tail = _split_on_sentence(merged, target_tokens)
            emit(head, heading=buffer_heading, page=buffer_page, is_table=False)
            buffer, buffer_tokens = ([tail], count_tokens(tail)) if tail else ([], 0)

    flush_prose()
    for index, chunk in enumerate(chunks):
        chunk.ordinal = index
    return chunks


def _split_on_sentence(text: str, target_tokens: int) -> tuple[str, str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    head: list[str] = []
    total = 0
    for index, sentence in enumerate(sentences):
        total += count_tokens(sentence)
        head.append(sentence)
        if total >= target_tokens:
            return " ".join(head), " ".join(sentences[index + 1 :])
    return text, ""
