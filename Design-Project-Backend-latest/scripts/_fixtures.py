"""Placeholder reference data and synthetic clients.

Stands in for Appendix D (clients) and the instructor's instrument/portfolio
definitions until the real files arrive. Kept as plain data so replacing it is a
data edit, not a code change.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal as D

EMAIL_DOMAIN = "@roboadvisor.local"
#: Development only. Never used outside a local seeded database.
DEFAULT_PASSWORD = "password123"

# symbol, name, asset_class, is_satellite, opening price
#
# The universe is Appendix C section C.4, verbatim: seven ETFs, no single stocks.
# It replaced a ten-instrument invented set (CASHF, SGSB, GLBD, CORPB, WEQ,
# SGEQ, EMEQ, TECH, REIT, GOLD) that was written on 2026-09-08 to stand in until
# the instructor's definitions arrived. They arrived on 2026-10-30 and nothing
# reconciled the two, so the database described a platform that no corpus
# document mentions -- 0 of the invented tickers appear in any of the 1418
# chunks, and none of these seven appeared in `instrument`.
#
# The OPENING PRICES remain synthetic. Appendix C C.0/C.1 says prices are
# served by an instructor-hosted price service whose endpoint is published in
# Week 4 and must not be hard-coded, so `gen_prices` stays a stand-in until
# `load_prices` can point at the real feed. Only the tickers it generates for
# were wrong, not the fact that it generates them.
INSTRUMENTS = [
    ("SPY", "S&P 500 ETF", "equities", False, D("585.400000")),
    ("AGG", "US Aggregate Bond ETF", "bonds", False, D("98.200000")),
    ("BIL", "1-3 Month T-Bill ETF", "cash", False, D("91.600000")),
    ("XLK", "Technology Sector ETF", "equities", True, D("236.800000")),
    ("XLV", "Healthcare Sector ETF", "equities", True, D("147.300000")),
    ("XLE", "Energy Sector ETF", "equities", True, D("92.750000")),
    ("SMH", "Semiconductor / AI Thematic ETF", "equities", True, D("254.100000")),
]

SATELLITE_SYMBOLS = {symbol for symbol, _n, _ac, is_satellite, _p in INSTRUMENTS if is_satellite}

# code, name, risk_category, requires_cka, {symbol: target weight %}
#
# Names, weights and the knowledge-check gate are Appendix E sections 1 and 4,
# the document that calls itself "the platform's authoritative suitability
# policy". The weights are the DEFAULTS, with no satellite taken: Appendix E
# states that satellite exposure replaces part of the SPY sleeve and that the
# SPY/AGG/BIL figures sum to 100% when none is held. The per-portfolio satellite
# cap (0%, 5%, 10%, 15%) has nowhere to live in `model_portfolio` yet, so it is
# not represented here -- see the note in scripts/fix_instrument_universe.py.
#
# `risk_category` keeps the existing enum values. Appendix E names categories
# after the portfolios (3 is "Conservative Balanced", 5 is "Growth Balanced",
# 8 is "Very Aggressive"), but the enum is a Postgres type referenced by
# `risk_assessment.category`, so renaming it is a schema migration rather than
# the data edit this file was built for. The ordering is identical, which is
# all the eligibility rule in Appendix E section 2 depends on.
PORTFOLIOS = [
    ("P1", "Capital Preservation", "capital_preservation", False,
     {"SPY": 10, "AGG": 60, "BIL": 30}),
    ("P2", "Conservative Income", "conservative", False,
     {"SPY": 25, "AGG": 60, "BIL": 15}),
    ("P3", "Conservative Balanced", "moderately_conservative", False,
     {"SPY": 40, "AGG": 50, "BIL": 10}),
    ("P4", "Balanced", "balanced", False,
     {"SPY": 55, "AGG": 35, "BIL": 10}),
    # Appendix E section 4 gates portfolios 5-8 and any satellite exposure
    # behind the knowledge check. The placeholder gated 6-8, so P5 was
    # reachable without it.
    ("P5", "Growth Balanced", "moderately_aggressive", True,
     {"SPY": 70, "AGG": 25, "BIL": 5}),
    ("P6", "Growth", "growth", True,
     {"SPY": 80, "AGG": 15, "BIL": 5}),
    ("P7", "Aggressive Growth", "aggressive", True,
     {"SPY": 90, "AGG": 10}),
    ("P8", "Very Aggressive", "very_aggressive", True,
     {"SPY": 100}),
]

ADMIN = {"code": "ADM", "name": "Compliance Officer", "email": f"admin{EMAIL_DOMAIN}"}

# Appendix D stand-ins. Deliberately varied so a supervisor can exercise every
# branch of the profiling and suitability logic without hand-building test data.
CLIENTS = [
    {
        "code": "C001",
        "name": "Lim Ah Hock",
        "note": "Appendix D User 1: low risk, no CKA, zero loss, short horizon → P1",
        "date_of_birth": _dt.date(1968, 3, 12),
        "monthly_salary": D("4500"),
        "monthly_commitments": D("1800"),
        "dependants": 1,
        "investment_objective": "income",
        "investment_horizon_yrs": 2,
        "loss_tolerance": "zero",
        "liquidity_needs": "medium",
        "income_stability": "stable",
        "investment_experience": "none",
        "cka_passed": False,
    },
    {
        "code": "C002",
        "name": "Priya Sharma",
        "note": "Appendix D User 2: high risk, CKA passed, can reach P8",
        "date_of_birth": _dt.date(1997, 6, 21),
        "monthly_salary": D("9500"),
        "monthly_commitments": D("1200"),
        "dependants": 0,
        "investment_objective": "aggressive_growth",
        "investment_horizon_yrs": 12,
        "loss_tolerance": "high",
        "liquidity_needs": "low",
        "income_stability": "stable",
        "investment_experience": "moderate",
        "cka_passed": True,
    },
    {
        "code": "C003",
        "name": "Marcus Chen",
        "note": "Appendix D User 3: can lose up to 10%, capped at P3",
        "date_of_birth": _dt.date(1991, 9, 4),
        "monthly_salary": D("7000"),
        "monthly_commitments": D("1900"),
        "dependants": 1,
        "investment_objective": "growth",
        "investment_horizon_yrs": 7,
        "loss_tolerance": "low",
        "liquidity_needs": "medium",
        "income_stability": "stable",
        "investment_experience": "moderate",
        "cka_passed": True,
    },
    {
        "code": "C004",
        "name": "Siti Nurhaliza",
        "note": "Appendix D User 4: inconsistent answers — clarifying questions, not a category",
        "date_of_birth": _dt.date(1984, 11, 18),
        "monthly_salary": D("5500"),
        "monthly_commitments": D("2900"),
        "dependants": 2,
        "investment_objective": "aggressive_growth",
        "investment_horizon_yrs": 0,
        "loss_tolerance": "high",
        "liquidity_needs": "high",
        "income_stability": "variable",
        "investment_experience": "none",
        "cka_passed": False,
    },
    {
        "code": "C005",
        "name": "Robert Teo",
        "note": "Appendix D User 5: older, no CKA, income-oriented",
        "date_of_birth": _dt.date(1959, 1, 30),
        "monthly_salary": D("2000"),
        "monthly_commitments": D("150"),
        "dependants": 0,
        "investment_objective": "income",
        "investment_horizon_yrs": 2,
        "loss_tolerance": "low",
        "liquidity_needs": "medium",
        "income_stability": "variable",
        "investment_experience": "limited",
        "cka_passed": False,
    },
]
