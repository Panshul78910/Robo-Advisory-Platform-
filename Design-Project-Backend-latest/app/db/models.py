"""SQLAlchemy Core table definitions mirroring supabase/migrations/.

The migrations own the DDL; this module exists only to build parameterised
queries in Python. There is no ORM mapping layer and no `metadata.create_all()`
-- schema changes go in a migration file, never here.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

metadata = sa.MetaData()

#: Enum members, mirroring 001_extensions.sql. SQLAlchemy needs the values to
#: decode a row -- without them it raises `LookupError: not among the defined
#: enum values` on the first read. `tests/unit/test_enum_parity.py` asserts these
#: stay in step with the migration, since a silent divergence would surface only
#: at runtime.
ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "user_role": ("client", "admin"),
    "loss_tolerance": ("zero", "low", "medium", "high"),
    "liquidity_needs": ("low", "medium", "high"),
    "income_stability": ("unstable", "variable", "stable"),
    "investment_exp": ("none", "limited", "moderate", "extensive"),
    "investment_obj": ("preservation", "income", "balanced", "growth", "aggressive_growth"),
    "asset_class": ("cash", "bonds", "equities", "alternatives"),
    "risk_category": (
        "capital_preservation",
        "conservative",
        "moderately_conservative",
        "balanced",
        "moderately_aggressive",
        "growth",
        "aggressive",
        "very_aggressive",
    ),
    "portfolio_event_type": ("invest", "rebalance", "revert", "switch"),
    "decision_type": (
        "risk_assessment",
        "suitability_gate",
        "recommendation",
        "rebalance",
        "revert",
        "chat_qa",
    ),
    "decision_status": (
        "completed",
        "auto_executed",
        "pending_review",
        "abstained",
        "approved",
        "approved_edited",
        "overridden",
        "refused",
    ),
}


def _enum(name: str) -> ENUM:
    # Types are created by 001_extensions.sql; never emit DDL for them here.
    return ENUM(*ENUM_VALUES[name], name=name, create_type=False)


def _uuid_pk() -> sa.Column:
    # `FetchedValue` records that the *database* supplies the id (the migrations
    # declare `default uuid_generate_v4()`), not Python. Without it SQLAlchemy
    # warns on every insert that a primary key has no default generator -- noise
    # that buries real warnings, since the inserts were always working.
    # Deliberately not `server_default=text(...)`: this metadata never emits DDL
    # (see the module docstring), so naming the expression twice could only ever
    # drift from the migration.
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.FetchedValue())


# --- identity ---------------------------------------------------------------
app_user = sa.Table(
    "app_user",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.FetchedValue()),
    sa.Column("role", _enum("user_role"), nullable=False),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column("email", sa.Text, unique=True),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

# Kept apart from app_user so that table holds no secret and admin views can
# select it freely. See 008_local_credentials.sql for why this is ours rather
# than Supabase's auth.users.
app_credential = sa.Table(
    "app_credential",
    metadata,
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), primary_key=True),
    sa.Column("password_hash", sa.Text, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

client = sa.Table(
    "client",
    metadata,
    _uuid_pk(),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), unique=True),
    sa.Column("client_code", sa.Text, unique=True, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

client_profile = sa.Table(
    "client_profile",
    metadata,
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id"), primary_key=True),
    sa.Column("full_name", sa.Text, nullable=False),
    sa.Column("date_of_birth", sa.Date),
    sa.Column("email", sa.Text),
    sa.Column("monthly_salary", sa.Numeric(14, 2)),
    sa.Column("monthly_commitments", sa.Numeric(14, 2)),
    sa.Column("dependants", sa.Integer),
    sa.Column("investment_objective", _enum("investment_obj")),
    sa.Column("investment_horizon_yrs", sa.Integer),
    sa.Column("loss_tolerance", _enum("loss_tolerance")),
    sa.Column("liquidity_needs", _enum("liquidity_needs")),
    sa.Column("income_stability", _enum("income_stability")),
    sa.Column("investment_experience", _enum("investment_exp")),
    sa.Column("cka_passed", sa.Boolean, nullable=False),
    sa.Column("cka_passed_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

client_profile_history = sa.Table(
    "client_profile_history",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("snapshot", JSONB, nullable=False),
    sa.Column("changed_at", sa.DateTime(timezone=True)),
    sa.Column("changed_by", UUID(as_uuid=True)),
)

client_document = sa.Table(
    "client_document",
    metadata,
    _uuid_pk(),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("storage_path", sa.Text, nullable=False),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("mime_type", sa.Text),
    sa.Column("size_bytes", sa.BigInteger),
    sa.Column("uploaded_at", sa.DateTime(timezone=True)),
)

# --- risk and instruments ---------------------------------------------------
risk_assessment = sa.Table(
    "risk_assessment",
    metadata,
    _uuid_pk(),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("score", sa.Numeric(10, 4), nullable=False),
    sa.Column("category", _enum("risk_category"), nullable=False),
    sa.Column("inputs_snapshot", JSONB, nullable=False),
    sa.Column("formula_version", sa.Text, nullable=False),
    sa.Column("conversation_id", UUID(as_uuid=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

instrument = sa.Table(
    "instrument",
    metadata,
    _uuid_pk(),
    sa.Column("symbol", sa.Text, unique=True, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("asset_class", _enum("asset_class"), nullable=False),
    sa.Column("is_satellite", sa.Boolean, nullable=False),
)

model_portfolio = sa.Table(
    "model_portfolio",
    metadata,
    _uuid_pk(),
    sa.Column("code", sa.Text, unique=True, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("risk_category", _enum("risk_category"), nullable=False),
    sa.Column("requires_cka", sa.Boolean, nullable=False),
)

model_portfolio_target = sa.Table(
    "model_portfolio_target",
    metadata,
    sa.Column(
        "portfolio_id", UUID(as_uuid=True), sa.ForeignKey("model_portfolio.id"), primary_key=True
    ),
    sa.Column(
        "instrument_id", UUID(as_uuid=True), sa.ForeignKey("instrument.id"), primary_key=True
    ),
    sa.Column("target_weight", sa.Numeric(7, 4), nullable=False),
)

instrument_price = sa.Table(
    "instrument_price",
    metadata,
    sa.Column(
        "instrument_id", UUID(as_uuid=True), sa.ForeignKey("instrument.id"), primary_key=True
    ),
    sa.Column("price_date", sa.Date, primary_key=True),
    sa.Column("price", sa.Numeric(18, 6), nullable=False),
)

# --- portfolio --------------------------------------------------------------
portfolio_event = sa.Table(
    "portfolio_event",
    metadata,
    _uuid_pk(),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("seq", sa.BigInteger, nullable=False),
    sa.Column("event_type", _enum("portfolio_event_type"), nullable=False),
    sa.Column("session_date", sa.Date, nullable=False),
    sa.Column("portfolio_id", UUID(as_uuid=True)),
    sa.Column("reverts_event_id", UUID(as_uuid=True)),
    sa.Column("decision_id", UUID(as_uuid=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

trade = sa.Table(
    "trade",
    metadata,
    _uuid_pk(),
    sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("portfolio_event.id")),
    sa.Column("instrument_id", UUID(as_uuid=True), sa.ForeignKey("instrument.id")),
    sa.Column("side", sa.Text, nullable=False),
    sa.Column("units", sa.Numeric(20, 6), nullable=False),
    sa.Column("price", sa.Numeric(18, 6), nullable=False),
    sa.Column("gross_amount", sa.Numeric(18, 2), nullable=False),
)

holdings_snapshot = sa.Table(
    "holdings_snapshot",
    metadata,
    _uuid_pk(),
    sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("portfolio_event.id"), unique=True),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("seq", sa.BigInteger, nullable=False),
    sa.Column("cash", sa.Numeric(18, 2), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

holdings_snapshot_line = sa.Table(
    "holdings_snapshot_line",
    metadata,
    sa.Column(
        "snapshot_id", UUID(as_uuid=True), sa.ForeignKey("holdings_snapshot.id"), primary_key=True
    ),
    sa.Column(
        "instrument_id", UUID(as_uuid=True), sa.ForeignKey("instrument.id"), primary_key=True
    ),
    sa.Column("units", sa.Numeric(20, 6), nullable=False),
)

idempotency_key = sa.Table(
    "idempotency_key",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("endpoint", sa.Text, nullable=False),
    sa.Column("request_hash", sa.Text, nullable=False),
    sa.Column("response_json", JSONB),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

# --- audit ------------------------------------------------------------------
decision = sa.Table(
    "decision",
    metadata,
    _uuid_pk(),
    sa.Column("display_no", sa.Text, nullable=False),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("decision_type", _enum("decision_type"), nullable=False),
    sa.Column("status", _enum("decision_status"), nullable=False),
    sa.Column("input", sa.Text),
    sa.Column("output", sa.Text),
    sa.Column("confidence", sa.Numeric(5, 4)),
    sa.Column("abstained", sa.Boolean, nullable=False),
    sa.Column("refused", sa.Boolean, nullable=False),
    sa.Column("model_version", sa.Text),
    sa.Column("prompt_version", sa.Text),
    sa.Column("policy_version", sa.Text),
    sa.Column("session_date", sa.Date),
    sa.Column("correlation_id", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

decision_source = sa.Table(
    "decision_source",
    metadata,
    sa.Column("id", sa.BigInteger, primary_key=True),
    sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("decision.id")),
    sa.Column("chunk_ref", sa.Text, nullable=False),
    sa.Column("doc_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text),
    sa.Column("section", sa.Text),
    sa.Column("page", sa.Integer),
    sa.Column("score", sa.Numeric(8, 5)),
    sa.Column("passage_text", sa.Text, nullable=False),
)

decision_override = sa.Table(
    "decision_override",
    metadata,
    _uuid_pk(),
    sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("decision.id"), unique=True),
    sa.Column("reviewer_user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id")),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("reason", sa.Text),
    sa.Column("pre_override_output", sa.Text, nullable=False),
    sa.Column("final_output", sa.Text, nullable=False),
    sa.Column("reviewed_at", sa.DateTime(timezone=True)),
)

escalation = sa.Table(
    "escalation",
    metadata,
    _uuid_pk(),
    sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("decision.id")),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("reason_code", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("resolved_at", sa.DateTime(timezone=True)),
)

# --- session and rag --------------------------------------------------------
app_session = sa.Table(
    "app_session",
    metadata,
    _uuid_pk(),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("app_user.id")),
    sa.Column("session_date", sa.Date, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

conversation = sa.Table(
    "conversation",
    metadata,
    _uuid_pk(),
    sa.Column("client_id", UUID(as_uuid=True), sa.ForeignKey("client.id")),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("state", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

message = sa.Table(
    "message",
    metadata,
    _uuid_pk(),
    sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversation.id")),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("role", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("decision_id", UUID(as_uuid=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

corpus_version = sa.Table(
    "corpus_version",
    metadata,
    _uuid_pk(),
    sa.Column("label", sa.Text, unique=True, nullable=False),
    sa.Column("note", sa.Text),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

document = sa.Table(
    "document",
    metadata,
    _uuid_pk(),
    sa.Column("corpus_version_id", UUID(as_uuid=True), sa.ForeignKey("corpus_version.id")),
    sa.Column("doc_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("source_path", sa.Text, nullable=False),
    sa.Column("audience", sa.Text, nullable=False),
)

chunk = sa.Table(
    "chunk",
    metadata,
    _uuid_pk(),
    sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("document.id")),
    sa.Column("chunk_ref", sa.Text, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("heading", sa.Text),
    sa.Column("section", sa.Text),
    sa.Column("page", sa.Integer),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("token_count", sa.Integer),
    sa.Column("audience", sa.Text, nullable=False),
    # `embedding vector(384)` and the generated `tsv` column are handled in raw SQL.
)

llm_call = sa.Table(
    "llm_call",
    metadata,
    _uuid_pk(),
    sa.Column("correlation_id", sa.Text, nullable=False),
    sa.Column("decision_id", UUID(as_uuid=True)),
    sa.Column("attempt", sa.Integer, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("block_tokens", JSONB, nullable=False),
    sa.Column("total_tokens", sa.Integer),
    sa.Column("context_limit", sa.Integer),
    sa.Column("response_text", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("latency_ms", sa.Integer),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)
