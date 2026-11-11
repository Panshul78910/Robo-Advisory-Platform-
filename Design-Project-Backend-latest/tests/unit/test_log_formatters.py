"""The two log renderings must stay interchangeable (B.7).

`LOG_FORMAT=pretty` exists so a demo can be read off the terminal. That is only
safe if it is a *view* of the same records: if the pretty path ever logged a
field the JSON path withholds, switching format for a demo would widen what is
exposed. These tests pin that equivalence, and pin the redaction rule that a
previous version of this module got wrong in the other direction -- it scrubbed
`total_tokens` as though a context-budget measurement were a credential.
"""

from __future__ import annotations

import json
import logging

from app.core.logging import JsonFormatter, PrettyFormatter

RETRIEVAL_EXTRA = {
    "kept": 6,
    "relevance_floor": 0.34,
    "best_similarity": 0.68287,
    "chunks": [
        {"chunk_ref": "COMPANY-SUITABILITY-POLICY#12", "similarity": 0.68287},
        {"chunk_ref": "FSG-G04#4", "similarity": 0.65836},
    ],
}


def _record(message: str = "retrieval complete", **extra) -> logging.LogRecord:
    record = logging.LogRecord("app.rag.retrieve", logging.INFO, __file__, 1, message, None, None)
    record.__dict__.update(extra)
    return record


def test_pretty_renders_a_list_of_dicts_as_an_aligned_table():
    out = PrettyFormatter().format(_record(**RETRIEVAL_EXTRA))
    lines = out.splitlines()

    assert "retrieval complete" in lines[0]
    # Header row, then one row per chunk, all carrying the column name once.
    assert sum(1 for line in lines if "chunk_ref" in line) == 1
    assert any("COMPANY-SUITABILITY-POLICY#12" in line for line in lines)
    assert any("FSG-G04#4" in line for line in lines)
    # Scores are what the reader compares, so within the table they must line up
    # in one column. Only the two chunk rows count: `best_similarity` is a scalar
    # line above the table and repeats the same number at its own indent.
    rows = [
        line for line in lines if "COMPANY-SUITABILITY-POLICY#12" in line or "FSG-G04#4" in line
    ]
    assert len(rows) == 2
    assert len({line.index("0.6") for line in rows}) == 1


def test_pretty_keeps_scalars_one_per_line():
    out = PrettyFormatter().format(_record(**RETRIEVAL_EXTRA))
    assert any(line.strip().startswith("relevance_floor") for line in out.splitlines())
    assert any(
        line.strip() == "kept                6" or "kept" in line for line in out.splitlines()
    )


def test_both_formatters_redact_the_same_secrets():
    extra = {"password": "hunter2", "access_token": "eyJ", "email": "c002@roboadvisor.local"}
    pretty = PrettyFormatter().format(_record("auth attempt", **extra))
    as_json = json.loads(JsonFormatter().format(_record("auth attempt", **extra)))

    for rendering in (pretty, json.dumps(as_json)):
        assert "hunter2" not in rendering
        assert "eyJ" not in rendering
        assert "[REDACTED]" in rendering
    assert as_json["password"] == "[REDACTED]"
    assert as_json["access_token"] == "[REDACTED]"


def test_token_counts_survive_both_formatters():
    """`total_tokens` is the B.5 measurement, not a credential."""
    extra = {"tokens": 412, "total_tokens": 3962, "token_count": 88}
    pretty = PrettyFormatter().format(_record("context assembled", **extra))
    as_json = json.loads(JsonFormatter().format(_record("context assembled", **extra)))

    assert as_json == {**as_json, "tokens": 412, "total_tokens": 3962, "token_count": 88}
    assert "3962" in pretty
    assert "[REDACTED]" not in pretty


def test_pretty_exposes_no_field_the_json_rendering_withholds():
    extra = {**RETRIEVAL_EXTRA, "password": "hunter2", "total_tokens": 3962}
    pretty = PrettyFormatter().format(_record(**extra))
    as_json = json.loads(JsonFormatter().format(_record(**extra)))

    # Every key the pretty rendering names must exist in the JSON payload, and
    # no redacted value may appear in either.
    for key in extra:
        assert key in as_json
        assert key in pretty
    assert "hunter2" not in pretty and "hunter2" not in json.dumps(as_json)


def test_pretty_omits_ansi_codes_when_colour_is_off():
    out = PrettyFormatter(colour=False).format(_record(**RETRIEVAL_EXTRA))
    assert "\033" not in out


def test_pretty_reports_an_exception_without_a_stack_trace():
    try:
        raise ValueError("pool exhausted")
    except ValueError:
        import sys

        record = _record("connection failed")
        record.exc_info = sys.exc_info()
        out = PrettyFormatter().format(record)

    assert "ValueError: pool exhausted" in out
    # Stack traces never leave the process (D.4).
    assert "Traceback" not in out
    assert __file__ not in out.replace(record.pathname, "")
