"""Synthetic price history standing in for the Appendix C file.

Seeded, so every team member and every CI run gets byte-identical prices -- the
golden trade arithmetic would otherwise drift between machines.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import random
from decimal import Decimal

from scripts._fixtures import INSTRUMENTS
from scripts.price_file import DEFAULT_PATH, write_price_file

SEED = 20260905
START = _dt.date(2026, 1, 5)  # a Monday
BUSINESS_DAYS = 120

#: Per-instrument daily drift and volatility. Cash barely moves; equities move most.
VOLATILITY = {
    "cash": (Decimal("0.00002"), Decimal("0.0001")),
    "bonds": (Decimal("0.00010"), Decimal("0.0020")),
    "equities": (Decimal("0.00035"), Decimal("0.0110")),
    "alternatives": (Decimal("0.00025"), Decimal("0.0140")),
}


def business_days(start: _dt.date, count: int) -> list[_dt.date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += _dt.timedelta(days=1)
    return days


def generate(days: int = BUSINESS_DAYS) -> list[tuple[_dt.date, str, Decimal]]:
    rng = random.Random(SEED)
    dates = business_days(START, days)
    rows: list[tuple[_dt.date, str, Decimal]] = []

    for symbol, _name, asset_class, _sat, opening in INSTRUMENTS:
        drift, vol = VOLATILITY[asset_class]
        price = opening
        for day in dates:
            rows.append((day, symbol, price))
            # Gaussian step in Decimal space, floored so a price can never
            # reach zero and make the trade arithmetic undefined.
            shock = Decimal(str(round(rng.gauss(0, float(vol)), 8)))
            price = max(price * (Decimal(1) + drift + shock), Decimal("0.010000"))
            price = price.quantize(Decimal("0.000001"))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_PATH)
    ap.add_argument("--days", type=int, default=BUSINESS_DAYS)
    args = ap.parse_args()

    rows = generate(args.days)
    write_price_file(args.out, rows)
    dates = sorted({d for d, _s, _p in rows})
    print(
        f"wrote {len(rows)} rows for {len(INSTRUMENTS)} instruments "
        f"over {len(dates)} business days ({dates[0]} .. {dates[-1]}) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
