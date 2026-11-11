"""Structured engineering log (B.7), with redaction.

This is deliberately *not* the audit log. The audit log records business decisions,
is append-only and is written inside the decision's own transaction (see app/audit).

Two renderings of the same records, chosen by `LOG_FORMAT`:

  * `json`   -- one line per event, for machines and for the B.7 evidence.
  * `pretty` -- the same fields laid out over several lines for a human reading
                them live. The structured records are the source of truth; this
                is a view of them, so nothing is logged in one mode that is not
                logged in the other.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
from typing import Any

from app.core.correlation import get_correlation_id

#: Keys whose VALUE must never be logged. Matched on whole name segments, not
#: as substrings: the old rule matched any key containing "token", which
#: redacted `tokens`, `total_tokens` and `token_count` -- the context-budget
#: measurements B.5 is signed off on -- as though they were credentials.
#: Bare `token` and `key` are still redacted; `tokens` is a count, not a secret.
REDACT_KEY = re.compile(
    r"(?:^|[_.\-])(password|passwd|secret|credential|authorization|bearer|jwt|cookie|"
    r"api_?key|private_?key|signing_?key|access_?token|refresh_?token|id_?token|session_?key)"
    r"(?:$|[_.\-])|^(token|key)$",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"

_STD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
    # uvicorn attaches an ANSI-coloured copy of its own message. It is the same
    # text wrapped in escape codes: noise as JSON, and worse as pretty output.
    "color_message",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if REDACT_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """Redacted `extra=` fields. Shared so the two renderings cannot diverge on
    what they consider loggable."""
    extras = {k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS}
    return redact(extras) if extras else {}


def _error_text(record: logging.LogRecord) -> str | None:
    if not record.exc_info:
        return None
    # Class and message only: stack traces never leave the process (D.4).
    exc = record.exc_info[1]
    return f"{type(exc).__name__}: {exc}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": get_correlation_id(),
            "message": record.getMessage(),
        }
        payload.update(_extras(record))
        if (error := _error_text(record)) is not None:
            payload["error"] = error
        return json.dumps(payload, default=str)


# --------------------------------------------------------------------------- pretty
#: Widest a single table cell is allowed to get before it is abbreviated. Chunk
#: refs and section headings are long enough to wrap a terminal otherwise, and a
#: wrapped table is harder to read than a truncated one.
_CELL_MAX = 46
_INDENT = "    "

_LEVEL_COLOUR = {
    "DEBUG": "\033[38;5;245m",
    "INFO": "\033[36m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_DIM = "\033[38;5;245m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _colour_enabled(stream) -> bool:
    """ANSI only for a real terminal. Redirecting the log to a file is the normal
    way to keep demo evidence, and escape codes in that file make it unreadable."""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _cell(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.5f}".rstrip("0").rstrip(".")
    elif isinstance(value, (list, tuple)) and all(
        not isinstance(v, (dict, list, tuple)) for v in value
    ):
        # `flags: ["scoring_probe"]` is the verdict a demo reads aloud. Quotes and
        # brackets are JSON syntax, not information.
        text = ", ".join(str(v) for v in value) or "(none)"
    elif isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str)
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= _CELL_MAX else text[: _CELL_MAX - 3] + "..."


def _is_table(value: Any) -> bool:
    """A list of flat dicts -- `chunks`, `blocks`, `versions` -- reads far better
    as an aligned table than as JSON, and those are the records a demo explains."""
    return (
        isinstance(value, (list, tuple))
        and len(value) > 0
        and all(isinstance(v, dict) for v in value)
        and all(not isinstance(x, (dict, list, tuple)) for v in value for x in v.values())
    )


def _render_table(rows: list[dict], indent: str, dim: str, reset: str) -> list[str]:
    columns: list[str] = []
    for row in rows:
        for k in row:
            if k not in columns:
                columns.append(k)

    cells = [[_cell(row.get(c, "")) for c in columns] for row in rows]
    widths = [max([len(name)] + [len(row[i]) for row in cells]) for i, name in enumerate(columns)]
    # Right-align a column only when every value in it is a number: scores line
    # up on the decimal point, which is the whole reason for a table here.
    numeric = [
        all(
            isinstance(row.get(name), (int, float)) and not isinstance(row.get(name), bool)
            for row in rows
        )
        for name in columns
    ]

    def line(values: list[str]) -> str:
        parts = [
            v.rjust(widths[i]) if numeric[i] else v.ljust(widths[i]) for i, v in enumerate(values)
        ]
        return indent + "  ".join(parts).rstrip()

    return [dim + line(columns) + reset] + [line(c) for c in cells]


class PrettyFormatter(logging.Formatter):
    """Human-readable rendering of the same records `JsonFormatter` emits.

    Built for explaining a running system to someone watching the terminal: the
    header carries who logged what, and the `extra=` fields are laid out below
    it so a retrieval or a context assembly can be read without parsing JSON by
    eye. Redaction is shared with the JSON path, so switching format cannot
    widen what is exposed.
    """

    def __init__(self, *, colour: bool = False) -> None:
        super().__init__()
        self._c = colour

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if self._c else text

    def format(self, record: logging.LogRecord) -> str:
        dim = _DIM if self._c else ""
        reset = _RESET if self._c else ""

        level = self._paint(f"{record.levelname:<7}", _LEVEL_COLOUR.get(record.levelname, ""))
        head = (
            f"{self.formatTime(record, '%H:%M:%S')} {level} "
            f"{dim}{record.name}{reset}  {self._paint(record.getMessage(), _BOLD)}"
        )
        # Outside a request there is no correlation id and the placeholder is "-".
        # Printing it on startup lines adds a column of noise to every record.
        corr = str(get_correlation_id() or "")
        if corr and corr != "-":
            head += f"  {dim}[{corr[:8]}]{reset}"

        lines = [head]
        extras = _extras(record)

        scalars = {k: v for k, v in extras.items() if not _is_table(v) and v is not None}
        tables = {k: v for k, v in extras.items() if _is_table(v)}

        if scalars:
            width = max(len(k) for k in scalars)
            for k, v in scalars.items():
                lines.append(f"{_INDENT}{dim}{k.ljust(width)}{reset}  {_cell(v)}")

        for name, rows in tables.items():
            lines.append(f"{_INDENT}{dim}{name}{reset}  ({len(rows)})")
            lines.extend(_render_table(list(rows), _INDENT * 2, dim, reset))

        if (error := _error_text(record)) is not None:
            lines.append(f"{_INDENT}{self._paint('error', _LEVEL_COLOUR['ERROR'])}  {error}")

        return "\n".join(lines)


#: Libraries that narrate work this log already reports. `app.rag.embedder`
#: announces the model load in one line; the embedding stack then repeats it and
#: adds a Hub rate-limit notice, so our one informative line arrives surrounded
#: by third-party chatter that reads, in a demo, as though retrieval were
#: struggling.
_THIRD_PARTY_CHATTER = ("sentence_transformers", "huggingface_hub", "transformers", "torch")


def _quieten_third_party(record: logging.LogRecord) -> bool:
    """Drop sub-error records from the embedding stack.

    Implemented as a filter on our own handler rather than `setLevel` on theirs:
    `huggingface_hub` sets its own level when it is imported, which happens on
    the first retrieval -- long after `configure_logging` has run -- so a level
    set here is simply overwritten. A filter cannot be.
    """
    return record.levelno >= logging.ERROR or not record.name.startswith(_THIRD_PARTY_CHATTER)


def configure_logging(
    level: str = "INFO", *, access_log: bool = True, log_format: str = "json"
) -> None:
    """Install the log handler and decide whether each request is logged.

    `access_log` controls uvicorn's per-request line. It matters more than it
    looks: this log only reports *events*, so a request that goes well writes
    nothing at all. With the access line off, a healthy second request is
    indistinguishable from a server that has stopped responding -- the one-time
    startup chatter (backend selected, embedding model loaded) makes the first
    request look verbose and every one after it look dead.

    `httpx` and `httpcore` stay quiet either way: they narrate every byte of our
    own outbound calls, which is noise rather than a record of anything.
    """
    handler = logging.StreamHandler(sys.stdout)
    if log_format == "pretty":
        # json.dumps escapes non-ASCII; the pretty path writes it through. A
        # cp1252 console would then raise mid-record, so degrade the character
        # rather than lose the log line.
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(errors="replace")
        handler.setFormatter(PrettyFormatter(colour=_colour_enabled(sys.stdout)))
    else:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    handler.addFilter(_quieten_third_party)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO if access_log else logging.WARNING)
