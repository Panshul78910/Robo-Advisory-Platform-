"""Appendix C prices: CSV is the source format; JSON is what clients receive."""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal

from scripts.price_file import HEADER, parse_price_json
from app.api.routers.session import SessionDateResponse
from app.api.routers.market import PriceBookDto, PriceRowDto


def test_canonical_csv_schema_is_appendix_c():
    assert HEADER == ("price_date", "symbol", "price")


def test_parse_instructor_json_list():
    rows = parse_price_json(
        [
            {"price_date": "2026-03-02", "symbol": "WEQ", "price": "52.11"},
            {"date": "2026-03-02", "symbol": "CSH", "price": 1.02},
        ]
    )
    assert rows[0] == (_dt.date(2026, 3, 2), "WEQ", Decimal("52.11"))
    assert rows[1][1] == "CSH"


def test_parse_our_api_envelope():
    rows = parse_price_json(
        {
            "canonicalSchema": list(HEADER),
            "prices": [{"priceDate": "2026-03-02", "symbol": "BND", "price": 99.4}],
        }
    )
    assert rows == [(_dt.date(2026, 3, 2), "BND", Decimal("99.4"))]


def test_session_metadata_points_at_json_not_csv():
    payload = json.loads(
        SessionDateResponse(
            sessionDate=_dt.date(2026, 3, 2),
            availableFrom=_dt.date(2026, 1, 5),
            availableTo=_dt.date(2026, 6, 19),
        ).model_dump_json(by_alias=True)
    )
    assert payload["priceFormat"] == "json"
    assert payload["priceFile"] == "/api/market/prices"
    assert "csv" not in payload["priceFile"].lower()
    assert payload["canonicalSchema"] == ["price_date", "symbol", "price"]


def test_price_book_is_json_with_canonical_columns():
    book = PriceBookDto(
        sessionDate=_dt.date(2026, 3, 2),
        endpoint="/api/market/prices",
        label="Appendix C prices (JSON price service)",
        prices=[
            PriceRowDto(
                priceDate=_dt.date(2026, 3, 2),
                symbol="WEQ",
                name="World Equity",
                price=Decimal("52.11"),
                assetClass="equities",
            )
        ],
    )
    payload = json.loads(book.model_dump_json(by_alias=True))
    assert payload["format"] == "json"
    assert payload["canonicalSchema"] == ["price_date", "symbol", "price"]
    assert payload["prices"][0]["symbol"] == "WEQ"
    assert payload["prices"][0]["price"] == 52.11
