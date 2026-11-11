"""Confidential scoring constants -- the Appendix B stand-in.

CONTAINS ONLY DATA. No functions, no imports of app code, nothing else.

Invariant 8: this module must never be reachable from `app.orchestration.*` or
`app.llm.*`. An import-linter contract in pyproject.toml fails CI if it is, and
`tests/unit/test_no_formula_in_payload.py` asserts none of these values ever
appear in an assembled prompt payload. The computed score and category may be
disclosed; the way they are produced may not.

Replacing this file with the real Appendix B numbers is the whole migration --
`scorer.py` does not change.
"""

from __future__ import annotations

from decimal import Decimal as D

FORMULA_VERSION = "appendix-b-placeholder-v1"

#: Relative contribution of each factor. Must sum to 1.
FACTOR_WEIGHTS: dict[str, D] = {
    "loss_tolerance": D("0.24"),
    "investment_objective": D("0.20"),
    "investment_horizon": D("0.18"),
    "investment_experience": D("0.12"),
    "liquidity_needs": D("0.10"),
    "income_stability": D("0.08"),
    "surplus_ratio": D("0.05"),
    "dependants": D("0.03"),
}

LOSS_TOLERANCE_POINTS: dict[str, D] = {
    "zero": D("0.00"),
    "low": D("0.30"),
    "medium": D("0.65"),
    "high": D("1.00"),
}

INVESTMENT_OBJECTIVE_POINTS: dict[str, D] = {
    "preservation": D("0.00"),
    "income": D("0.30"),
    "balanced": D("0.55"),
    "growth": D("0.80"),
    "aggressive_growth": D("1.00"),
}

INVESTMENT_EXPERIENCE_POINTS: dict[str, D] = {
    "none": D("0.00"),
    "limited": D("0.35"),
    "moderate": D("0.70"),
    "extensive": D("1.00"),
}

LIQUIDITY_NEEDS_POINTS: dict[str, D] = {
    "high": D("0.00"),
    "medium": D("0.50"),
    "low": D("1.00"),
}

INCOME_STABILITY_POINTS: dict[str, D] = {
    "unstable": D("0.00"),
    "variable": D("0.50"),
    "stable": D("1.00"),
}

#: (exclusive upper bound in years, points). The final entry is the catch-all.
HORIZON_BANDS: tuple[tuple[int | None, D], ...] = (
    (2, D("0.00")),
    (4, D("0.25")),
    (7, D("0.50")),
    (11, D("0.75")),
    (None, D("1.00")),
)

#: (exclusive upper bound on monthly surplus / monthly salary, points).
SURPLUS_RATIO_BANDS: tuple[tuple[D | None, D], ...] = (
    (D("0.10"), D("0.00")),
    (D("0.25"), D("0.35")),
    (D("0.45"), D("0.70")),
    (None, D("1.00")),
)

#: (exclusive upper bound on number of dependants, points).
DEPENDANTS_BANDS: tuple[tuple[int | None, D], ...] = (
    (1, D("1.00")),
    (3, D("0.60")),
    (None, D("0.25")),
)

SCORE_SCALE = D("100")

#: (exclusive upper bound on score, risk category). The final entry is the catch-all.
CATEGORY_BANDS: tuple[tuple[D | None, str], ...] = (
    (D("12.5"), "capital_preservation"),
    (D("25.0"), "conservative"),
    (D("37.5"), "moderately_conservative"),
    (D("50.0"), "balanced"),
    (D("62.5"), "moderately_aggressive"),
    (D("75.0"), "growth"),
    (D("87.5"), "aggressive"),
    (None, "very_aggressive"),
)
