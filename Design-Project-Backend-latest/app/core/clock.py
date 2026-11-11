"""The only module permitted to read the wall clock.

Invariant 4: domain code is handed a session date; it never asks what day it is.
`scripts/check_clock.py` enforces this and runs as part of `make lint`.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Protocol


class SessionClock(Protocol):
    """Supplies the simulated date all pricing and valuation resolves against."""

    def session_date(self) -> _dt.date: ...


@dataclass(frozen=True, slots=True)
class FixedSessionClock:
    """The session date chosen by the user at login (A.5)."""

    date: _dt.date

    def session_date(self) -> _dt.date:
        return self.date


class WallClock:
    """Real time. Audit and log timestamps only -- never domain arithmetic."""

    @staticmethod
    def now() -> _dt.datetime:
        return _dt.datetime.now(_dt.UTC)

    @staticmethod
    def today() -> _dt.date:
        return _dt.datetime.now(_dt.UTC).date()
