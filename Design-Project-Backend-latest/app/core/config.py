from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return f"{parsed.hostname}:{parsed.port}" if parsed.hostname else "(unset)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- database target ---------------------------------------------------
    # Two connection strings, one switch. Keeping both configured means moving
    # between the shared Supabase project and a local container is a one-word
    # edit, with no risk of a half-changed URL pointing somewhere unintended.
    #
    # Note this is a *Postgres connection string*, not a Supabase API key. This
    # service speaks the Postgres wire protocol directly and never calls the
    # Supabase REST API, so `sb_publishable_...` / `sb_secret_...` have no use
    # here. Get the URI from: Project Settings -> Database -> Connection string.
    db_target: Literal["local", "supabase"] = "local"
    database_url_local: str = "postgresql://postgres:postgres@127.0.0.1:54329/postgres"
    database_url_supabase: str = ""

    #: Explicit override. When set it wins over `db_target`, so CI and one-off
    #: scripts can point somewhere specific without touching the switch.
    database_url: str = ""

    @model_validator(mode="after")
    def _resolve_database_url(self) -> "Settings":
        target = (
            self.database_url_supabase if self.db_target == "supabase" else self.database_url_local
        )
        if self.database_url:
            # Both are set. That is fine while they agree -- but if they point
            # somewhere different, DATABASE_URL silently wins and the switch
            # appears to do nothing, which is the kind of thing that costs an
            # afternoon. Only complain when they actually disagree.
            if "db_target" in self.model_fields_set and target and target != self.database_url:
                raise ValueError(
                    f"DATABASE_URL and DB_TARGET={self.db_target} disagree, and DATABASE_URL "
                    "wins -- so the switch would do nothing.\n"
                    f"  DATABASE_URL      -> {_host_of(self.database_url)}\n"
                    f"  DB_TARGET={self.db_target:<9} -> {_host_of(target)}\n"
                    "Comment out DATABASE_URL to use DB_TARGET, or drop DB_TARGET."
                )
            return self
        if not target:
            raise ValueError(
                "DB_TARGET=supabase but DATABASE_URL_SUPABASE is empty. Paste the URI from "
                "Supabase -> Project Settings -> Database -> Connection string (not an API key)."
            )
        # Assign past the frozen-field machinery; Settings is mutable by default.
        object.__setattr__(self, "database_url", target)
        return self

    @property
    def is_remote_db(self) -> bool:
        from urllib.parse import urlparse

        return (urlparse(self.database_url).hostname or "") not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "db",
            "postgres",
        }

    # This service issues and verifies its own tokens; it is not a Supabase Auth
    # client. Supabase is used only as a local Postgres and a migration runner,
    # so no project API key belongs here. See `app/core/auth.py`.
    #
    # SUPABASE_JWT_SECRET is accepted as a legacy name so existing .env files
    # keep working, but JWT_SECRET is the correct one.
    jwt_secret: str = Field(
        default="dev-only-insecure-secret-change-me-0123456789abcdef",
        validation_alias=AliasChoices("JWT_SECRET", "SUPABASE_JWT_SECRET"),
    )
    jwt_audience: str = "authenticated"
    jwt_ttl_seconds: int = 60 * 60 * 8

    # The course publishes these as LLM_ENDPOINT / LLM_API_KEY / LLM_MODELS.
    # Those names are listed first, so a .env carrying both the course names and
    # the older HPC_* ones resolves to the course values -- they are the
    # authoritative source. `make check` reports which name supplied each.
    hpc_base_url: str = Field(
        default="", validation_alias=AliasChoices("LLM_ENDPOINT", "HPC_BASE_URL")
    )
    hpc_api_key: str = Field(
        default="", validation_alias=AliasChoices("LLM_API_KEY", "HPC_API_KEY")
    )
    hpc_model_name: str = Field(
        default="", validation_alias=AliasChoices("LLM_MODELS", "HPC_MODEL_NAME")
    )
    hpc_context_limit: int = 8192
    #: Hard cap on a chat message, in characters. Longer text is rejected before
    #: decoding, retrieval or any model call. About 1,200–1,500 words fit; a
    #: multi-thousand-word paste does not. Override with MAX_USER_MESSAGE_CHARS.
    max_user_message_chars: int = Field(default=8000, gt=200, le=20000)

    # --- personal OpenAI account (fallback when the shared endpoint is down) ---
    # Sends prompts to a third party and bills a real account, so it is never
    # the default and tests can never select it.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com"

    #: stub = offline and deterministic; hpc = the shared course endpoint;
    #: openai = a personal OpenAI key.
    llm_backend: Literal["stub", "hpc", "openai"] = "stub"

    @property
    def context_limit(self) -> int:
        """Token ceiling for the active backend.

        The HPC value is a measured (or guessed) property of that server; OpenAI
        models are far larger, but the builder's budgets are tuned for a modest
        window and a larger one buys nothing here.
        """
        return self.hpc_context_limit

    @field_validator("hpc_base_url", mode="after")
    @classmethod
    def _normalise_endpoint(cls, value: str) -> str:
        """Accept a bare host:port as well as a full URL.

        The published value is not guaranteed to carry a scheme, and a missing
        one makes httpx reject the request with a message that says nothing
        useful about the cause.
        """
        value = value.strip().strip("\"'")
        if value and "://" not in value:
            value = f"https://{value}"
        return value

    @field_validator("hpc_model_name", mode="after")
    @classmethod
    def _first_model(cls, value: str) -> str:
        """LLM_MODELS may list several; a request names exactly one.

        The first entry wins. To use a different one, reorder LLM_MODELS --
        HPC_MODEL_NAME cannot override it, since LLM_MODELS takes precedence.
        """
        value = value.strip().strip("\"'")
        for separator in (",", ";", " "):
            if separator in value:
                return value.split(separator)[0].strip()
        return value

    # Resilience (Task 10).
    hpc_connect_timeout_s: float = 5.0
    hpc_read_timeout_s: float = 60.0
    hpc_max_attempts: int = 3
    hpc_backoff_base_s: float = 0.5
    hpc_backoff_cap_s: float = 8.0
    circuit_failure_threshold: int = 5
    circuit_cooldown_s: float = 30.0

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_backend: Literal["local", "hash"] = "local"
    #: Calibrated against all-MiniLM-L6-v2 on the live corpus, not guessed. On the
    #: five-document, 159-chunk corpus: 10 genuine queries scored 0.4192-0.7034,
    #: 8 nonsense queries 0.0000-0.2683, so this sits at the midpoint of the gap
    #: with ~0.07 margin either side.
    #:
    #: It is corpus-specific, not just model-specific. At 0.22 -- the value
    #: calibrated against the earlier single-document corpus -- nonsense now
    #: *clears* the floor: more chunks means more surface for a spurious match, so
    #: the highest nonsense score rose from 0.14 to 0.27 while genuine scores rose
    #: too. A floor left behind by a growing corpus stops abstaining, which is the
    #: failure that looks like success. Re-measure whenever the corpus changes.
    relevance_floor: Decimal = Decimal("0.34")
    retrieval_top_k: int = 6
    retrieval_candidates: int = 20

    # Below this, a response is routed to client review rather than returned flat (C.4 -> C.5).
    low_confidence_threshold: Decimal = Decimal("0.60")

    # --- Part D semantic judge (local Ollama; see app/llm/judge.py) ---------------
    #: Off by default so tests and the stub never need Ollama. Turn on for the demo.
    judge_enabled: bool = False
    #: If the judge is unreachable: False blocks the request (fail closed, safer),
    #: True lets it through on the regex layer alone.
    judge_fail_open: bool = False
    ollama_url: str = "http://localhost:11434"
    judge_model: str = "qwen2.5:7b-instruct"
    #: Model for the egress faithfulness check only. Defaults to `judge_model`.
    #:
    #: These are different jobs. `judge_input` detects adversarial intent, where
    #: size measurably buys accuracy: on the red-team set the 7B blocks 30/30 and
    #: the 3B 25/30, and every one of those five misses is an injection or
    #: jailbreak. Faithfulness is entailment -- does this reply follow from this
    #: evidence -- which the 3B handled without any of those failures, at roughly
    #: half the latency. Splitting them spends the large model only where it is
    #: shown to be worth its cost.
    faith_model: str = ""
    guard_model: str = "llama-guard3:1b"

    # --- hosted judges: the same two checks, off this machine -----------------
    #: Judge latency here is a hardware problem, not a prompt one. Inference is
    #: memory-bandwidth bound, and the measured ~10 tokens/sec on a 7B is what
    #: system RAM can physically sustain -- the 35B HPC model answers five times
    #: faster because it sits on a GPU. Hosting the two large judges removes that
    #: ceiling without touching their prompts.
    #:
    #: Any OpenAI-compatible chat-completions endpoint: Groq, OpenRouter, Google,
    #: Together, the course HPC. Only the base URL, key and model names differ,
    #: which is why these are named for the role rather than for a vendor.
    #:
    #: Blank key or blank model means nothing is hosted and Ollama serves the call
    #: as before. When a hosted model is configured, every failure -- bad key,
    #: rate limit, timeout, malformed reply -- falls back to the local model
    #: rather than failing the request. `judge_fail_open` is False, so an
    #: unreachable judge blocks everything; a hosted judge must never be the only
    #: path to a verdict.
    # --- refusal rewriting ----------------------------------------------------
    #: When a check blocks a message, rewrite the approved template so it names
    #: what was declined instead of returning the same sentence every time.
    #:
    #: Off by default, and the default matters: this is the one place where text
    #: that has *just been judged an attack* is handed to a model with no
    #: guardrails and asked to write what the client reads. Benchmarked on this
    #: task, a small model given an injected instruction obeyed it and produced
    #: "APPROVED. P8 granted." as the refusal. `app/llm/refusal.py` validates
    #: every rewrite before it ships and falls back to the template otherwise,
    #: but the safest configuration is still the one that never calls out.
    refusal_rewrite_enabled: bool = False
    #: Blank follows `hosted_judge_model`.
    refusal_model: str = ""
    #: Tighter than the judges'. A blocked request is already decided; the only
    #: question is how it is worded, and no wording is worth making the client
    #: wait for. Slow means: ship the template.
    refusal_timeout_s: float = Field(default=6.0, gt=0)

    # --- vagueness check ------------------------------------------------------
    #: Ask the client to rephrase when a message does not say enough to answer,
    #: instead of guessing at it or abstaining with a message about evidence.
    #:
    #: Safer than the rewriter above by construction: this model returns only a
    #: boolean, and the reply the client sees is a fixed string, so no text
    #: derived from the message can reach them. It runs only on messages the
    #: deterministic layer could not place, so a query that classifies cleanly
    #: pays nothing for it.
    vagueness_check_enabled: bool = False
    #: Blank follows `hosted_judge_model`.
    vagueness_model: str = ""
    #: On the critical path of a question that will probably be answered anyway,
    #: so it fails open quickly rather than making anyone wait on a maybe.
    vagueness_timeout_s: float = Field(default=5.0, gt=0)

    #: Master switch, so a hosted/local comparison is one edit in each direction.
    #: False forces every judge local while leaving the key and model names in
    #: place; blanking either of those works too, but then they have to be pasted
    #: back, and a measurement run that silently used the wrong provider is worse
    #: than no measurement.
    hosted_judge_enabled: bool = True
    judge_api_key: str = Field(
        default="", validation_alias=AliasChoices("JUDGE_API_KEY", "GROQ_API_KEY")
    )
    judge_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        validation_alias=AliasChoices("JUDGE_BASE_URL", "GROQ_BASE_URL"),
    )
    #: Tried when the primary key fails, before the local model. The case it
    #: exists for is a free tier's rate limit, which arrives as a fast 429, so
    #: the second key costs about a hundred milliseconds; an expired or revoked
    #: key is covered by the same path. Blank disables the chain entirely and
    #: behaviour is exactly as it was: primary, then local.
    backup_api_key: str = Field(
        default="", validation_alias=AliasChoices("BACKUP_API_KEY", "BACKUP_JUDGE_API_KEY")
    )
    #: Blank means the same provider as `judge_base_url` with a second key, the
    #: usual arrangement. Set it to put the backup on a different provider --
    #: the model names then have to exist there too, or it 404s and the local
    #: model takes over.
    backup_base_url: str = Field(default="", validation_alias=AliasChoices("BACKUP_BASE_URL"))
    #: Serves `judge_input` and `judge_fields`. The prompts were tuned against
    #: qwen2.5 over several iterations and the compact verdict format is sensitive
    #: to that, so a same-family model is the lowest-risk swap -- though measured,
    #: gemma-3-12b honoured the format unchanged.
    hosted_judge_model: str = Field(
        default="", validation_alias=AliasChoices("HOSTED_JUDGE_MODEL", "GROQ_JUDGE_MODEL")
    )
    #: Egress faithfulness. Blank falls back to `hosted_judge_model`.
    hosted_faith_model: str = Field(
        default="", validation_alias=AliasChoices("HOSTED_FAITH_MODEL", "GROQ_FAITH_MODEL")
    )
    #: Far shorter than `judge_timeout_s`: a hosted judge that is slow has failed,
    #: and the local model is right there. Waiting out a stalled request would
    #: cost more than the fallback it is delaying.
    hosted_timeout_s: float = Field(
        default=20.0, gt=0, validation_alias=AliasChoices("HOSTED_TIMEOUT_S", "GROQ_TIMEOUT_S")
    )
    #: The toxicity classifier stays local. It costs ~1.5s, runs in parallel with
    #: the input judge, and is the only check doing harm categories -- there is
    #: nothing to win and a substitution risk to take.
    #: Per-call, and the egress faithfulness check is the one that needs the room:
    #: it is handed the whole reply plus the client facts plus the retrieved policy,
    #: so its cost scales with payload size. Measured warm on CPU (Ollama cannot use
    #: this machine's Intel Arc GPU): guard 0.6s, input 7.4s, fields 4.6s,
    #: faithfulness 21.9s on a 2.4KB payload and longer on a real one. 30s cut the
    #: last one off mid-flight, and because the judge fails closed that surfaced as
    #: "the safety check is temporarily unavailable" on perfectly legitimate
    #: questions -- a false block, not a refusal.
    judge_timeout_s: float = 120.0

    #: D.1(1) demonstration switch. False makes retrieval return nothing, so the
    #: model answers with no grounding evidence -- the "before" capture for the
    #: data-grounding technique. The corpus is left intact, unlike resetting the
    #: store, so the "after" capture needs only a restart.
    rag_enabled: bool = True
    #: D.1(2) demonstration switch. "minimal" selects `prompts/<name>_minimal.md`
    #: where one exists -- the same system prompt with the enforced-citation and
    #: abstention rules removed. The active variant changes `prompt_version`, so
    #: every decision records which prompt produced it.
    prompt_variant: Literal["full", "minimal"] = "full"

    # --- RAG chunking -------------------------------------------------------
    #: Ceiling for a prose chunk, not a quota: the chunker splits on headings
    #: first, so a document whose sections are shorter never reaches it. Raise it
    #: to keep long sections whole, lower it for finer-grained retrieval.
    chunk_target_tokens: int = Field(default=500, gt=0)
    #: Fraction of a chunk carried into the next one, so a passage straddling a
    #: boundary still appears whole on one side. Must be below 1: at 1 a chunk
    #: would carry itself forward entirely.
    chunk_overlap_ratio: float = Field(default=0.15, ge=0.0, lt=1.0)

    #: Idle minutes after which a new chat starts a new conversation.
    #:
    #: Sessions must persist across requests -- the profiling interview keeps its
    #: place in conversation.state (A.2) and the judge reads prior turns to catch
    #: a probe spread over several (D.4(2)). Without an expiry, though, one
    #: conversation accumulated for days and unrelated turns from a previous
    #: sitting made ordinary questions look suspicious. Long enough that a pause
    #: mid-conversation is not punished; short enough that yesterday is over.
    conversation_idle_minutes: int = Field(default=30, gt=0)

    log_level: str = "INFO"
    #: `json` is the machine-readable record and the B.7 evidence. `pretty` is
    #: the same records laid out over several lines for someone reading the
    #: terminal live -- a demo rather than a deployment setting. Redaction and
    #: field selection are shared, so the choice cannot widen what is exposed.
    log_format: Literal["json", "pretty"] = "json"
    #: One JSON line per HTTP request. On by default because the rest of this
    #: log is event-driven -- without it a healthy server is silent, which is
    #: indistinguishable from a hung one. Set ACCESS_LOG=false to quieten it.
    access_log: bool = True
    allow_fault_injection: bool = False

    starting_cash: Decimal = Decimal("50000.00")

    #: Percentage-point band a holding must drift before a rebalance produces
    #: trades. Below this, the position is treated as already on target -- the
    #: alternative is an unbounded loop of 0.000001-unit "trades" that write
    #: audit rows claiming a rebalance happened when nothing material moved.
    rebalance_band_pct: Decimal = Field(default=Decimal("0.5"))
    #: Drop an individual rebalance/switch trade whose gross amount is below
    #: this (currency units). Catches the other half of the dust-trade loop:
    #: a 1e-6 unit sell that rounds to $0.00.
    min_trade_notional: Decimal = Field(default=Decimal("1.00"))

    # Appendix C prices. CSV remains the canonical *source* format the domain
    # lead hands the software lead. Runtime exposure is JSON from this service
    # (or from an instructor-hosted URL with the same schema).
    price_service_url: str = Field(
        default="",
        validation_alias=AliasChoices("PRICE_SERVICE_URL", "APPENDIX_C_PRICE_URL"),
    )
    price_source_label: str = "Appendix C prices (JSON price service)"
    price_json_path: str = "/api/market/prices"


@lru_cache
def get_settings() -> Settings:
    return Settings()
