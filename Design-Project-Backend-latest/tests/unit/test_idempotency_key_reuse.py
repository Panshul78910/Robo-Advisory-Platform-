"""B6: a retried POST must reuse the same Idempotency-Key.

The backend already replays on a duplicate key. The live defect was the
frontend minting a fresh UUID inside `request()`, so a timeout-and-retry
looked like a new order. These tests pin both halves of that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.trading.execution import request_hash


def test_the_backend_digest_changes_when_the_payload_does():
    same = dict(op="rebalance", date="2026-06-19", preview_token="abc")
    assert request_hash(**same) == request_hash(**same)
    assert request_hash(**same) != request_hash(
        op="rebalance", date="2026-06-19", preview_token="def"
    )


def _frontend_api() -> Path | None:
    here = Path(__file__).resolve()
    goat = here.parents[3]
    backend_parent = here.parents[2].parent
    for root in (goat, backend_parent):
        for name in ("Design-Project-Frontend-latest", "Design-Project-Frontend"):
            path = root / name / "src" / "api.js"
            if path.is_file():
                return path
    return None


def test_frontend_mints_the_key_per_action_not_per_http_call():
    path = _frontend_api()
    if path is None:
        pytest.skip("frontend api.js is not in this workspace")
    src = path.read_text(encoding="utf-8")
    start = src.index("async function request(")
    end = src.index("export const api")
    request_body = src[start:end]
    assert "newKey(" not in request_body, (
        "request() must not mint an Idempotency-Key; a timeout retry would then double-trade"
    )
    assert "idempotencyKey" in request_body
    assert "function withIdempotency(" in src
    assert "keepKeyOnRetry" in src
    for action in (
        "withIdempotency(`invest:",
        'withIdempotency("rebalance"',
        "withIdempotency(`switch:",
        'withIdempotency("revert"',
    ):
        assert action in src, f"missing action-scoped key for {action}"
