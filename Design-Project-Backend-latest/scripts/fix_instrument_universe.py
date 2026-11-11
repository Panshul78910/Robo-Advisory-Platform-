"""Replace the placeholder instrument universe with Appendix C and E.

WHY THIS EXISTS
---------------
`scripts/_fixtures.py` was written on 2026-09-08 and its own docstring says it
"stands in for Appendix D (clients) and the instructor's instrument/portfolio
definitions until the real files arrive". Those files arrived -- Appendix E on
2026-10-27, Appendix C on 2026-10-30 -- and only the suitability policy half was
replaced. The instrument half stayed, so every seeded database described a
platform built from ten invented tickers (CASHF, SGSB, GLBD, CORPB, WEQ, SGEQ,
EMEQ, TECH, REIT, GOLD) while the corpus described one built from seven real
ETFs. The overlap between the two sets is empty in both directions: none of the
ten appears in any of the 1418 corpus chunks, and none of the seven appeared in
`instrument`.

The visible symptom was an assistant that could not answer "Does P1 contain any
equities?". The database said 40% cash + 60% bonds; Appendix E says 10% SPY /
60% AGG / 30% BIL. Neither reached the model, so it correctly said it did not
know -- a true statement about a system holding two contradictory answers.

WHAT IT CHANGES
---------------
  instrument              adds the 7 of Appendix C C.4
  model_portfolio         names + CKA gate  ->  Appendix E sections 1 and 4
  model_portfolio_target  50 invented rows  ->  Appendix E section 1 defaults
  instrument_price        drops prices for retired tickers NOTHING STILL HOLDS

WHAT IT DELIBERATELY LEAVES ALONE
---------------------------------
The ten placeholder `instrument` rows stay. `trade`, `holdings_snapshot`,
`holdings_snapshot_line`, `portfolio_event`, `risk_assessment`, `decision` and
`decision_source` are append-only -- a BEFORE DELETE trigger raises
"table is append-only; record a correcting row instead of DELETE". That is a
deliberate audit control, and existing trades and holdings are denominated in
the old instruments. Deleting the instruments would either break that history or
require tearing through the control, so retired tickers remain as the
denomination of rows that by design cannot be removed.

The consequence is reported, not hidden: after this runs, no model portfolio
references a retired ticker, but clients may still *hold* them. A demo database
that should show holdings in the real universe needs a full reset, which drops
and recreates the tables and so does not fight the triggers:

    make reset

`model_portfolio.risk_category` keeps its enum values. Appendix E names the
categories after the portfolios ("Conservative Balanced", "Growth Balanced",
"Very Aggressive"); the database uses moderately_conservative,
moderately_aggressive and very_aggressive. The ordering is identical, which is
all the Appendix E section 2 eligibility rule depends on, and the enum is a
Postgres type referenced by `risk_assessment.category`, so renaming it is a
schema migration rather than a data edit.

The per-portfolio satellite cap (0% / 5% / 10% / 15% of the SPY sleeve) is not
stored. `model_portfolio` has no column for it, and `policy/suitability.yaml`
already carries the caps the engine enforces. The targets here are the
no-satellite defaults, which Appendix E states sum to 100%.

USAGE
-----
    python -m scripts.fix_instrument_universe              # report only
    python -m scripts.fix_instrument_universe --apply      # make the changes

Then regenerate prices for the new tickers:

    python -m scripts.gen_prices
    python -m scripts.load_prices

Safe to re-run: it upserts by symbol and portfolio code, verifies the result
against the appendices, and rolls back if anything fails to match.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import uuid
from decimal import Decimal

from app.db.session import connect_sync
from scripts._fixtures import INSTRUMENTS, PORTFOLIOS

#: Tables this script rewrites. Both are ordinary reference data; neither
#: carries an append-only trigger.
REWRITTEN = ("model_portfolio_target", "instrument_price")

#: Tables that reference an instrument and cannot be touched. Checked so the
#: report can say how much history still points at a retired ticker.
APPEND_ONLY = ("trade", "holdings_snapshot_line")

BACKUP_DIR = pathlib.Path("backups")


class _Encoder(json.JSONEncoder):
    """Everything the backup can meet: ids are UUIDs, weights and prices are
    Decimals, and timestamps come back as dates."""

    def default(self, o):
        if isinstance(o, Decimal | uuid.UUID):
            return str(o)
        if isinstance(o, _dt.date | _dt.datetime):
            return o.isoformat()
        return super().default(o)


def _rows(cur, sql: str, args: tuple = ()) -> list[dict]:
    cur.execute(sql, args)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def survey(cur) -> dict:
    """What is in the database now, and how far is it from the appendices?"""
    current = _rows(cur, "select symbol, name from instrument order by symbol")
    wanted = {s for s, *_ in INSTRUMENTS}
    have = {r["symbol"] for r in current}
    # Not "to retire": these rows stay, because append-only history is
    # denominated in them. They are listed so the report shows what is no
    # longer offered, not what is about to be deleted.
    legacy = sorted(have - wanted)

    stranded = {}
    for table in APPEND_ONLY:
        stranded[table] = _rows(
            cur,
            f"select count(*) as n from {table} t join instrument i on i.id = t.instrument_id"
            " where i.symbol <> all(%s)",
            ([s for s, *_ in INSTRUMENTS],),
        )[0]["n"]

    return {
        "instruments": current,
        "legacy": legacy,
        "install": sorted(wanted - have),
        # Correctness is "the seven are installed and everything verifies",
        # not "nothing else exists" -- the legacy rows are permanent.
        "correct": not (wanted - have),
        "counts": {t: _rows(cur, f"select count(*) as n from {t}")[0]["n"] for t in REWRITTEN},
        "stranded": stranded,
    }


def back_up(cur) -> pathlib.Path:
    """Write every row this run rewrites, so a mistake is recoverable."""
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = BACKUP_DIR / f"instrument_universe_{stamp}.json"
    payload: dict = {
        "written": stamp,
        "reason": "scripts/fix_instrument_universe.py replacing placeholder tickers",
        "instrument": _rows(cur, "select * from instrument"),
        "model_portfolio": _rows(cur, "select * from model_portfolio"),
    }
    for table in REWRITTEN:
        payload[table] = _rows(cur, f"select * from {table}")
    path.write_text(json.dumps(payload, indent=1, cls=_Encoder), encoding="utf-8")
    return path


def apply_fix(cur) -> dict:
    """Install the Appendix C universe and the Appendix E portfolios."""
    done: dict = {"deleted": {}, "instruments": 0, "portfolios": 0, "targets": 0}
    keep = [s for s, *_ in INSTRUMENTS]

    # Targets are rewritten wholesale: every portfolio is redefined below, so
    # there is nothing in the old set worth merging.
    cur.execute("delete from model_portfolio_target")
    done["deleted"]["model_portfolio_target"] = cur.rowcount

    # Prices for a retired ticker are dropped ONLY if nothing still holds it.
    #
    # The first version of this script deleted them unconditionally, and the
    # dashboard died with "No price on 2026-06-02 for 6 instrument(s)": append-
    # only history still holds the legacy instruments, and a holding with no
    # price cannot be valued. Those prices are also what lets a client
    # `rebalance` OUT of the old universe through the normal audited path --
    # rebalance prices the current holdings and the new targets, so deleting
    # them strands the client with no supported way back.
    cur.execute(
        "delete from instrument_price where instrument_id in ("
        "  select i.id from instrument i"
        "  where i.symbol <> all(%s)"
        "    and not exists (select 1 from holdings_snapshot_line l"
        "                    where l.instrument_id = i.id)"
        "    and not exists (select 1 from trade t where t.instrument_id = i.id))",
        (keep,),
    )
    done["deleted"]["instrument_price"] = cur.rowcount

    ids: dict[str, str] = {}
    for symbol, name, asset_class, is_satellite, _price in INSTRUMENTS:
        cur.execute(
            "insert into instrument (symbol, name, asset_class, is_satellite)"
            " values (%s, %s, %s, %s)"
            " on conflict (symbol) do update set name = excluded.name,"
            " asset_class = excluded.asset_class, is_satellite = excluded.is_satellite"
            " returning id",
            (symbol, name, asset_class, is_satellite),
        )
        ids[symbol] = cur.fetchone()[0]
        done["instruments"] += 1

    for code, name, category, requires_cka, targets in PORTFOLIOS:
        cur.execute(
            "insert into model_portfolio (code, name, risk_category, requires_cka)"
            " values (%s, %s, %s, %s)"
            " on conflict (code) do update set name = excluded.name,"
            " risk_category = excluded.risk_category,"
            " requires_cka = excluded.requires_cka returning id",
            (code, name, category, requires_cka),
        )
        portfolio_id = cur.fetchone()[0]
        done["portfolios"] += 1
        for symbol, weight in targets.items():
            cur.execute(
                "insert into model_portfolio_target (portfolio_id, instrument_id,"
                " target_weight) values (%s, %s, %s)"
                " on conflict (portfolio_id, instrument_id) do update set"
                " target_weight = excluded.target_weight",
                (portfolio_id, ids[symbol], weight),
            )
            done["targets"] += 1
    return done


def verify(cur) -> list[str]:
    """Check the result against the appendices. Returns a list of problems."""
    problems: list[str] = []

    have = {r["symbol"] for r in _rows(cur, "select symbol from instrument")}
    missing = {s for s, *_ in INSTRUMENTS} - have
    if missing:
        problems.append(f"Appendix C C.4 tickers absent from `instrument`: {sorted(missing)}")

    # No model portfolio may reference a ticker outside the Appendix C universe.
    offenders = _rows(
        cur,
        "select distinct i.symbol from model_portfolio_target t"
        " join instrument i on i.id = t.instrument_id where i.symbol <> all(%s)",
        ([s for s, *_ in INSTRUMENTS],),
    )
    if offenders:
        problems.append(
            "model portfolios still target non-Appendix-C tickers: "
            f"{sorted(r['symbol'] for r in offenders)}"
        )

    for row in _rows(cur, "select p.code, sum(t.target_weight) as total"
                          " from model_portfolio p"
                          " join model_portfolio_target t on t.portfolio_id = p.id"
                          " group by p.code order by p.code"):
        if Decimal(row["total"]) != Decimal(100):
            problems.append(f"{row['code']} weights sum to {row['total']}%, not 100%")

    # The question that exposed all this: P1 is 10% SPY per Appendix E, so
    # "does P1 contain any equities?" has the answer yes, 10%.
    p1 = _rows(cur, "select i.symbol, t.target_weight from model_portfolio p"
                    " join model_portfolio_target t on t.portfolio_id = p.id"
                    " join instrument i on i.id = t.instrument_id"
                    " where p.code = 'P1' order by i.symbol")
    actual = {r["symbol"]: Decimal(r["target_weight"]) for r in p1}
    expected = {"SPY": Decimal(10), "AGG": Decimal(60), "BIL": Decimal(30)}
    if actual != expected:
        problems.append(f"P1 is {actual}, Appendix E section 1 says {expected}")

    gated = {r["code"] for r in _rows(cur, "select code from model_portfolio where requires_cka")}
    if gated != {"P5", "P6", "P7", "P8"}:
        problems.append(f"knowledge check gates {sorted(gated)},"
                        " Appendix E section 4 gates P5-P8")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="make the changes; without it, only report")
    args = ap.parse_args()

    with connect_sync() as conn, conn.cursor() as cur:
        state = survey(cur)

        print("current instrument universe:")
        for row in state["instruments"]:
            mark = "  legacy" if row["symbol"] in state["legacy"] else "  offered"
            print(f"{mark:9} {row['symbol']:6} {row['name']}")
        print()
        print(f"  no longer offered: {', '.join(state['legacy']) or 'none'}")
        print(f"  to install       : {', '.join(state['install']) or 'none'}")
        print()
        print("rows this script rewrites:")
        for table, n in state["counts"].items():
            print(f"  {table:24} {n}")

        if state["correct"] and not verify(cur):
            print("\nalready matches Appendix C and E -- nothing to do.")
            return 0

        if not args.apply:
            print("\nDRY RUN. Re-run with --apply to make these changes.")
            print("Everything rewritten is backed up to backups/ first.")
            return 0

        path = back_up(cur)
        print(f"\nbacked up to {path}")

        done = apply_fix(cur)
        print("\ndeleted:")
        for table, n in done["deleted"].items():
            print(f"  {table:24} {n}")
        print(f"\ninstalled {done['instruments']} instruments,"
              f" {done['portfolios']} portfolios, {done['targets']} targets")

        problems = verify(cur)
        if problems:
            conn.rollback()
            print("\nVERIFICATION FAILED -- rolled back, database unchanged:")
            for p in problems:
                print(f"  - {p}")
            return 1
        conn.commit()
        stranded = state["stranded"]

    print("\nverified against Appendix C C.4 and Appendix E sections 1 and 4.")
    print("\nnext, regenerate prices for the new tickers:")
    print("  python -m scripts.gen_prices")
    print("  python -m scripts.load_prices")

    if any(stranded.values()):
        print("\nNOTE: append-only history still references retired tickers --")
        for table, n in stranded.items():
            if n:
                print(f"  {table:24} {n} rows")
        print("  These cannot be deleted (audit triggers), so clients still hold")
        print("  instruments the platform no longer offers. Their prices were")
        print("  kept so those holdings can still be valued and so each client")
        print("  can `rebalance` into the new targets through the normal path.")
        print("  For a clean demo database instead, run `make reset`, which")
        print("  recreates the tables rather than fighting the triggers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
