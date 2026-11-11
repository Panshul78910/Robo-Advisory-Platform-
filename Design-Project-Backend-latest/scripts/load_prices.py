"""Load Appendix C prices into `instrument_price`. Idempotent.

CSV is the canonical source format (price_date, symbol, price). The domain lead
hands that file to the software lead. This script loads it into the service
database; clients then read JSON from GET /api/market/prices — they never see
the CSV.

`--url` pulls the same schema from an instructor-hosted HTTP JSON service.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import urllib.request

from app.core.config import get_settings
from app.db.session import connect_sync
from scripts.price_file import DEFAULT_PATH, parse_price_file, parse_price_json


def _rows_from_args(args) -> tuple[list, str]:
    settings = get_settings()
    url = args.url or settings.price_service_url
    if url:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return parse_price_json(payload), url
    if not args.file.exists():
        raise SystemExit(f"{args.file} not found -- run `python -m scripts.gen_prices` first")
    return list(parse_price_file(args.file)), str(args.file)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=pathlib.Path, default=DEFAULT_PATH)
    ap.add_argument("--url", default="", help="Instructor JSON price service URL (overrides CSV)")
    args = ap.parse_args()

    parsed, origin = _rows_from_args(args)

    with connect_sync() as conn:
        with conn.cursor() as cur:
            cur.execute("select symbol, id from instrument")
            by_symbol = dict(cur.fetchall())
            if not by_symbol:
                raise SystemExit("no instruments -- run `python -m scripts.seed` first")

            rows, unknown = [], set()
            for day, symbol, price in parsed:
                instrument_id = by_symbol.get(symbol)
                if instrument_id is None:
                    unknown.add(symbol)
                    continue
                rows.append((instrument_id, day, price))

            cur.executemany(
                "insert into instrument_price (instrument_id, price_date, price)"
                " values (%s, %s, %s) on conflict do nothing",
                rows,
            )
            cur.execute("select count(distinct price_date) from instrument_price")
            dates = cur.fetchone()[0]
        conn.commit()

    if unknown:
        print(f"warning: skipped unknown symbol(s) {sorted(unknown)}")
    print(f"loaded {len(rows)} price rows from {origin} covering {dates} distinct dates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
