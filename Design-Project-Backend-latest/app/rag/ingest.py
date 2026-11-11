"""Corpus ingestion (B.3): parse -> normalise -> chunk -> embed -> index.

Idempotent by content hash: re-running against an unchanged corpus reuses the
existing version, an updated corpus writes a *new* `corpus_version` rather than
overwriting the old one. That is what keeps a citation shown in week 8 resolvable
after a re-ingest in week 10.

One document is deliberately never ingested: the risk-scoring formula. The
strongest guarantee it cannot be retrieved and disclosed is that it is not in the
index at all. The output filter is a second line, not the first.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import pathlib
import re
import sys

from app.db.session import connect_sync
from app.rag.chunker import chunk_document
from app.rag.embedder import embedder_identity, get_embedder

log = logging.getLogger("app.rag.ingest")

SUPPORTED = {".pdf", ".md", ".txt", ".docx"}

#: Filenames that must never enter the client-facing index, whatever the flags say.
EXCLUDED = re.compile(r"appendix[\s_-]*b|scoring[\s_-]*formula|risk[\s_-]*weights", re.IGNORECASE)

#: Sections that are instructions to the system rather than policy a client
#: reads. Same intent as EXCLUDED above, at section granularity.
#:
#: "Prohibited behaviours" is a list of exactly what the system must never do.
#: It is written for the team, it is a target list for anyone probing the
#: assistant, and it is what retrieval handed the model when a client asked why
#: they were assigned their risk category -- after which the answer opened by
#: refusing to disclose a formula nobody had asked for.
#:
#: The confidentiality statement is deliberately NOT here, though it was the
#: other half of that preamble. Hiding it cost more than it saved: it is also
#: where the client's entitlement lives ("clients receive their risk category
#: and a qualitative explanation"), so with it gone the caveat the model still
#: makes had nothing to stand on and the faithfulness judge began refusing the
#: answer outright -- a cosmetic wart traded for a real failure.
#:
#: Ingested as `admin` rather than dropped, so the officer console can still
#: show it; client retrieval filters on audience and will not see it.
INTERNAL_SECTIONS = re.compile(r"prohibited behaviour|confidentiality statement", re.IGNORECASE)


# --- parse ------------------------------------------------------------------
def parse(path: pathlib.Path) -> str:
    """Extract text, preserving page boundaries and table structure.

    Tables are emitted as markdown pipe rows so the chunker can recognise and
    keep them whole.
    """
    if path.suffix.lower() in {".md", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".docx":
        return _parse_docx(path)
    return _parse_pdf(path)


def _parse_docx(path: pathlib.Path) -> str:
    """Extract a .docx with the standard library.

    A .docx is a zip of XML, so this needs no dependency -- which matters
    because the authoritative policy arrives in this format and a missing parser
    is not a loud failure: an unsupported extension is skipped silently, so the
    corpus would simply go on serving whatever it held before.

    Headings become markdown hashes and tables become pipe rows, because that is
    what the heading-aware chunker reads. A table flattened to prose would be
    split mid-row, and the eligibility matrix is exactly the kind of table that
    has to survive whole to be worth citing.
    """
    import zipfile
    from xml.etree import ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def text_of(element) -> str:
        return "".join(node.text or "" for node in element.iter(f"{ns}t"))

    try:
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise SystemExit(f"{path.name} is not a readable .docx: {exc}") from exc

    out: list[str] = []
    for element in root.find(f"{ns}body") or []:
        tag = element.tag.removeprefix(ns)
        if tag == "p":
            body = text_of(element).strip()
            if not body:
                continue
            style = element.find(f"{ns}pPr/{ns}pStyle")
            name = style.get(f"{ns}val", "") if style is not None else ""
            level = "".join(c for c in name if c.isdigit())
            out.append(
                f"{'#' * int(level or 1)} {body}" if name.lower().startswith("heading") else body
            )
        elif tag == "tbl":
            rows = [
                "| "
                + " | ".join(
                    text_of(cell).strip().replace("\n", " ") for cell in row.findall(f"{ns}tc")
                )
                + " |"
                for row in element.findall(f"{ns}tr")
            ]
            if rows:
                rows.insert(1, "|" + "---|" * (rows[0].count("|") - 1))
                out.append("\n".join(rows))
    return "\n\n".join(out)


def _parse_pdf(path: pathlib.Path) -> str:
    try:
        import pymupdf  # type: ignore
    except ImportError:  # pragma: no cover
        raise SystemExit("PDF parsing needs the rag extra: pip install -e '.[rag]'") from None

    parts: list[str] = []
    with pymupdf.open(path) as doc:
        for number, page in enumerate(doc, start=1):
            parts.append(f"<!-- page: {number} -->")
            table_bboxes = []
            try:
                for table in page.find_tables().tables:
                    table_bboxes.append(table.bbox)
                    parts.append(_as_markdown_table(table.extract()))
            except Exception as exc:  # pragma: no cover - parser quirks per file
                log.warning("table extraction failed", extra={"page": number, "err": str(exc)})
            # Text outside the tables, so table cells are not duplicated as prose.
            parts.append(_text_excluding(page, table_bboxes))
    return "\n\n".join(p for p in parts if p and p.strip())


def _as_markdown_table(rows) -> str:
    cleaned = [
        ["" if cell is None else " ".join(str(cell).split()) for cell in row] for row in rows
    ]
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    header, *body = cleaned
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _text_excluding(page, bboxes) -> str:
    import pymupdf  # type: ignore

    if not bboxes:
        return page.get_text("text")
    clip = pymupdf.Rect(page.rect)
    blocks = page.get_text("blocks", clip=clip)
    kept = [
        b[4]
        for b in blocks
        if not any(pymupdf.Rect(b[:4]).intersects(pymupdf.Rect(bb)) for bb in bboxes)
    ]
    return "\n".join(kept)


# --- normalise --------------------------------------------------------------
REPEATED_WS = re.compile(r"[ \t]{2,}")
BLANK_RUNS = re.compile(r"\n{3,}")


def normalise(text: str, *, filename: str) -> str:
    """Strip running headers/footers, collapse whitespace, promote headings."""
    lines = text.splitlines()
    counts: dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if 0 < len(stripped) < 90:
            counts[stripped] = counts.get(stripped, 0) + 1

    pages = max(1, text.count("<!-- page:"))
    # A short line repeated on most pages is furniture, not content.
    furniture = {line for line, n in counts.items() if n >= max(3, pages * 0.6)}

    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in furniture or re.fullmatch(r"page\s*\d+|\d+", stripped, re.IGNORECASE):
            continue
        if _looks_like_heading(stripped) and not line.lstrip().startswith(("#", "|")):
            out.append(f"## {stripped}")
        else:
            out.append(REPEATED_WS.sub(" ", line.rstrip()))
    return BLANK_RUNS.sub("\n\n", "\n".join(out)).strip()


NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z]\.\d+)\s+\S.{0,80}$")


def _looks_like_heading(line: str) -> bool:
    if not line or len(line) > 90 or line.endswith((".", ",", ";", "|")):
        return False
    return bool(NUMBERED_HEADING.match(line)) or (line.isupper() and len(line.split()) <= 10)


# --- index ------------------------------------------------------------------
def corpus_digest(files: list[pathlib.Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.name.encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


def _chunk_audience(document_audience: str, heading: str | None) -> str:
    """`admin` for a section written for the team, otherwise the document's own.

    Only ever narrows: an admin corpus stays admin, and a client corpus keeps
    every section except the ones INTERNAL_SECTIONS names.
    """
    if document_audience == "client" and INTERNAL_SECTIONS.search(heading or ""):
        return "admin"
    return document_audience


def ingest(source: pathlib.Path, *, label: str, audience: str, reset: bool = False) -> dict:
    files = sorted(p for p in source.rglob("*") if p.suffix.lower() in SUPPORTED)
    if not files:
        raise SystemExit(f"no ingestible documents ({sorted(SUPPORTED)}) under {source}")

    excluded = [p for p in files if EXCLUDED.search(p.name)]
    files = [p for p in files if p not in excluded]
    for path in excluded:
        log.warning("refusing to ingest confidential source", extra={"file": path.name})
    if not files:
        raise SystemExit("every candidate document was excluded as confidential")

    version_label = f"{label}-{corpus_digest(files)}"
    embedder = get_embedder()
    stats = {"documents": 0, "chunks": 0, "tables": 0, "version": version_label}

    with connect_sync() as conn:
        with conn.cursor() as cur:
            if reset:
                cur.execute("delete from corpus_version")

            cur.execute("select id from corpus_version where label = %s", (version_label,))
            if (existing := cur.fetchone()) is not None:
                cur.execute(
                    "select count(*) from chunk c join document d on d.id = c.document_id"
                    " where d.corpus_version_id = %s",
                    (existing[0],),
                )
                stats["chunks"] = cur.fetchone()[0]
                stats["unchanged"] = True
                return stats

            cur.execute(
                "insert into corpus_version (label, note) values (%s, %s) returning id",
                (
                    version_label,
                    f"{len(files)} document(s) from {source} | embedder={embedder_identity()}",
                ),
            )
            corpus_version_id = cur.fetchone()[0]

            for path in files:
                doc_id = path.stem.upper().replace(" ", "-")[:40]
                body = normalise(parse(path), filename=path.name)
                chunks = chunk_document(body)
                if not chunks:
                    log.warning("document produced no chunks", extra={"file": path.name})
                    continue

                cur.execute(
                    "insert into document (corpus_version_id, doc_id, title, source_path,"
                    " audience) values (%s, %s, %s, %s, %s) returning id",
                    (corpus_version_id, doc_id, _title(body, path), str(path), audience),
                )
                document_id = cur.fetchone()[0]

                vectors = embedder.encode([c.text for c in chunks])
                cur.executemany(
                    "insert into chunk (document_id, chunk_ref, ordinal, heading, section,"
                    " page, text, token_count, audience, embedding)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            document_id,
                            f"{doc_id}#{c.ordinal}",
                            c.ordinal,
                            c.heading,
                            c.section,
                            c.page,
                            c.text,
                            c.token_count,
                            _chunk_audience(audience, c.heading),
                            "[" + ",".join(f"{v:.7f}" for v in vec) + "]",
                        )
                        for c, vec in zip(chunks, vectors, strict=True)
                    ],
                )
                stats["documents"] += 1
                stats["chunks"] += len(chunks)
                stats["tables"] += sum(1 for c in chunks if c.is_table)
        conn.commit()
    return stats


#: First-heading candidates that are never a document's title. PDFs have no real
#: heading markup, so `normalise` *promotes* whatever looked like a heading at the
#: top of page one -- in practice a contents header, a footer URL or a section
#: number. Markdown headings are authored, so they are trusted.
_NOT_A_TITLE = re.compile(
    r"^(contents|table of contents|index|preface|foreword|disclaimer|notice|annex)\b"
    r"|https?://|www\."
    r"|^[A-Za-z]?\.?\d+(\.\d+)*\s",
    re.IGNORECASE,
)


def _title_from_filename(path: pathlib.Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip().title()[:120]


def _title(body: str, path: pathlib.Path) -> str:
    """A human title for citations.

    The title is shown to the client on every citation, so a document called
    "1 https://www.pdpc.gov.sg/..." is not a cosmetic problem -- it makes the
    provenance look broken precisely where it is meant to inspire confidence.
    Only an authored heading is trusted; for anything else the filename is the
    more reliable source, because these documents are named descriptively.
    """
    if path.suffix.lower() in {".md", ".txt"}:
        for line in body.splitlines():
            if line.startswith("#"):
                candidate = line.lstrip("# ").strip()[:120]
                if candidate and not _NOT_A_TITLE.search(candidate):
                    return candidate
    return _title_from_filename(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a document corpus into the vector store.")
    ap.add_argument("--source", type=pathlib.Path, required=True)
    ap.add_argument("--corpus-label", default="corpus")
    ap.add_argument("--audience", choices=("client", "admin"), default="client")
    ap.add_argument("--reset", action="store_true", help="drop every existing corpus version")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    stats = ingest(args.source, label=args.corpus_label, audience=args.audience, reset=args.reset)
    if stats.get("unchanged"):
        print(
            f"corpus {stats['version']} already indexed ({stats['chunks']} chunks); nothing to do"
        )
    else:
        print(
            f"indexed {stats['chunks']} chunks ({stats['tables']} tables) from "
            f"{stats['documents']} document(s) as {stats['version']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
