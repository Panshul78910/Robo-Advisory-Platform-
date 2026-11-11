"""Session date selection (A.5): the simulated 'today' all pricing resolves to."""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends

from app.api.deps import client_repo, market_repo
from app.api.dto.common import Dto
from app.api.dto.requests import SessionDateRequest
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository

router = APIRouter(tags=["session"])


class SessionDateResponse(Dto):
    #: Null until the user has chosen one. The selectable range is always
    #: present, because a client needs it precisely when no date is set yet.
    sessionDate: _dt.date | None = None
    availableFrom: _dt.date | None = None
    availableTo: _dt.date | None = None
    priceFile: str = "/api/market/prices"
    priceFileLabel: str = "Appendix C prices (JSON price service)"
    instrumentCount: int | None = None
    priceFormat: str = "json"
    canonicalSchema: list[str] = ["price_date", "symbol", "price"]


@router.post("/session/date", response_model=SessionDateResponse)
async def set_session_date(
    body: SessionDateRequest,
    repo: ClientRepository = Depends(client_repo),
    market: MarketRepository = Depends(market_repo),
) -> SessionDateResponse:
    first, last = await market.available_dates()
    if first is None:
        raise ValidationError("No price data has been loaded yet.")
    if not (first <= body.session_date <= last):
        raise ValidationError(
            f"No prices exist for {body.session_date.isoformat()}. "
            f"Choose a date between {first.isoformat()} and {last.isoformat()}."
        )

    await repo.set_session_date(body.session_date)
    settings = get_settings()
    return SessionDateResponse(
        sessionDate=body.session_date,
        availableFrom=first,
        availableTo=last,
        instrumentCount=len(await market.instruments()),
        priceFile=settings.price_json_path,
        priceFileLabel=settings.price_source_label,
        priceFormat="json",
    )


@router.get("/session/date", response_model=SessionDateResponse)
async def get_session_date(
    repo: ClientRepository = Depends(client_repo),
    market: MarketRepository = Depends(market_repo),
) -> SessionDateResponse:
    """The chosen session date, and the range it may be chosen from.

    Returns 200 with a null `sessionDate` when none has been picked rather than
    an error: "which dates may I choose?" is a legitimate question, and it is
    asked exactly when no date exists yet. Raising here meant the only way to
    discover the range was to POST a deliberately impossible date and scrape the
    bounds out of the refusal message -- which the frontend did, leaving its date
    picker unbounded on first login whenever that message changed.
    """
    first, last = await market.available_dates()
    if first is None:
        raise ValidationError("No price data has been loaded yet.")
    settings = get_settings()
    return SessionDateResponse(
        sessionDate=await repo.current_session_date(),
        availableFrom=first,
        availableTo=last,
        instrumentCount=len(await market.instruments()),
        priceFile=settings.price_json_path,
        priceFileLabel=settings.price_source_label,
        priceFormat="json",
    )
