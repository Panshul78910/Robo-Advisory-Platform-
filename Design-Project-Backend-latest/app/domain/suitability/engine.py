"""Suitability rules and product gating (A.3).

Deterministic: the model is never consulted about what a client may hold. A
client can argue with the chatbot as long as they like -- eligibility is decided
here, and a request outside the eligible set produces a refusal plus an
escalation record rather than a negotiation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.suitability.rules import SuitabilityPolicy, load_policy


@dataclass(frozen=True, slots=True)
class FiredRule:
    """One rule firing, recorded verbatim on the decision for the audit trail."""

    code: str
    kind: str  # 'category_cap' | 'override' | 'cka_gate'
    cap: str
    explanation: str


@dataclass(frozen=True, slots=True)
class SuitabilityResult:
    eligible_portfolios: tuple[str, ...]
    #: The rule that set the final cap, or None when the category cap alone bound.
    capped_by: FiredRule | None
    fired_rules: tuple[FiredRule, ...]
    satellite_cap_pct: Decimal
    policy_version: str
    max_portfolio: str = field(default="")

    def permits(self, portfolio_code: str) -> bool:
        return portfolio_code in self.eligible_portfolios

    def refusal_reason(self, portfolio_code: str) -> str:
        """Plain-language refusal tied to the specific rule that bound (A.3 iv)."""
        binding = self.capped_by
        head = (
            f"{portfolio_code} sits above the highest portfolio your assessment supports, "
            f"which is {self.max_portfolio}."
        )
        return f"{head} {binding.explanation}" if binding else head


def fired_rules_text(result: SuitabilityResult) -> str:
    """Audit-log wording of every rule that shaped this eligibility (A.3(4))."""
    if not result.fired_rules:
        return "No suitability rules fired."
    lines = ["Triggered suitability rules:"]
    for rule in result.fired_rules:
        lines.append(f"- {rule.code} ({rule.kind}, cap {rule.cap}): {rule.explanation}")
    return "\n".join(lines)


def evaluate(
    *,
    profile,
    risk_category: str,
    policy: SuitabilityPolicy | None = None,
) -> SuitabilityResult:
    """Eligible portfolios for this client, and every rule that shaped the answer.

    `profile` supplies loss_tolerance, investment_horizon_yrs, liquidity_needs,
    income_stability and cka_passed.
    """
    policy = policy or load_policy()
    fired: list[FiredRule] = []

    category_cap = policy.cap_for_category(risk_category)
    fired.append(
        FiredRule(
            code=f"category_cap:{risk_category}",
            kind="category_cap",
            cap=category_cap,
            explanation=(f"Your assessed risk category permits portfolios up to {category_cap}."),
        )
    )

    # Overrides fire independently; the most restrictive cap wins (A.3 ii).
    for override in policy.overrides:
        if override.when.matches(profile):
            fired.append(
                FiredRule(
                    code=override.code,
                    kind="override",
                    cap=override.cap,
                    explanation=override.explanation,
                )
            )

    if not getattr(profile, "cka_passed", False):
        fired.append(
            FiredRule(
                code="cka_not_passed",
                kind="cka_gate",
                cap=policy.cka.non_cka_cap,
                explanation=policy.cka.explanation,
            )
        )

    binding = min(fired, key=lambda r: policy.rank(r.cap))
    max_rank = policy.rank(binding.cap)
    eligible = tuple(f"P{i + 1}" for i in range(max_rank + 1))

    satellite_cap = policy.satellite_cap(risk_category)
    if policy.cka.satellite_requires_cka and not getattr(profile, "cka_passed", False):
        satellite_cap = Decimal(0)

    return SuitabilityResult(
        eligible_portfolios=eligible,
        capped_by=binding if binding.kind != "category_cap" else None,
        fired_rules=tuple(fired),
        satellite_cap_pct=satellite_cap,
        policy_version=policy.version,
        max_portfolio=binding.cap,
    )


def satellite_exposure_pct(weights: dict, satellite_ids: set, /) -> Decimal:
    """Share of a portfolio's target weight sitting in satellite instruments."""
    return sum((w for iid, w in weights.items() if iid in satellite_ids), Decimal(0))


def check_satellite_exposure(
    result: SuitabilityResult, weights: dict, satellite_ids: set
) -> str | None:
    """Reason the portfolio breaches the satellite cap, or None if it is within it (A.3 iii)."""
    exposure = satellite_exposure_pct(weights, satellite_ids)
    if exposure <= result.satellite_cap_pct:
        return None
    if result.satellite_cap_pct == 0:
        return (
            "This portfolio holds satellite ETF exposure, which requires a completed "
            "Customer Knowledge Assessment."
        )
    return (
        f"This portfolio holds {exposure}% in satellite instruments, above the "
        f"{result.satellite_cap_pct}% ceiling for your risk category."
    )
