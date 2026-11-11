"""Response DTOs.

Shapes match what the frontend already consumes, with the spec's deliberate
corrections applied: confidence is null (never 0) when abstained, every valuation
is parameterised by session date, and `decisionId` is a display string over a
UUID primary key rather than an in-memory counter.

This is the only layer permitted to turn a Decimal into a float.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_serializer


def _as_number(value: Decimal | None) -> float | None:
    """The serialisation boundary. Pydantic renders a bare Decimal as a JSON
    *string*, which the frontend would then have to parse -- and silently get
    wrong in arithmetic. Every money, unit, price and weight field uses one of
    the aliases below instead."""
    return float(value) if value is not None else None


#: Use for any Decimal leaving the API. `Money` for required fields,
#: `OptMoney` where null is meaningful (an unscored client has no risk score).
Money = Annotated[Decimal, PlainSerializer(_as_number, return_type=float, when_used="json")]
OptMoney = Annotated[
    Decimal | None, PlainSerializer(_as_number, return_type=float | None, when_used="json")
]


class Dto(BaseModel):
    model_config = ConfigDict(populate_by_name=True, ser_json_inf_nan="null")


def epoch_ms(value: _dt.datetime | None) -> int | None:
    return int(value.timestamp() * 1000) if value else None


class PolicyChunkDto(Dto):
    doc_id: str = Field(serialization_alias="docId")
    title: str
    section: str | None = None
    page: int | None = None
    text: str
    chunk_ref: str | None = Field(default=None, serialization_alias="chunkRef")
    score: Decimal | None = None

    @field_serializer("score")
    def _score(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class HoldingRowDto(Dto):
    """Asset-class view. Positions are stored per instrument; this is an
    aggregation for display only (spec §5)."""

    asset: str
    units: Decimal
    price: Decimal
    value: Decimal
    target_weight: Decimal = Field(serialization_alias="targetWeight")
    actual_weight: Decimal = Field(serialization_alias="actualWeight")

    @field_serializer("units", "price", "value", "target_weight", "actual_weight")
    def _num(self, v: Decimal) -> float:
        return float(v)


class InstrumentPositionDto(Dto):
    instrument_id: str = Field(serialization_alias="instrumentId")
    symbol: str
    name: str
    asset_class: str = Field(serialization_alias="assetClass")
    is_satellite: bool = Field(serialization_alias="isSatellite")
    units: Decimal
    price: Decimal
    value: Decimal
    target_weight: Decimal = Field(serialization_alias="targetWeight")
    actual_weight: Decimal = Field(serialization_alias="actualWeight")

    @field_serializer("units", "price", "value", "target_weight", "actual_weight")
    def _num(self, v: Decimal) -> float:
        return float(v)


class PerformancePointDto(Dto):
    date: str
    value: Decimal

    @field_serializer("value")
    def _num(self, v: Decimal) -> float:
        return float(v)


class ChatAnswerDto(Dto):
    text: str
    #: null when abstained. Never 0 -- that would read as a confident negative.
    confidence: Decimal | None = None
    sources: list[PolicyChunkDto] = Field(default_factory=list)
    abstained: bool = False
    refused_formula: bool = Field(default=False, serialization_alias="refusedFormula")
    decision_id: str | None = Field(default=None, serialization_alias="decisionId")
    confidence_label: str = Field(default="medium", serialization_alias="confidenceLabel")
    cautions: list[str] = Field(default_factory=list)
    ambiguity_options: list[str] = Field(
        default_factory=list, serialization_alias="ambiguityOptions"
    )
    requires_review: bool = Field(default=False, serialization_alias="requiresReview")
    intent: str | None = None
    #: Sent back on the next message so earlier turns count toward multi-turn checks.
    conversation_id: str | None = Field(default=None, serialization_alias="conversationId")

    @field_serializer("confidence")
    def _conf(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class HumanOverrideDto(Dto):
    action: str
    reason: str | None = None
    pre_override_output: str = Field(serialization_alias="preOverrideOutput")
    final_output: str = Field(serialization_alias="finalOutput")
    reviewed_at: int | None = Field(default=None, serialization_alias="reviewedAt")


#: The `decision` row is append-only, so a review cannot change `status` in place.
#: The officer action is the later, authoritative state for the client trail.
_STATUS_AFTER_REVIEW = {
    "approve": "approved",
    "edit": "approved_edited",
    "reject": "overridden",
}


def override_dto(record: dict | None) -> HumanOverrideDto | None:
    if record is None:
        return None
    return HumanOverrideDto(
        action=record["action"],
        reason=record["reason"],
        pre_override_output=record["pre_override_output"],
        final_output=record["final_output"],
        reviewed_at=epoch_ms(record["reviewed_at"]),
    )


def status_after_review(decision_status: str, override: dict | None) -> str:
    if not override:
        return decision_status
    return _STATUS_AFTER_REVIEW.get(str(override.get("action") or ""), decision_status)


class AuditEntryDto(Dto):
    decision_id: str = Field(serialization_alias="decisionId")
    timestamp: int
    client_id: str | None = Field(default=None, serialization_alias="clientId")
    client_name: str | None = Field(default=None, serialization_alias="clientName")
    decision_type: str = Field(serialization_alias="decisionType")
    input: str | None = None
    output: str | None = None
    confidence: Decimal | None = None
    model_version: str | None = Field(default=None, serialization_alias="modelVersion")
    prompt_version: str | None = Field(default=None, serialization_alias="promptVersion")
    policy_version: str | None = Field(default=None, serialization_alias="policyVersion")
    retrieved_sources: list[str] = Field(
        default_factory=list, serialization_alias="retrievedSources"
    )
    sources: list[PolicyChunkDto] = Field(default_factory=list)
    human_override: HumanOverrideDto | None = Field(
        default=None, serialization_alias="humanOverride"
    )
    status: str
    session_date: _dt.date | None = Field(default=None, serialization_alias="sessionDate")
    correlation_id: str | None = Field(default=None, serialization_alias="correlationId")

    @field_serializer("confidence")
    def _conf(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class ErrorDto(Dto):
    code: str
    message: str
    correlation_id: str = Field(serialization_alias="correlationId")


class OkDto(Dto):
    ok: bool = True
    detail: str | None = None
    data: dict[str, Any] | None = None
