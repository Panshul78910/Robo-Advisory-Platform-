from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Req(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LoginRequest(Req):
    email: str
    password: str


class RegisterRequest(Req):
    """A.1(1): create a client account, then the user completes the profile form."""

    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    full_name: str = Field(alias="fullName", min_length=2, max_length=120)


class SessionDateRequest(Req):
    """A.5: the simulated date all pricing resolves against."""

    session_date: _dt.date = Field(alias="sessionDate")


class ProfileUpdateRequest(Req):
    full_name: str | None = Field(default=None, alias="fullName")
    date_of_birth: _dt.date | None = Field(default=None, alias="dateOfBirth")
    email: str | None = None
    monthly_salary: Decimal | None = Field(default=None, alias="monthlySalary", ge=0)
    monthly_commitments: Decimal | None = Field(default=None, alias="monthlyCommitments", ge=0)
    dependants: int | None = Field(default=None, ge=0, le=20)
    investment_objective: str | None = Field(default=None, alias="investmentObjective")
    investment_horizon_yrs: int | None = Field(
        default=None, alias="investmentHorizonYrs", ge=0, le=60
    )
    loss_tolerance: str | None = Field(default=None, alias="lossTolerance")
    liquidity_needs: str | None = Field(default=None, alias="liquidityNeeds")
    income_stability: str | None = Field(default=None, alias="incomeStability")
    investment_experience: str | None = Field(default=None, alias="investmentExperience")


class InvestRequest(Req):
    portfolio_code: str = Field(alias="portfolioCode", pattern=r"^P[1-8]$")


class RebalanceRequest(Req):
    """C.1: execute only the trade set the client just previewed."""

    preview_token: str = Field(alias="previewToken", min_length=16)


class SwitchRequest(Req):
    portfolio_code: str = Field(alias="portfolioCode", pattern=r"^P[1-8]$")
    preview_token: str = Field(alias="previewToken", min_length=16)


class ChatRequest(Req):
    # 20_000 is only the parser ceiling. The configured cap (default 8_000) is
    # enforced below with a message the client can show.
    message: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = Field(default=None, alias="conversationId")

    @field_validator("message")
    @classmethod
    def _within_configured_limit(cls, value: str) -> str:
        from app.core.config import get_settings

        limit = get_settings().max_user_message_chars
        if len(value) > limit:
            raise ValueError(
                f"That message is too long to process ({len(value)} characters). "
                f"Please shorten it to {limit} characters or fewer and try again."
            )
        return value


class ProfilingMessageRequest(Req):
    message: str = Field(min_length=1, max_length=4000)


class ReviewRequest(Req):
    """C.5: the human decision that supersedes the AI's proposal."""

    action: str = Field(pattern=r"^(approve|edit|reject)$")
    final_output: str | None = Field(default=None, alias="finalOutput")
    reason: str | None = None


class RagQueryRequest(Req):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, alias="topK", ge=1, le=50)
    audience: str | None = None


class RagIngestRequest(Req):
    source: str
    corpus_label: str = Field(default="corpus", alias="corpusLabel")
    audience: str = Field(default="client", pattern=r"^(client|admin)$")
    reset: bool = False
