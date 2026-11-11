"""Bootstrap accounts, instruments and model portfolios (A.1 v).

Idempotent: re-running updates in place rather than duplicating, so it is safe
to run against an existing database.
"""

from __future__ import annotations

from argon2 import PasswordHasher

from app.db.session import connect_sync
from app.domain.suitability.rules import load_policy
from scripts._fixtures import ADMIN, CLIENTS, DEFAULT_PASSWORD, INSTRUMENTS, PORTFOLIOS

hasher = PasswordHasher()

PROFILE_COLUMNS = (
    "date_of_birth",
    "monthly_salary",
    "monthly_commitments",
    "dependants",
    "investment_objective",
    "investment_horizon_yrs",
    "loss_tolerance",
    "liquidity_needs",
    "income_stability",
    "investment_experience",
    "cka_passed",
)


def _upsert_user(cur, email: str, display_name: str, role: str) -> str:
    """Create or refresh an account and its password.

    Credentials go to `app_credential`, not Supabase's `auth.users` -- see
    008_local_credentials.sql for why.
    """
    cur.execute(
        "insert into app_user (email, role, display_name) values (%s, %s, %s)"
        " on conflict (email) do update set role = excluded.role,"
        " display_name = excluded.display_name"
        " returning id",
        (email, role, display_name),
    )
    user_id = cur.fetchone()[0]
    cur.execute(
        "insert into app_credential (user_id, password_hash) values (%s, %s)"
        " on conflict (user_id) do update set password_hash = excluded.password_hash,"
        " updated_at = now()",
        (user_id, hasher.hash(DEFAULT_PASSWORD)),
    )
    return user_id


def seed_identities(cur) -> None:
    _upsert_user(cur, ADMIN["email"], ADMIN["name"], "admin")

    for spec in CLIENTS:
        email = f"{spec['code'].lower()}@roboadvisor.local"
        user_id = _upsert_user(cur, email, spec["name"], "client")
        cur.execute(
            "insert into client (user_id, client_code) values (%s, %s)"
            " on conflict (client_code) do update set user_id = excluded.user_id"
            " returning id",
            (user_id, spec["code"]),
        )
        client_id = cur.fetchone()[0]

        values = [spec[c] for c in PROFILE_COLUMNS]
        assignments = ", ".join(f"{c} = excluded.{c}" for c in PROFILE_COLUMNS)
        columns = ", ".join(PROFILE_COLUMNS)
        cur.execute(
            f"insert into client_profile"
            f" (client_id, full_name, email, {columns})"
            f" values ({', '.join(['%s'] * (len(PROFILE_COLUMNS) + 3))})"
            f" on conflict (client_id) do update set full_name = excluded.full_name,"
            f" email = excluded.email, {assignments}",
            (client_id, spec["name"], email, *values),
        )


def seed_instruments(cur) -> None:
    for symbol, name, asset_class, is_satellite, _price in INSTRUMENTS:
        cur.execute(
            "insert into instrument (symbol, name, asset_class, is_satellite)"
            " values (%s, %s, %s, %s) on conflict (symbol) do update set"
            " name = excluded.name, asset_class = excluded.asset_class,"
            " is_satellite = excluded.is_satellite",
            (symbol, name, asset_class, is_satellite),
        )


def seed_portfolios(cur) -> None:
    for code, name, category, requires_cka, targets in PORTFOLIOS:
        total = sum(targets.values())
        if total != 100:
            raise SystemExit(f"portfolio {code} target weights sum to {total}, expected 100")
        cur.execute(
            "insert into model_portfolio (code, name, risk_category, requires_cka)"
            " values (%s, %s, %s, %s) on conflict (code) do update set"
            " name = excluded.name, risk_category = excluded.risk_category,"
            " requires_cka = excluded.requires_cka returning id",
            (code, name, category, requires_cka),
        )
        portfolio_id = cur.fetchone()[0]
        cur.execute("delete from model_portfolio_target where portfolio_id = %s", (portfolio_id,))
        for symbol, weight in targets.items():
            cur.execute(
                "insert into model_portfolio_target (portfolio_id, instrument_id, target_weight)"
                " select %s, id, %s from instrument where symbol = %s",
                (portfolio_id, weight, symbol),
            )


def check_satellite_caps(cur) -> None:
    """Fail loudly if seed data contradicts the suitability policy.

    A portfolio that breaches its own category's satellite cap would make A.3
    untestable, so it is caught here rather than at demo time.
    """
    policy = load_policy()
    cur.execute(
        "select p.code, p.risk_category, coalesce(sum(t.target_weight) filter"
        " (where i.is_satellite), 0)"
        " from model_portfolio p"
        " join model_portfolio_target t on t.portfolio_id = p.id"
        " join instrument i on i.id = t.instrument_id"
        " group by p.code, p.risk_category"
    )
    for code, category, exposure in cur.fetchall():
        cap = policy.satellite_cap(category)
        if exposure > cap:
            raise SystemExit(
                f"portfolio {code} holds {exposure}% satellite, above the {cap}% cap "
                f"for {category} in policy/suitability.yaml"
            )


def main() -> int:
    with connect_sync() as conn:
        with conn.cursor() as cur:
            seed_identities(cur)
            seed_instruments(cur)
            seed_portfolios(cur)
            check_satellite_caps(cur)
        conn.commit()
    print(
        f"seeded {len(CLIENTS)} clients + 1 admin, {len(INSTRUMENTS)} instruments, "
        f"{len(PORTFOLIOS)} portfolios (password: {DEFAULT_PASSWORD})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
