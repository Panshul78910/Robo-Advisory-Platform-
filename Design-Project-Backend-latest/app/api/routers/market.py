"""Appendix C prices as JSON (A.5).

The CSV schema (price_date, symbol, price) is the canonical source format.
This service loads that file (or an instructor JSON feed) into Postgres and
exposes the same rows over HTTP so clients never handle CSV.
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends, Query

from app.api.deps import client_repo, market_repo, session_date
from app.api.dto.common import Dto, Money
from app.core.config import get_settings
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository

router = APIRouter(tags=["market"])


class PriceRowDto(Dto):
    priceDate: _dt.date
    symbol: str
    name: str
    price: Money
    assetClass: str
    isSatellite: bool = False


class PriceBookDto(Dto):
    sessionDate: _dt.date
    source: str = "appendix-c"
    format: str = "json"
    canonicalSchema: list[str] = ["price_date", "symbol", "price"]
    endpoint: str
    label: str
    prices: list[PriceRowDto]


def _book(day: _dt.date, instruments: dict, prices: dict) -> PriceBookDto:
    settings = get_settings()
    rows = [
        PriceRowDto(
            priceDate=day,
            symbol=info.symbol,
            name=info.name,
            price=prices[iid],
            assetClass=info.asset_class,
            isSatellite=info.is_satellite,
        )
        for iid, info in sorted(instruments.items(), key=lambda kv: kv[1].symbol)
        if iid in prices
    ]
    return PriceBookDto(
        sessionDate=day,
        endpoint=settings.price_json_path,
        label=settings.price_source_label,
        prices=rows,
    )


@router.get("/market/prices", response_model=PriceBookDto)
async def market_prices(
    when: _dt.date | None = Query(default=None, alias="session_date"),
    repo: ClientRepository = Depends(client_repo),
    market: MarketRepository = Depends(market_repo),
) -> PriceBookDto:
    """A.5(1): Appendix C prices for the chosen session date, as JSON."""
    day = when or await repo.current_session_date()
    if day is None:
        from app.core.errors import ValidationError

        raise ValidationError("Choose a session date first.")
    instruments = await market.instruments()
    priced = await market.prices_on(day)
    return _book(day, instruments, priced)


@router.get("/market/prices/session", response_model=PriceBookDto)
async def market_prices_for_session(
    when: _dt.date = Depends(session_date),
    market: MarketRepository = Depends(market_repo),
) -> PriceBookDto:
    instruments = await market.instruments()
    priced = await market.prices_on(when)
    return _book(when, instruments, priced)
