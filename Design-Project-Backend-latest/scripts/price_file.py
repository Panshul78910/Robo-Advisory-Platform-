"""The one place that knows the price file's format.

When the real Appendix C file arrives, `parse_price_file` is the only function
that changes -- generation, loading and every consumer stay as they are.
"""

from __future__ import annotations

import csv
import datetime as _dt
import pathlib
from collections.abc import Iterator
from decimal import Decimal

DEFAULT_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "prices" / "prices.csv"
HEADER = ("price_date", "symbol", "price")


def parse_price_json(payload) -> list[tuple[_dt.date, str, Decimal]]:
    """Accept the instructor JSON price service (or our own /api/market/prices).

    Canonical row fields remain Appendix C: price_date, symbol, price.
    Alternate keys `date` / `priceDate` are tolerated so a hosted service can
    speak camelCase without a second loader.
    """
    if isinstance(payload, dict):
        rows = payload.get("prices") or payload.get("data") or payload.get("rows") or []
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("price JSON must be a list of rows or an object with a prices array")
    out: list[tuple[_dt.date, str, Decimal]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"price row {i} is not an object")
        raw_date = row.get("price_date") or row.get("priceDate") or row.get("date")
        symbol = row.get("symbol")
        raw_price = row.get("price")
        if not raw_date or not symbol or raw_price is None:
            raise ValueError(f"price row {i} needs price_date, symbol and price")
        if isinstance(raw_date, str):
            day = _dt.date.fromisoformat(raw_date[:10])
        elif isinstance(raw_date, _dt.date):
            day = raw_date
        else:
            raise ValueError(f"price row {i} has an unreadable date")
        out.append((day, str(symbol).strip(), Decimal(str(raw_price))))
    return out


def parse_price_file(path: pathlib.Path) -> Iterator[tuple[_dt.date, str, Decimal]]:
    """Yield (date, symbol, price) rows. Long format: one row per instrument per day."""
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = set(HEADER) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name} is missing column(s): {sorted(missing)}")
        for row in reader:
            yield (
                _dt.date.fromisoformat(row["price_date"].strip()),
                row["symbol"].strip(),
                Decimal(row["price"].strip()),
            )


def write_price_file(path: pathlib.Path, rows: list[tuple[_dt.date, str, Decimal]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        for day, symbol, price in rows:
            writer.writerow([day.isoformat(), symbol, f"{price:.6f}"])
