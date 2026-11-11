"""Client profile, documents, CKA and eligibility (A.1, A.3, C.1).

Every route is `me`, resolved from the authenticated principal. There is no path
client id anywhere in this router.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import pathlib
from decimal import Decimal

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import client_repo, market_repo, portfolio_repo, session_date
from app.api.dto.common import (
    Dto,
    HoldingRowDto,
    InstrumentPositionDto,
    Money,
    OkDto,
    OptMoney,
    PerformancePointDto,
    epoch_ms,
)
from app.api.dto.requests import ProfileUpdateRequest
from app.api.views import asset_class_rows, instrument_positions, performance_series
from app.audit import service as audit
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.db.repositories.client import ClientRepository
from app.db.repositories.market import MarketRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.db.session import get_connection
from app.domain.profile.validation import coherence_check
from app.domain.risk.scorer import ScoringInputs, score
from app.domain.suitability.engine import evaluate
from app.domain.trading.arithmetic import drift
from app.domain.trading.valuation import value_position

PRICE_FILE = "/api/market/prices"
PRICE_FILE_LABEL = "Appendix C prices (JSON price service)"

router = APIRouter(tags=["client"])

UPLOAD_ROOT = pathlib.Path("data/uploads")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_TYPES = {"application/pdf", "image/png", "image/jpeg"}


class ProfileDto(Dto):
    clientId: str
    clientCode: str
    fullName: str
    dateOfBirth: _dt.date | None = None
    email: str | None = None
    monthlySalary: OptMoney = None
    monthlyCommitments: OptMoney = None
    dependants: int | None = None
    investmentObjective: str | None = None
    investmentHorizonYrs: int | None = None
    lossTolerance: str | None = None
    liquidityNeeds: str | None = None
    incomeStability: str | None = None
    investmentExperience: str | None = None
    ckaPassed: bool
    missingFields: list[str]
    inconsistencies: list[dict]


class HoldingsDto(Dto):
    sessionDate: _dt.date
    portfolioCode: str | None = None
    portfolioName: str | None = None
    cash: Money
    totalValue: Money
    byAssetClass: list[HoldingRowDto]
    byInstrument: list[InstrumentPositionDto]
    priceFile: str = PRICE_FILE
    priceFileLabel: str = PRICE_FILE_LABEL
    maxAbsDriftPct: Money | None = None


class EligibilityDto(Dto):
    riskCategory: str | None = None
    eligiblePortfolios: list[str]
    maxPortfolio: str | None = None
    satelliteCapPct: Money
    policyVersion: str
    firedRules: list[dict]
    ckaPassed: bool
    missingFields: list[str] = []
    inconsistencies: list[dict] = []


def _inconsistencies(profile) -> list[dict]:
    return [
        {"code": i.code, "fields": list(i.fields), "question": i.question}
        for i in coherence_check(profile)
    ]


async def _eligibility_dto(repo: ClientRepository, profile=None) -> EligibilityDto:
    """Appendix B: the numeric score is stored, never returned on the wire."""
    profile = profile or await repo.profile()
    assessment = await repo.latest_risk_assessment()
    if assessment is None:
        from app.domain.suitability.rules import load_policy

        return EligibilityDto(
            eligiblePortfolios=[],
            satelliteCapPct=Decimal(0),
            policyVersion=load_policy().version,
            firedRules=[],
            ckaPassed=profile.cka_passed,
            missingFields=profile.missing_fields(),
            inconsistencies=_inconsistencies(profile),
        )

    result = evaluate(profile=profile, risk_category=assessment.category)
    return EligibilityDto(
        riskCategory=assessment.category,
        eligiblePortfolios=list(result.eligible_portfolios),
        maxPortfolio=result.max_portfolio,
        satelliteCapPct=result.satellite_cap_pct,
        policyVersion=result.policy_version,
        firedRules=[
            {"code": r.code, "kind": r.kind, "cap": r.cap, "explanation": r.explanation}
            for r in result.fired_rules
        ],
        ckaPassed=profile.cka_passed,
        missingFields=profile.missing_fields(),
        inconsistencies=_inconsistencies(profile),
    )


def _profile_dto(profile) -> ProfileDto:
    return ProfileDto(
        clientId=str(profile.client_id),
        clientCode=profile.client_code,
        fullName=profile.full_name,
        dateOfBirth=profile.date_of_birth,
        email=profile.email,
        monthlySalary=profile.monthly_salary,
        monthlyCommitments=profile.monthly_commitments,
        dependants=profile.dependants,
        investmentObjective=profile.investment_objective,
        investmentHorizonYrs=profile.investment_horizon_yrs,
        lossTolerance=profile.loss_tolerance,
        liquidityNeeds=profile.liquidity_needs,
        incomeStability=profile.income_stability,
        investmentExperience=profile.investment_experience,
        ckaPassed=profile.cka_passed,
        missingFields=profile.missing_fields(),
        inconsistencies=_inconsistencies(profile),
    )


@router.get("/clients/me/profile", response_model=ProfileDto)
async def get_profile(repo: ClientRepository = Depends(client_repo)) -> ProfileDto:
    return _profile_dto(await repo.profile())


@router.put("/clients/me/profile", response_model=ProfileDto)
async def update_profile(
    body: ProfileUpdateRequest, repo: ClientRepository = Depends(client_repo)
) -> ProfileDto:
    changes = body.model_dump(exclude_none=True)
    return _profile_dto(await repo.update_profile(changes))


@router.post("/clients/me/documents", response_model=OkDto)
async def upload_document(
    file: UploadFile = File(...), repo: ClientRepository = Depends(client_repo)
) -> OkDto:
    """A.1(iv): stored against the client record.

    A PDF is read once here. Instruction-like lines, including encoded or
    translated ones, are dropped. The remaining financial lines may later be
    attached to a prompt as data. PNG and JPEG files are stored only; there is
    no OCR.
    """
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise ValidationError(f"Unsupported file type. Accepted: {sorted(ALLOWED_UPLOAD_TYPES)}.")
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValidationError("That file is larger than the 10 MB limit.")

    digest = hashlib.sha256(payload).hexdigest()[:16]
    safe_name = pathlib.Path(file.filename or "upload").name
    destination = UPLOAD_ROOT / digest[:2] / f"{digest}-{safe_name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)

    from app.orchestration.untrusted_text import screen_upload, write_screened

    screened = screen_upload(destination, file.content_type)
    write_screened(destination, screened)
    document_id = await repo.add_document(
        storage_path=str(destination),
        filename=safe_name,
        mime_type=file.content_type,
        size_bytes=len(payload),
    )
    return OkDto(
        detail="Document stored.",
        data={
            "documentId": str(document_id),
            "instructionsRemoved": screened["removed"],
            "textExtracted": bool(screened["kept"]),
        },
    )


@router.get("/clients/me/documents", response_model=list[dict])
async def list_documents(repo: ClientRepository = Depends(client_repo)) -> list[dict]:
    return [
        {
            "id": str(d["id"]),
            "filename": d["filename"],
            "mimeType": d["mime_type"],
            "sizeBytes": d["size_bytes"],
            "uploadedAt": d["uploaded_at"],
        }
        for d in await repo.documents()
    ]


@router.post("/clients/me/cka", response_model=EligibilityDto)
async def pass_cka(repo: ClientRepository = Depends(client_repo)) -> EligibilityDto:
    """A.3(iii): passing the knowledge check re-opens the gated portfolios."""
    await repo.pass_cka()
    return await _eligibility_dto(repo)


@router.post("/clients/me/reassess", response_model=EligibilityDto)
async def reassess(
    repo: ClientRepository = Depends(client_repo),
    conn: AsyncConnection = Depends(get_connection),
) -> EligibilityDto:
    """Dashboard drivers: re-score this login when the stored profile is coherent."""
    profile = await repo.profile()
    if profile.missing_fields() or coherence_check(profile):
        return await _eligibility_dto(repo, profile)

    inputs = ScoringInputs(
        monthly_salary=profile.monthly_salary,
        monthly_commitments=profile.monthly_commitments,
        dependants=profile.dependants,
        investment_objective=profile.investment_objective,
        investment_horizon_yrs=profile.investment_horizon_yrs,
        loss_tolerance=profile.loss_tolerance,
        liquidity_needs=profile.liquidity_needs,
        income_stability=profile.income_stability,
        investment_experience=profile.investment_experience,
    )
    result = score(inputs)
    await repo.record_risk_assessment(
        score=result.score,
        category=result.category,
        inputs_snapshot=inputs.as_snapshot(),
        formula_version=result.formula_version,
    )
    suitability = evaluate(profile=profile, risk_category=result.category)
    await audit.record_decision(
        conn,
        client_id=profile.client_id,
        decision_type="risk_assessment",
        status="completed",
        input="Dashboard reassess from stored profile drivers",
        output=(
            f"Assessed category is {result.category.replace('_', ' ')}. "
            f"Highest eligible portfolio is {suitability.max_portfolio}."
        ),
        policy_version=suitability.policy_version,
    )
    return await _eligibility_dto(repo, profile)


@router.get("/clients/me/eligible-portfolios", response_model=EligibilityDto)
async def get_eligibility(repo: ClientRepository = Depends(client_repo)) -> EligibilityDto:
    return await _eligibility_dto(repo)


@router.get("/clients/me/conversations")
async def list_conversations(repo: ClientRepository = Depends(client_repo)) -> list[dict]:
    """A.1(4): threads belonging to this login only."""
    return [
        {
            "id": str(row["id"]),
            "kind": row["kind"],
            "createdAt": epoch_ms(row["created_at"]),
            "updatedAt": epoch_ms(row["updated_at"]),
            "messageCount": int(row["message_count"] or 0),
            "lastMessage": row["last_message"] or "",
        }
        for row in await repo.list_conversations()
    ]


@router.get("/clients/me/holdings", response_model=HoldingsDto)
async def get_holdings(
    when: _dt.date = Depends(session_date),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> HoldingsDto:
    """C.1 / A.5(iv). Purely deterministic -- works with the LLM unreachable."""
    valuation = await value_position(portfolios, market, when)
    instruments = await market.instruments()
    portfolio = valuation["portfolio"]
    gaps = drift(valuation["actual_weights"], valuation["target_weights"]) if portfolio else {}
    max_gap = max((abs(v) for v in gaps.values()), default=None)

    return HoldingsDto(
        sessionDate=when,
        portfolioCode=portfolio.code if portfolio else None,
        portfolioName=portfolio.name if portfolio else None,
        cash=valuation["snapshot"].cash,
        totalValue=valuation["total_value"],
        byAssetClass=asset_class_rows(valuation, instruments),
        byInstrument=instrument_positions(valuation, instruments),
        priceFile=get_settings().price_json_path,
        priceFileLabel=get_settings().price_source_label,
        maxAbsDriftPct=max_gap,
    )


@router.get("/clients/me/performance", response_model=list[PerformancePointDto])
async def get_performance(
    days: int = 90,
    when: _dt.date = Depends(session_date),
    portfolios: PortfolioRepository = Depends(portfolio_repo),
    market: MarketRepository = Depends(market_repo),
) -> list[PerformancePointDto]:
    snapshot = await portfolios.latest_snapshot(get_settings().starting_cash)
    return await performance_series(market, snapshot, when, min(max(days, 1), 365))
