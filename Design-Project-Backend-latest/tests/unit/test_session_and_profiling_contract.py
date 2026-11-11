"""Two API-shape guarantees the frontend depends on.

Both were found during frontend integration, and both had forced the UI into a
workaround rather than producing an obvious error.
"""

from __future__ import annotations

import datetime as _dt

from app.api.routers.session import SessionDateResponse


class TestSessionDateResponse:
    """`GET /api/session/date` must carry the range even with no date chosen.

    The date picker needs `min`/`max` *before* the user picks, which is exactly
    when no session date exists. Raising there left the only route to the bounds
    a regex over a rejected POST's error message -- so the picker silently lost
    its limits whenever that wording changed.
    """

    def test_session_date_is_optional(self):
        dto = SessionDateResponse(
            availableFrom=_dt.date(2026, 1, 5), availableTo=_dt.date(2026, 6, 19)
        )
        assert dto.sessionDate is None
        assert dto.availableFrom and dto.availableTo

    def test_bounds_survive_serialisation(self):
        import json

        payload = json.loads(
            SessionDateResponse(
                availableFrom=_dt.date(2026, 1, 5), availableTo=_dt.date(2026, 6, 19)
            ).model_dump_json(by_alias=True)
        )
        assert payload["sessionDate"] is None
        assert payload["availableFrom"] == "2026-01-05"
        assert payload["availableTo"] == "2026-06-19"

    def test_a_chosen_date_still_round_trips(self):
        dto = SessionDateResponse(
            sessionDate=_dt.date(2026, 3, 2),
            availableFrom=_dt.date(2026, 1, 5),
            availableTo=_dt.date(2026, 6, 19),
        )
        assert dto.sessionDate == _dt.date(2026, 3, 2)

    def test_the_get_route_no_longer_raises_on_an_unset_date(self):
        """Guards the behaviour, not just the DTO: the old code raised here."""
        import inspect

        from app.api.routers.session import get_session_date

        source = inspect.getsource(get_session_date)
        assert "No session date has been selected yet" not in source, (
            "GET /session/date must return the range rather than raising when no "
            "date is set -- the frontend needs the bounds at exactly that moment"
        )


class TestProfilingStartHonoursCompletion:
    """`POST /api/profiling/start` must not claim a finished profile is unfinished."""

    def test_start_and_message_share_one_completion_path(self):
        import inspect

        from app.api.routers import profiling

        start = inspect.getsource(profiling.start)
        assert "complete=False" not in start, (
            "start hardcoded complete=False, contradicting turn.complete for any "
            "client whose A.1 profile was already filled in"
        )
        assert "_respond" in start
        assert "_respond" in inspect.getsource(profiling.message)

    def test_a_complete_coherent_profile_opens_as_complete(self):
        """The domain layer already said so; only the route disagreed."""
        from dataclasses import dataclass
        from decimal import Decimal

        from app.domain.profile.conversation import opening

        @dataclass(frozen=True)
        class Profile:
            monthly_salary: Decimal = Decimal("8000")
            monthly_commitments: Decimal = Decimal("4500")
            dependants: int = 1
            investment_objective: str = "balanced"
            investment_horizon_yrs: int = 8
            loss_tolerance: str = "medium"
            liquidity_needs: str = "medium"
            income_stability: str = "stable"
            investment_experience: str = "moderate"

            def missing_fields(self):
                return []

        turn = opening(Profile())
        assert turn.complete is True
        assert turn.awaiting is None
