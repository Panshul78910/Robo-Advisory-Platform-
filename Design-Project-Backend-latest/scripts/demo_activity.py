"""Put the seeded clients into the state a demo starts from.

WHY THIS EXISTS
---------------
`scripts.seed` writes reference data and five client profiles, and stops there.
Everything a dashboard actually shows -- a risk category, an eligible-portfolio
list, holdings, trades, a performance line -- is created by *using* the product,
so a freshly reset database shows five clients who have never invested.

That was fine while one person ran the demo in a browser. It is not fine for a
team: "log in as five clients and click through profiling and investing" is not
a reproducible step, and the holdings it produces depend on which portfolio the
person happened to click. This script does it the same way every time.

WHAT IT DOES
------------
For each seeded client, over the real HTTP API so every write is audited exactly
as a browser's would be:

  1. log in
  2. set the session date (all pricing resolves against it -- invariant 4)
  3. POST /clients/me/reassess  -- scores the stored profile, writes a
     `risk_assessment` row and a `risk_assessment` decision
  4. POST /clients/me/invest    -- into the client's own highest eligible
     portfolio, writing a `portfolio_event`, a `holdings_snapshot` with lines,
     the `trade` rows and a `recommendation` decision

Step 4 asks for `maxPortfolio` from step 3 rather than a hard-coded code, so the
suitability engine chooses and the result cannot drift from the policy. C004's
profile is deliberately incoherent (Appendix D User 4), so reassess returns no
category and this script leaves C004 un-invested -- which is the correct state
to demo clarifying questions from, not a failure.

It is idempotent: a client who already has an invest event is skipped, so
running it twice does not double the holdings.

REQUIREMENTS
------------
The API must be running and the database seeded and priced. Order:

    python -m scripts.migrate --reset
    python -m scripts.seed
    python -m scripts.gen_prices
    python -m scripts.load_prices
    python -m scripts.serve --port 8000        # in another terminal
    python -m scripts.demo_activity

USAGE
-----
    python -m scripts.demo_activity
    python -m scripts.demo_activity --base-url http://127.0.0.1:8001
    python -m scripts.demo_activity --session-date 2026-06-19
    python -m scripts.demo_activity --dry-run

Prices are generated from a fixed seed, so the same session date yields the same
unit counts on every machine.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys

import httpx

from scripts._fixtures import CLIENTS, DEFAULT_PASSWORD

#: The last date `gen_prices` produces. Holdings are valued here, so pinning it
#: is what makes two machines agree on the unit counts.
DEFAULT_SESSION_DATE = _dt.date(2026, 6, 19)

TIMEOUT = httpx.Timeout(60.0)


def _fail(response: httpx.Response, what: str) -> str:
    """The API's own message, which is written to be shown to a person."""
    try:
        body = response.json()
    except ValueError:
        return f"{what}: HTTP {response.status_code}"
    detail = body.get("detail") or body.get("message") or body
    return f"{what}: HTTP {response.status_code} {detail}"


def drive_client(
    client: httpx.Client, code: str, session_date: _dt.date, *, dry_run: bool
) -> tuple[bool, str]:
    """Return (changed, one-line report) for a single client."""
    email = f"{code.lower()}@roboadvisor.local"

    login = client.post("/api/auth/login", json={"email": email, "password": DEFAULT_PASSWORD})
    if login.status_code != 200:
        return False, _fail(login, f"{code} login")
    auth = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    dated = client.post(
        "/api/session/date", json={"sessionDate": session_date.isoformat()}, headers=auth
    )
    if dated.status_code != 200:
        return False, _fail(dated, f"{code} session date")

    events = client.get("/api/clients/me/events", headers=auth)
    if events.status_code != 200:
        return False, _fail(events, f"{code} events")
    if any(e["eventType"] == "invest" for e in events.json()):
        return False, f"{code} already invested -- left alone"

    assessed = client.post("/api/clients/me/reassess", headers=auth)
    if assessed.status_code != 200:
        return False, _fail(assessed, f"{code} reassess")
    eligibility = assessed.json()
    category, target = eligibility.get("riskCategory"), eligibility.get("maxPortfolio")

    if not category or not target:
        # The profile did not score. Expected for C004, whose answers conflict.
        why = (
            eligibility.get("inconsistencies") or eligibility.get("missingFields") or "no category"
        )
        reason = (
            ", ".join(i["code"] for i in why)
            if isinstance(why, list) and why and isinstance(why[0], dict)
            else str(why)
        )
        # No write happened: `reassess` returns early without recording an
        # assessment when the profile is incomplete or self-contradictory.
        return False, f"{code} not scored, not invested -- profile is incoherent ({reason})"

    if dry_run:
        return False, f"{code} would invest in {target} ({category})"

    invested = client.post(
        "/api/clients/me/invest",
        json={"portfolioCode": target},
        # Deterministic per client, so a re-run of a half-finished pass replays
        # the same order rather than placing a second one.
        headers={**auth, "Idempotency-Key": f"demo-activity-{code}-{target}"},
    )
    if invested.status_code != 200:
        return False, _fail(invested, f"{code} invest {target}")

    trades = invested.json().get("trades") or []
    legs = ", ".join(f"{t.get('symbol', '?')}" for t in trades)
    return True, f"{code} {category} -> {target} ({len(trades)} trades: {legs})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8000", help="where the API is serving")
    ap.add_argument(
        "--session-date",
        type=_dt.date.fromisoformat,
        default=DEFAULT_SESSION_DATE,
        help=f"simulated date all pricing resolves against (default {DEFAULT_SESSION_DATE})",
    )
    ap.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    args = ap.parse_args()

    print(f"API      {args.base_url}")
    print(f"session  {args.session_date}")
    print(f"mode     {'dry run -- no writes' if args.dry_run else 'applying'}\n")

    changed = 0
    problems: list[str] = []
    with httpx.Client(base_url=args.base_url, timeout=TIMEOUT) as client:
        try:
            client.get("/healthz")
        except httpx.HTTPError as exc:
            print(f"cannot reach {args.base_url}: {exc}")
            print("start the API first:  python -m scripts.serve --port 8000")
            return 1

        for spec in CLIENTS:
            did, report = drive_client(
                client, spec["code"], args.session_date, dry_run=args.dry_run
            )
            print(f"  {report}")
            changed += did
            if "HTTP" in report:
                problems.append(report)

    print(f"\n{changed} client(s) changed")
    if problems:
        print(f"{len(problems)} failed -- the database is unchanged for those clients")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
