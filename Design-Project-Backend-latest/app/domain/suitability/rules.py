"""Loading and representation of policy/suitability.yaml.

The policy is data. Conditions are field/op/value triples interpreted by a fixed
comparator table -- never `eval` -- so editing the YAML can change the rules but
cannot introduce code execution.
"""

from __future__ import annotations

import functools
import pathlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import yaml

from app.core.errors import ValidationError

POLICY_PATH = pathlib.Path(__file__).resolve().parents[3] / "policy" / "suitability.yaml"

COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in b,
}


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    op: str
    value: Any

    def matches(self, profile) -> bool:
        actual = getattr(profile, self.field, None)
        if actual is None:
            return False
        if isinstance(actual, Decimal) and not isinstance(self.value, Decimal):
            return COMPARATORS[self.op](actual, Decimal(str(self.value)))
        return COMPARATORS[self.op](actual, self.value)


@dataclass(frozen=True, slots=True)
class Override:
    code: str
    when: Condition
    cap: str
    explanation: str


@dataclass(frozen=True, slots=True)
class CkaGates:
    portfolios: frozenset[str]
    satellite_requires_cka: bool
    non_cka_cap: str
    explanation: str


@dataclass(frozen=True, slots=True)
class SuitabilityPolicy:
    version: str
    ladder: tuple[str, ...]
    eligibility: dict[str, str]
    overrides: tuple[Override, ...]
    cka: CkaGates
    satellite_caps: dict[str, Decimal]

    def rank(self, portfolio_code: str) -> int:
        """Position of a portfolio on the risk ladder. P1 -> 0, P2 -> 1, ..."""
        try:
            return int(portfolio_code.removeprefix("P")) - 1
        except ValueError:
            raise ValidationError(
                detail=f"unrecognised portfolio code {portfolio_code!r}"
            ) from None

    def cap_for_category(self, category: str) -> str:
        try:
            return self.eligibility[category]
        except KeyError:
            raise ValidationError(detail=f"no eligibility entry for {category!r}") from None

    def satellite_cap(self, category: str) -> Decimal:
        return self.satellite_caps.get(category, Decimal(0))


def _parse(raw: dict) -> SuitabilityPolicy:
    for op in {o["when"]["op"] for o in raw.get("overrides", [])}:
        if op not in COMPARATORS:
            raise ValidationError(detail=f"unsupported condition operator {op!r}")
    gates = raw["cka_gates"]
    return SuitabilityPolicy(
        version=raw["version"],
        ladder=tuple(raw["ladder"]),
        eligibility=dict(raw["eligibility"]),
        overrides=tuple(
            Override(
                code=o["code"],
                when=Condition(**o["when"]),
                cap=o["cap"],
                explanation=o["explanation"].strip(),
            )
            for o in raw.get("overrides", [])
        ),
        cka=CkaGates(
            portfolios=frozenset(gates.get("portfolios", ())),
            satellite_requires_cka=bool(gates.get("satellite_requires_cka", True)),
            non_cka_cap=gates["non_cka_cap"],
            explanation=gates["explanation"].strip(),
        ),
        satellite_caps={k: Decimal(str(v)) for k, v in raw.get("satellite_caps", {}).items()},
    )


@functools.lru_cache(maxsize=1)
def load_policy(path: pathlib.Path = POLICY_PATH) -> SuitabilityPolicy:
    return _parse(yaml.safe_load(path.read_text(encoding="utf-8")))
