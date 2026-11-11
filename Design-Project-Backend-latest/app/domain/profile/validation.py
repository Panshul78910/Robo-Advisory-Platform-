"""Profile coherence checks (A.2 iv).

When a profile contradicts itself the system must ask rather than score. A model
asked to "just pick one" will happily do so; refusing is a deterministic rule
here so the behaviour is reproducible under red-teaming.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

AGGRESSIVE_OBJECTIVES = frozenset({"growth", "aggressive_growth"})


@dataclass(frozen=True, slots=True)
class Inconsistency:
    code: str
    fields: tuple[str, ...]
    #: Shown to the client as-is; must never hint at weights or thresholds.
    question: str


def coherence_check(profile) -> list[Inconsistency]:
    """Contradictions that block scoring. An empty list means the profile is scorable.

    Accepts anything exposing the profile attributes (the repository dataclass or
    a partially-filled conversation state object).
    """
    g = lambda f: getattr(profile, f, None)  # noqa: E731
    found: list[Inconsistency] = []

    objective = g("investment_objective")
    horizon = g("investment_horizon_yrs")
    tolerance = g("loss_tolerance")
    liquidity = g("liquidity_needs")
    experience = g("investment_experience")
    salary = g("monthly_salary")
    commitments = g("monthly_commitments")

    if tolerance == "zero" and objective in AGGRESSIVE_OBJECTIVES:
        found.append(
            Inconsistency(
                "zero_tolerance_aggressive_objective",
                ("loss_tolerance", "investment_objective"),
                "You have said you are not willing to accept any loss of capital, but also "
                "that your objective is growth. Those pull in opposite directions. Which "
                "matters more to you: protecting the capital, or pursuing the growth?",
            )
        )

    if horizon is not None and horizon < 2 and objective in AGGRESSIVE_OBJECTIVES:
        found.append(
            Inconsistency(
                "short_horizon_aggressive_objective",
                ("investment_horizon_yrs", "investment_objective"),
                "A growth objective over a horizon of under two years leaves little time "
                "to recover from a downturn. Is the horizon firm, or could the money stay "
                "invested for longer?",
            )
        )

    if liquidity == "high" and horizon is not None and horizon >= 10:
        found.append(
            Inconsistency(
                "high_liquidity_long_horizon",
                ("liquidity_needs", "investment_horizon_yrs"),
                "You have indicated you may need quick access to this money, yet also a "
                "horizon of ten years or more. Roughly how much of it might you need at "
                "short notice?",
            )
        )

    if liquidity == "high" and objective in AGGRESSIVE_OBJECTIVES:
        found.append(
            Inconsistency(
                "high_liquidity_aggressive_objective",
                ("liquidity_needs", "investment_objective"),
                "You may need this money at short notice, but you also asked for aggressive "
                "growth. Which is the priority: keeping the funds available, or pursuing "
                "growth?",
            )
        )

    if experience == "none" and objective in AGGRESSIVE_OBJECTIVES:
        found.append(
            Inconsistency(
                "inexperienced_aggressive_objective",
                ("investment_experience", "investment_objective"),
                "You have said you have no investment experience, yet the objective on file "
                "is aggressive growth. Would you like to keep that objective, or start more "
                "conservatively?",
            )
        )

    if salary is not None and commitments is not None:
        if salary <= Decimal(0):
            found.append(
                Inconsistency(
                    "non_positive_salary",
                    ("monthly_salary",),
                    "Could you confirm your monthly income? The figure on file is not usable.",
                )
            )
        elif commitments > salary:
            found.append(
                Inconsistency(
                    "commitments_exceed_salary",
                    ("monthly_salary", "monthly_commitments"),
                    "Your recorded monthly commitments exceed your monthly income. Could you "
                    "confirm both figures?",
                )
            )

    return found
