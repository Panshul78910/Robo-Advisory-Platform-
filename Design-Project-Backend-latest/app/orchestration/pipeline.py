"""The request pipeline (B.1).

One fixed stage order, so each stage is unit-testable in isolation and a new
guardrail lands at a known point rather than being retrofitted into scattered
call sites:

  0. welfare check (self-harm wording): fixed reply, no model call, runs first
  1. security and session context      6. dispatch to the LLM
  2. ingress guardrails                7. egress guardrails
  3. classify intent                   8. append the disclosure structurally
  4. gather evidence                   9. persist interaction + decision + sources
  5. assemble the payload             10. return

Stage 8 is application code on purpose. A disclosure the model is merely
instructed to include is a much weaker guarantee than one the application
appends, and A.4(ii) requires it to survive a client asking for it to be removed.
"""

from __future__ import annotations

import logging
import pathlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncConnection

from app.audit import service as audit
from app.core.config import get_settings
from app.core.correlation import get_correlation_id
from app.core.errors import LLMError
from app.core.timing import stage
from app.db.repositories.client import ClientRepository
from app.llm.client import LLMClient
from app.llm.refusal import compose_refusal
from app.llm.vagueness import TOO_VAGUE, is_vague
from app.orchestration import guardrails
from app.orchestration.confidence import Confidence, assess
from app.orchestration.context import BuildInput, ContextBuilder, record_llm_call
from app.orchestration.intent import (
    CAPABILITY_REPLY,
    FORMULA_REPLY,
    Classification,
    Intent,
    classify,
    is_capability_question,
    retrieval_query,
)
from app.orchestration.welfare import (
    WELFARE_REASON_CODE,
    WELFARE_REPLY,
    WELFARE_RULE,
    is_self_harm_message,
)
from app.rag.retrieve import RetrievalResult, Retriever, corpus_topics

log = logging.getLogger("app.orchestration.pipeline")

DISCLOSURE_PATH = pathlib.Path(__file__).resolve().parents[2] / "policy" / "disclosure.md"

#: Intents whose answers constitute advice and therefore always carry the
#: mandatory disclosure block (Appendix G).
DISCLOSURE_INTENTS = frozenset(
    {
        Intent.PORTFOLIO_EXPLANATION,
        Intent.REBALANCE_EXPLANATION,
        Intent.RISK_EXPLANATION,
        Intent.HOLDINGS_QUERY,
        Intent.TRANSACTION_HISTORY,
    }
)

DECISION_TYPE_FOR_INTENT = {
    Intent.RISK_EXPLANATION: "risk_assessment",
    Intent.PORTFOLIO_EXPLANATION: "recommendation",
    Intent.REBALANCE_EXPLANATION: "rebalance",
    Intent.POLICY_QUESTION: "chat_qa",
    Intent.FORMULA_REQUEST: "chat_qa",
    Intent.OUT_OF_SCOPE: "chat_qa",
    Intent.HOLDINGS_QUERY: "chat_qa",
    Intent.TRANSACTION_HISTORY: "chat_qa",
    Intent.GENERAL: "chat_qa",
    Intent.PROFILING: "risk_assessment",
}


def disclosure_text() -> str:
    return DISCLOSURE_PATH.read_text(encoding="utf-8").strip()


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    confidence: Confidence
    retrieval: RetrievalResult
    intent: Intent
    decision_id: uuid.UUID
    decision_no: str
    model_version: str
    disclosure_applied: bool
    requires_review: bool
    refused_formula: bool = False
    guardrails_triggered: tuple[str, ...] = field(default=())

    def to_api(self) -> dict:
        """Field names for `ChatAnswerDto`, which owns the camelCase wire format.

        Snake_case deliberately. The DTO's camelCase aliases are
        *serialization* aliases, which do not apply on input -- so camelCase
        keys here were silently discarded and every optional field fell back to
        its default. `decisionId` arrived as null and `confidenceLabel` as the
        literal default, which is how C.4's confidence banding and C.5's review
        routing quietly stopped reaching the client.
        """
        return {
            "text": self.text,
            "confidence": float(self.confidence.value)
            if self.confidence.value is not None
            else None,
            "sources": [c.to_citation() for c in self.retrieval.chunks],
            "abstained": self.confidence.abstained,
            "refused_formula": self.refused_formula,
            "decision_id": self.decision_no,
            "confidence_label": self.confidence.label,
            "cautions": list(self.confidence.cautions),
            "ambiguity_options": list(self.confidence.ambiguity_options),
            "requires_review": self.requires_review,
            "intent": self.intent.value,
        }


class ChatPipeline:
    def __init__(
        self,
        conn: AsyncConnection,
        *,
        llm: LLMClient,
        clients: ClientRepository,
        client_id: uuid.UUID,
        session_date=None,
    ):
        self._conn = conn
        self._llm = llm
        self._clients = clients
        self._client_id = client_id
        self._session_date = session_date
        self._settings = get_settings()
        self._builder = ContextBuilder()

    async def run(
        self,
        message: str,
        *,
        client_facts: dict,
        missing_profile_fields: tuple[str, ...] = (),
        route_intent: Intent | None = None,
        conversation_id: uuid.UUID | None = None,
        policy_version: str | None = None,
        client_email: str = "",
    ) -> ChatResult:
        # 0. Welfare check. First, before the input limit, the glossary check,
        #    evasion, ingress and the judge: fixed text, no model call, and
        #    logged for the officer as a welfare concern rather than a block.
        if is_self_harm_message(message):
            return await self._welfare_response(message, conversation_id)

        # A.2(5): formula requests get a qualitative refusal before Part D
        # ingress, which would otherwise treat "weights" / "formula" as a probe.
        # A known attack phrase in another language is refused first. Otherwise
        # "Apakah formula pemarkahan risiko?" matches the word "formula" and
        # skips the language check entirely.
        with stage("input.limit"):
            from app.orchestration.input_budget import condense_for_model, ensure_within_limit

            ensure_within_limit(message)
            model_message = condense_for_model(message)
        if guardrails.is_attack_glossary_question(message):
            return await self._fixed_answer(
                message,
                Classification(Intent.GENERAL, confident=True),
                conversation_id,
                guardrails.R_GLOSSARY,
                "attack_glossary",
            )
        classification = classify(message, route_intent=route_intent)
        # Loaded before the formula short-circuit so an attack split across
        # turns is visible here, not only later in the ingress pass.
        prior = tuple(
            turn["content"]
            for turn in await self._history(conversation_id)
            if turn.get("role") == "user"
        )

        # The client's own email and name read as "another client" to the regex
        # rules (the address carries a client code), so screen self-references.
        def resolve(text: str) -> tuple[str, str | None]:
            return guardrails.resolve_self_reference(
                text,
                client_name=str(client_facts.get("clientName") or ""),
                client_email=client_email,
            )

        def own(text: str) -> str:
            return resolve(text)[0]

        name_refusal = resolve(message)[1]
        if name_refusal:
            return await self._refuse(
                message,
                classification,
                name_refusal,
                triggered=("leak_name_not_own",),
                conversation_id=conversation_id,
            )

        prior = tuple(own(turn) for turn in prior)
        # The answering model reads "the user with mail id <own email>" as a request
        # about someone else and refuses too, so it gets the resolved form as well.
        if own(message) != " ".join(message.split()):
            model_message = condense_for_model(own(message))
        # This is the call that covers the live path, for the reason given where
        # `prior` is loaded above: the formula and capability short-circuits below
        # return before the ingress pass, so an encoded or split attack wearing
        # either of those intents would never be decoded. `evasion` is in
        # INGRESS_CHECKS as well, for callers that enter at run_ingress; the
        # duplicate costs ~9ms of cached work and cannot change the outcome.
        evasion_hit = guardrails.evasion(
            message=own(message), intent=str(classification.intent), history=prior
        )
        if evasion_hit.blocked:
            return await self._refuse(
                message,
                classification,
                evasion_hit.reason or "That request cannot be processed.",
                triggered=evasion_hit.triggered,
                conversation_id=conversation_id,
            )
        if classification.intent is Intent.FORMULA_REQUEST:
            return await self._formula_refusal(message, classification, conversation_id)
        if route_intent is None and is_capability_question(message):
            return await self._fixed_answer(
                message, classification, conversation_id, CAPABILITY_REPLY, "capability"
            )

        # 2. Ingress guardrails (Part D). History is passed so multi-turn probing
        #    is judged across the whole conversation, not turn by turn.
        with stage("guardrails.ingress"):
            ingress, client_facts = await guardrails.screen_input(
                message=message,
                intent=str(route_intent or ""),
                history=prior,
                facts=client_facts,
                scope_topics=await corpus_topics(self._conn),
                client_email=client_email,
            )
        if ingress.blocked:
            return await self._refuse(
                message,
                Classification(Intent.GENERAL, confident=True),
                ingress.reason or "That request cannot be processed.",
                triggered=ingress.triggered,
                conversation_id=conversation_id,
            )

        # 3. Classify intent -- deterministically, without an LLM call.
        classification = classify(message, route_intent=route_intent)
        from app.orchestration.semantics import CLARIFY, clarification_language

        # A question in Spanish, Mandarin, Malay or Tamil that we cannot place is
        # a clarification, not a refusal and not a guess. Attacks were already
        # refused above, including by the semantic judge when it is enabled.
        if (
            route_intent is None
            and classification.intent is Intent.GENERAL
            and not classification.confident
            and (language := clarification_language(message))
        ):
            return await self._fixed_answer(
                message,
                classification,
                conversation_id,
                CLARIFY[language],
                "clarify_language",
            )

        # 3b. A message that says too little to answer. Gated on the same
        #     condition as the language check -- a query the deterministic layer
        #     placed confidently is by definition not vague, so the common case
        #     never pays for this call. Placed *after* ingress so a vague attack
        #     is refused rather than invited to rephrase, and after the ambiguity
        #     regex, which names the two readings it found and is more useful
        #     than asking for a rewrite. Fails open.
        if (
            route_intent is None
            and classification.intent is Intent.GENERAL
            and not classification.confident
            and not classification.is_ambiguous
        ):
            with stage("vagueness.check"):
                vague = await is_vague(message, prior)
            if vague:
                return await self._fixed_answer(
                    message, classification, conversation_id, TOO_VAGUE, "too_vague"
                )

        # 4. Gather evidence.
        with stage("retrieval.total"):
            retrieval = await self._retrieve(message, classification)

        confidence = assess(
            classification=classification,
            retrieval=retrieval,
            missing_profile_fields=missing_profile_fields,
            message=own(message),
        )

        # An abstention needs no generation: asking the model to answer a
        # question we have already decided we cannot ground would only invite a
        # fluent guess.
        if confidence.abstained:
            return await self._abstain(message, classification, confidence, retrieval)

        # 5. Assemble the payload -- the only place a Payload is constructed.
        #    The model sees collapsed repeats. The audit log keeps the original.
        history_for_payload = guardrails.history_for_model(await self._history(conversation_id))
        with stage("context.build"):
            payload = self._builder.build(
                BuildInput(
                    intent=classification.intent.value,
                    user_message=model_message,
                    client_facts=client_facts,
                    retrieved=retrieval.chunks,
                    history=history_for_payload,
                )
            )

        # 6. Dispatch.
        try:
            with stage("llm.hpc_call"):
                response = await self._llm.complete(payload, correlation_id=get_correlation_id())
        except LLMError as exc:
            await record_llm_call(self._conn, payload, status=exc.code)
            # Never substitute an ungrounded answer for an unavailable one.
            raise

        with stage("db.record_llm_call"):
            await record_llm_call(
                self._conn,
                payload,
                status="ok",
                response_text=response.text,
                latency_ms=response.latency_ms,
            )

        # Re-assess with the model's hint folded in as a ceiling.
        confidence = assess(
            classification=classification,
            retrieval=retrieval,
            missing_profile_fields=missing_profile_fields,
            model_hint=response.suggested_confidence,
            message=own(message),
        )

        # 7. Egress guardrails.
        allowed_refs = frozenset(c.chunk_ref for c in retrieval.chunks)
        with stage("guardrails.egress"):
            egress = await guardrails.screen_output(
                text=response.text,
                intent=classification.intent.value,
                allowed_chunk_refs=allowed_refs,
                facts=client_facts,
                chunk_texts=[c.text for c in retrieval.chunks],
                chunk_refs=tuple(c.chunk_ref for c in retrieval.chunks),
                user_message=message,
            )
        if egress.blocked:
            # Rewritten only in the voice the verdict asks for, and most egress
            # blocks ask for none. The ingress voice is never right here: it
            # declines a *question*, and an egress refusal is about the answer
            # drafted for a question that was perfectly fine -- "Why not?" came
            # back as "I can't explain why I couldn't verify that answer", a
            # refusal to explain its own refusal. The `unverified` voice says the
            # true thing instead: which answer was withheld, and that a check on
            # it failed. An outage or a leak block still carries no voice, so
            # those keep their template verbatim.
            return await self._refuse(
                message,
                classification,
                egress.reason or "That answer could not be released.",
                triggered=egress.triggered,
                conversation_id=conversation_id,
                rewritable=bool(egress.refusal_voice),
                voice=egress.refusal_voice,
            )
        text = egress.rewritten or response.text

        # 8. Append the disclosure structurally. Not negotiable, not promptable.
        applied = classification.intent in DISCLOSURE_INTENTS
        if applied:
            text = f"{text}\n\n---\n\n{disclosure_text()}"

        # 9. Persist.
        status = "pending_review" if confidence.requires_review else "completed"
        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type=DECISION_TYPE_FOR_INTENT.get(classification.intent, "chat_qa"),
            status=status,
            input=message,
            output=text,
            confidence=confidence.value,
            sources=_sources(retrieval),
            model_version=response.model_version,
            prompt_version_=payload.prompt_version,
            policy_version=policy_version,
            session_date=self._session_date,
        )
        await self._persist_turn(conversation_id, message, text, decision.id)

        return ChatResult(
            text=text,
            confidence=confidence,
            retrieval=retrieval,
            intent=classification.intent,
            decision_id=decision.id,
            decision_no=decision.display_no,
            model_version=response.model_version,
            disclosure_applied=applied,
            requires_review=confidence.requires_review,
            refused_formula=classification.intent is Intent.FORMULA_REQUEST,
            guardrails_triggered=tuple(egress.triggered) + tuple(ingress.triggered),
        )

    # --- stages ------------------------------------------------------------
    async def _retrieve(self, message: str, classification: Classification) -> RetrievalResult:
        if classification.intent is Intent.OUT_OF_SCOPE:
            return RetrievalResult(chunks=(), grounded=False)
        # Always the client-facing view: never widened for a client request.
        # A Chinese or Malay wording of a real advisory question is searched in
        # English, which is the language of the policy corpus.
        return await Retriever(self._conn, audience="client").search(retrieval_query(message))

    async def _history(self, conversation_id: uuid.UUID | None) -> list[dict]:
        if conversation_id is None:
            return []
        return await self._clients.history(conversation_id, limit=12)

    async def _persist_turn(
        self, conversation_id: uuid.UUID | None, message: str, reply: str, decision_id
    ) -> None:
        if conversation_id is None:
            return
        await self._clients.append_message(conversation_id, "user", message)
        await self._clients.append_message(conversation_id, "assistant", reply, decision_id)

    async def _abstain(
        self,
        message: str,
        classification: Classification,
        confidence: Confidence,
        retrieval: RetrievalResult,
    ) -> ChatResult:
        text = _abstention_text(classification, confidence)
        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type=DECISION_TYPE_FOR_INTENT.get(classification.intent, "chat_qa"),
            status="abstained",
            input=message,
            output=text,
            confidence=None,
            sources=_sources(retrieval),
            session_date=self._session_date,
        )
        return ChatResult(
            text=text,
            confidence=confidence,
            retrieval=retrieval,
            intent=classification.intent,
            decision_id=decision.id,
            decision_no=decision.display_no,
            model_version="none",
            disclosure_applied=False,
            requires_review=confidence.requires_review,
        )

    async def _formula_refusal(
        self,
        message: str,
        classification: Classification,
        conversation_id: uuid.UUID | None,
    ) -> ChatResult:
        return await self._fixed_answer(
            message,
            classification,
            conversation_id,
            FORMULA_REPLY,
            "formula_confidential",
            refused_formula=True,
        )

    async def _fixed_answer(
        self,
        message: str,
        classification: Classification,
        conversation_id: uuid.UUID | None,
        text: str,
        reason: str,
        *,
        refused_formula: bool = False,
    ) -> ChatResult:
        confidence = Confidence(
            value=Decimal("0.95"),
            abstained=False,
            reasons=(reason,),
        )
        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type="chat_qa",
            status="completed",
            input=message,
            output=text,
            confidence=confidence.value,
            session_date=self._session_date,
        )
        await self._persist_turn(conversation_id, message, text, decision.id)
        return ChatResult(
            text=text,
            confidence=confidence,
            retrieval=RetrievalResult(chunks=(), grounded=False),
            intent=classification.intent,
            decision_id=decision.id,
            decision_no=decision.display_no,
            model_version="none",
            disclosure_applied=False,
            requires_review=False,
            refused_formula=refused_formula,
        )

    async def _welfare_response(
        self,
        message: str,
        conversation_id: uuid.UUID | None,
    ) -> ChatResult:
        """Fixed welfare reply. Not a refusal: `refused` stays false.

        Recorded as `pending_review` with an escalation coded
        `welfare_concern:self_harm`, so the officer sees it as a welfare
        concern and not as an `ai_safety:*` block. No model is called.
        """
        confidence = Confidence(
            value=Decimal("0.95"),
            abstained=False,
            reasons=("welfare_concern",),
            requires_review=True,
        )
        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type="chat_qa",
            status="pending_review",
            input=message,
            output=WELFARE_REPLY,
            confidence=None,
            session_date=self._session_date,
            escalate=audit.EscalationReason(WELFARE_REASON_CODE),
        )
        await self._persist_turn(conversation_id, message, WELFARE_REPLY, decision.id)
        return ChatResult(
            text=WELFARE_REPLY,
            confidence=confidence,
            retrieval=RetrievalResult(chunks=(), grounded=False),
            intent=Intent.GENERAL,
            decision_id=decision.id,
            decision_no=decision.display_no,
            model_version="none",
            disclosure_applied=False,
            requires_review=True,
            guardrails_triggered=(WELFARE_RULE,),
        )

    async def _refuse(
        self,
        message: str,
        classification: Classification,
        reason: str,
        *,
        triggered: tuple[str, ...],
        conversation_id: uuid.UUID | None = None,
        detail: str = "",
        rewritable: bool = True,
        voice: str = "",
    ) -> ChatResult:
        confidence = Confidence(value=None, abstained=True, reasons=("guardrail",))
        fired = ", ".join(triggered) if triggered else "guardrail"

        # Every block in the system arrives here -- regex and judge alike -- so
        # naming the subject is done once rather than at fourteen call sites.
        # The rewrite is cosmetic by construction: `reason` still decides what
        # the refusal means, and a rewrite that fails validation is simply not
        # used. The disclosure line below is appended afterwards either way, so
        # no rewrite can drop it.
        with stage("refusal.rewrite"):
            rewritten = (
                await compose_refusal(reason, message, detail=detail, voice=voice)
                if rewritable
                else None
            )
        # One variable for what the client reads, used for the reply, the stored
        # turn and the audit record alike. Keeping the rewrite in only one of
        # those produced a refusal the officer console showed as specific while
        # the client was shown the generic template.
        shown = rewritten or reason
        output = f"{shown}\n\nAI safety: this query was refused ({fired})."
        decision = await audit.record_decision(
            self._conn,
            client_id=self._client_id,
            decision_type="chat_qa",
            status="refused",
            input=message,
            output=output,
            confidence=None,
            session_date=self._session_date,
            escalate=audit.EscalationReason(_safety_reason_code(triggered)),
        )
        await self._persist_turn(conversation_id, message, shown, decision.id)
        return ChatResult(
            text=shown,
            confidence=confidence,
            retrieval=RetrievalResult(chunks=(), grounded=False),
            intent=classification.intent,
            decision_id=decision.id,
            decision_no=decision.display_no,
            model_version="none",
            disclosure_applied=False,
            requires_review=True,
            guardrails_triggered=triggered,
        )


def _safety_reason_code(triggered: tuple[str, ...]) -> str:
    """Queue label for the officer console. First firing rule is the headline."""
    hit = next((name for name in triggered if name), "guardrail")
    return f"ai_safety:{hit}"


def _sources(retrieval: RetrievalResult) -> list[audit.SourceRef]:
    return [
        audit.SourceRef(
            chunk_ref=c.chunk_ref,
            doc_id=c.doc_id,
            title=c.title,
            section=c.section,
            page=c.page,
            score=c.score,
            passage_text=c.text,
        )
        for c in retrieval.chunks
    ]


def _abstention_text(classification: Classification, confidence: Confidence) -> str:
    if classification.is_ambiguous:
        options = "\n".join(f"  - {o}" for o in confidence.ambiguity_options)
        return (
            "I want to make sure I answer the right question. That could mean "
            f"either of these:\n{options}\n\nWhich did you have in mind?"
        )
    if classification.intent is Intent.OUT_OF_SCOPE:
        return (
            "That is outside what I can help with here -- I only cover your portfolio, "
            "your risk profile and the firm's investment policy. Is there something "
            "about your holdings or your recommendation I can help with instead?"
        )
    return (
        "I could not find anything in the firm's policy materials that supports an "
        "answer to that, so I would rather tell you than guess. You could try asking "
        "more specifically, or ask me about your own holdings and risk profile, which "
        "I can answer from your records."
    )


def confidence_floor() -> Decimal:
    return get_settings().low_confidence_threshold
