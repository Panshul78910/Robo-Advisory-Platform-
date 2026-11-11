"""Generate the portfolio reference page the corpus is missing.

WHY THIS EXISTS
---------------
Appendix E is the authoritative suitability policy, and it numbers the model
portfolios 1 to 8. It never writes "P1". The platform numbers them P1 to P8 --
that is what `model_portfolio.code` holds, what `eligiblePortfolios` lists, what
the dashboard shows and what clients type.

Nothing in the corpus bridged the two. The only chunk that did was Table 3.1 of
a placeholder suitability policy whose source file was deleted in b62976a; it
survived in the index only because nothing cleared it, and `make reset` removes
it for good.

The effect was measurable. "Does P1 contain any equities? What percentage?"
retrieved nothing -- best cosine 0.318 against a 0.34 floor -- while "Does the
Capital Preservation portfolio contain equities?" scored 0.490 and grounded on
six chunks. The composition was in the corpus; the name the client used was not
in the same chunk as the numbers.

WHY IT IS GENERATED RATHER THAN WRITTEN
---------------------------------------
The alternative was adding a Code column to the instructor's Appendix E, which
would leave this team's corpus differing from the issued ground truth -- and the
price specification states that both teams must face identical conditions in red
team testing. The alternative to THAT was hand-writing a second copy of the
composition table, which is exactly the drift that caused this whole problem:
`_fixtures.py` and Appendix E disagreeing for seven weeks because two people
maintained the same numbers.

So this file is derived, never authored. It reads `model_portfolio` and
`model_portfolio_target`, which `fix_instrument_universe.py` has already aligned
to Appendix E, and regenerating it is part of `make reset`. If the database and
Appendix E ever diverge again, this page follows the database and the mismatch
shows up in the same place as every other mismatch, rather than hiding in a
third copy.

SHAPE
-----
One `###` section per portfolio, because the ingester starts a new chunk at each
heading and keeps tables whole. Each portfolio's code, Appendix E number, name,
weights and equity exposure therefore land in a single chunk, which is what
makes a question phrased with the code retrievable.

USAGE
-----
    python -m scripts.gen_portfolio_reference          # write the page
    python -m scripts.gen_portfolio_reference --check  # fail if out of date

Run after `scripts.seed` and before `app.rag.ingest`; `make reset` does both.
"""

from __future__ import annotations

import argparse
import pathlib
from decimal import Decimal

from app.db.session import connect_sync

OUT = pathlib.Path("docs/corpus/platform-portfolio-reference.md")

#: Appendix E calls the three core sleeves by role, not by ticker alone. Saying
#: both means a question phrased either way reaches this page.
ROLE = {
    "SPY": "broad equity",
    "AGG": "fixed income",
    "BIL": "cash / money-market",
    "XLK": "technology satellite",
    "XLV": "healthcare satellite",
    "XLE": "energy satellite",
    "SMH": "semiconductor / AI satellite",
}

HEADER = """# Platform Portfolio Reference

GENERATED FILE -- do not edit by hand. Produced from the platform database by
`scripts/gen_portfolio_reference.py`, and rewritten by `make reset`.

Appendix E: Company Suitability Policy is the authoritative source for the model
portfolios and their weights. Appendix E numbers the portfolios 1 to 8; the
platform refers to the same eight portfolios by the codes P1 to P8. This page
states the mapping and repeats the composition so that a question asked with a
portfolio code can be answered. Where this page and Appendix E differ, Appendix
E is correct.

The weights below are the default allocations with no satellite holding. As
Appendix E states, satellite exposure replaces part of the broad equity (SPY)
sleeve, and the stated weights sum to 100% when no satellite is held.
"""


def fetch(cur) -> list[dict]:
    cur.execute(
        "select p.code, p.name, p.risk_category, p.requires_cka,"
        "       i.symbol, i.asset_class, t.target_weight"
        " from model_portfolio p"
        " join model_portfolio_target t on t.portfolio_id = p.id"
        " join instrument i on i.id = t.instrument_id"
        " order by p.code, t.target_weight desc, i.symbol"
    )
    by_code: dict[str, dict] = {}
    for code, name, category, cka, symbol, asset_class, weight in cur.fetchall():
        entry = by_code.setdefault(
            code,
            {"code": code, "name": name, "category": category, "cka": cka, "holdings": []},
        )
        entry["holdings"].append(
            {"symbol": symbol, "asset_class": asset_class, "weight": Decimal(weight)}
        )
    return [by_code[c] for c in sorted(by_code)]


def _pct(value: Decimal) -> str:
    """Trim the trailing zeros Postgres numerics carry, so 10.0000 reads 10."""
    return f"{value.normalize():f}"


def render(portfolios: list[dict]) -> str:
    symbols = sorted(
        {h["symbol"] for p in portfolios for h in p["holdings"]},
        key=lambda s: list(ROLE).index(s) if s in ROLE else 99,
    )

    out = [HEADER, "\n## Portfolio codes and composition\n"]
    out.append(
        "| Code | Appendix E | Name | " + " | ".join(symbols) + " | Equity | Knowledge check |"
    )
    out.append("|---|---|---|" + "---|" * (len(symbols) + 2))
    for p in portfolios:
        weights = {h["symbol"]: h["weight"] for h in p["holdings"]}
        equity = sum(h["weight"] for h in p["holdings"] if h["asset_class"] == "equities")
        cells = [_pct(weights[s]) + "%" if s in weights else "0%" for s in symbols]
        out.append(
            f"| {p['code']} | {p['code'][1:]} | {p['name']} | " + " | ".join(cells)
            + f" | {_pct(equity)}% | {'Required' if p['cka'] else 'Not required'} |"
        )

    out.append("\n## Portfolio detail\n")
    for p in portfolios:
        number = p["code"][1:]
        equity = sum(h["weight"] for h in p["holdings"] if h["asset_class"] == "equities")
        out.append(f"### {p['code']} - {p['name']}\n")
        out.append(
            f"{p['code']} is the platform's code for portfolio {number} in Appendix E, "
            f"named {p['name']}. It is the portfolio for clients assessed at the "
            f"{p['category'].replace('_', ' ')} risk category."
        )
        out.append("")
        out.append(f"{p['code']} holds:")
        out.append("")
        for h in p["holdings"]:
            role = ROLE.get(h["symbol"], h["asset_class"])
            out.append(f"- {_pct(h['weight'])}% {h['symbol']} ({role}, {h['asset_class']})")
        out.append("")
        if equity:
            out.append(
                f"Equity exposure in {p['code']} ({p['name']}) is {_pct(equity)}% "
                f"of the portfolio. {p['code']} does contain equities."
            )
        else:
            out.append(
                f"{p['code']} ({p['name']}) holds no equities. Its equity exposure is 0%."
            )
        out.append("")
        out.append(
            f"The knowledge check is {'required' if p['cka'] else 'not required'} "
            f"before holding {p['code']}."
        )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the file on disk is out of date")
    args = ap.parse_args()

    with connect_sync() as conn, conn.cursor() as cur:
        portfolios = fetch(cur)

    if not portfolios:
        print("no model portfolios in the database -- run scripts.seed first")
        return 1

    body = render(portfolios)

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != body:
            print(f"{OUT} is out of date -- run python -m scripts.gen_portfolio_reference")
            return 1
        print(f"{OUT} is up to date")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(body, encoding="utf-8")
    print(f"wrote {OUT} ({len(portfolios)} portfolios, {len(body)} chars)")
    print("re-ingest to pick it up:")
    print("  python -m app.rag.ingest --source docs/corpus --corpus-label bootstrap"
          " --audience client --reset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
