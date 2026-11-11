"""Per-request stage timing.

Latency work needs a breakdown, not a total. Guessing which stage dominates is
how an afternoon gets spent optimising retrieval when the cost is actually in a
judge -- or, as measured here, optimising the remote model when it answers in a
tenth of the time the local checkers take.

Stages record into a dict held in a context variable, so a stage deep in the RAG
layer can time itself without the pipeline passing a timer down to it. Outside a
request the context variable is empty and `stage()` is a no-op, which keeps this
usable from scripts and tests without setup.
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager

_STAGES: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar(
    "request_stages", default=None
)


#: Nesting depth, so a stage inside another is not added to the request total
#: twice. `retrieval.total` wraps `retrieval.dense_sql` and three siblings, and
#: summing every recorded stage counted that work once for the parent and again
#: for each child -- which is how an accounted total came out larger than the
#: request it was measuring.
_DEPTH: contextvars.ContextVar[int] = contextvars.ContextVar("stage_depth", default=0)


def start_request() -> dict[str, float]:
    """Begin collecting stage timings for this request."""
    stages: dict[str, float] = {}
    _STAGES.set(stages)
    _DEPTH.set(0)
    return stages


@contextmanager
def stage(name: str):
    """Time a block and add it to this request's breakdown.

    Durations accumulate under the same name rather than overwrite, so a stage
    entered twice -- history is read once for the guardrails and again for the
    payload -- reports its true total cost instead of only the last visit.

    A nested stage is still reported, because knowing the embedding call inside
    retrieval costs 10ms is the point of having it. It is only excluded from the
    accounted total, where its parent already represents it.
    """
    started = time.perf_counter()
    depth = _DEPTH.get()
    token = _DEPTH.set(depth + 1)
    try:
        yield
    finally:
        _DEPTH.reset(token)
        stages = _STAGES.get()
        if stages is not None:
            elapsed = (time.perf_counter() - started) * 1000
            key = name if depth == 0 else f"{_NESTED_PREFIX}{name}"
            stages[key] = stages.get(key, 0.0) + elapsed


#: Marks a stage recorded inside another. Stripped for display; used to keep
#: nested work out of the accounted total.
_NESTED_PREFIX = "└"


def snapshot() -> dict[str, int]:
    """Stage timings so far, in whole milliseconds, slowest first.

    Nested stages keep their marker so `accounted()` can tell them apart; the
    log strips it for display.
    """
    stages = _STAGES.get() or {}
    return {k: int(v) for k, v in sorted(stages.items(), key=lambda kv: -kv[1])}


def accounted() -> int:
    """Milliseconds of the request explained by top-level stages.

    Nested stages are excluded: their parent already covers the same wall clock,
    so including both reports more time than the request took.
    """
    stages = _STAGES.get() or {}
    return int(sum(v for k, v in stages.items() if not k.startswith(_NESTED_PREFIX)))


def is_nested(name: str) -> bool:
    return name.startswith(_NESTED_PREFIX)


def display_name(name: str) -> str:
    return name.removeprefix(_NESTED_PREFIX)
