"""Intent routing (B.5/A.4).

Deterministic first. A rebalance request arrives from an endpoint, not from
guessing; a keyword table handles the obvious rest. The LLM classifier is a
fallback for genuinely ambiguous free text only -- classifying every turn with a
model call would spend the scarce shared endpoint on something a dictionary does
better and faster.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Intent(StrEnum):
    RISK_EXPLANATION = "risk_explanation"
    PORTFOLIO_EXPLANATION = "portfolio_explanation"
    REBALANCE_EXPLANATION = "rebalance_explanation"
    HOLDINGS_QUERY = "holdings_query"
    TRANSACTION_HISTORY = "transaction_history"
    POLICY_QUESTION = "policy_question"
    FORMULA_REQUEST = "formula_request"
    OUT_OF_SCOPE = "out_of_scope"
    PROFILING = "profiling"
    GENERAL = "general"


#: Intents whose answer must be grounded in the corpus. An ungrounded answer to
#: one of these is an abstention, not a best effort.
REQUIRES_GROUNDING = frozenset(
    {Intent.POLICY_QUESTION, Intent.PORTFOLIO_EXPLANATION, Intent.RISK_EXPLANATION}
)

#: Intents that need the expensive evidence (prices, positions, trades).
NEEDS_MARKET_EVIDENCE = frozenset(
    {Intent.HOLDINGS_QUERY, Intent.REBALANCE_EXPLANATION, Intent.TRANSACTION_HISTORY}
)

#: Creative framings that are out of scope only when their *subject* is.
#: "Tell me a story about dragons" is off-topic; "tell me a story about my risk
#: category" is a request for an explanation, differently phrased. Matching the
#: phrase alone refused both, which is precisely the over-refusal D.3 is graded
#: on -- so these are held apart from the topics below and tested against what
#: follows them.
NARRATIVE_FORMS = (
    "tell me a story",
    "story about",
    "write a story",
    "write me a story",
    "write a poem",
    "write me a poem",
    "poem about",
    "song about",
)

#: What a narrative request has to be about to stay in scope.
NARRATIVE_SUBJECTS = (
    "risk categor",
    "risk profile",
    "risk score",
    "portfolio",
    "holding",
    "rebalanc",
    "policy",
    "my cash",
    "allocation",
    "my investment",
    "my account",
    "eligib",
    "suitab",
)

#: How far past the phrase to look for its subject. Deliberately tight: in
#: "a poem about autumn for my portfolio newsletter" the poem is about autumn,
#: and a portfolio mentioned three words later does not make it a portfolio
#: question. Widening this to catch more phrasings starts accepting that one.
NARRATIVE_SUBJECT_WORDS = 3

OUT_OF_SCOPE_TERMS = (
    "itinerary",
    "holiday",
    "vacation",
    "recipe",
    "weather",
    "joke",
    "trip to",
    "restaurant",
    "flight",
    "hotel",
    "movie",
    "sports",
    "write me code",
    "write me a script",
    "write a script",
    "python script",
    "write a function",
    "homework",
    "essay",
    # Briefing examples. Phrases, so "which portfolio" and "key risks" still pass.
    "where the market is heading",
    "current market environment",
    "market forecast",
    "return over the next",
    "price target",
    "guaranteed number",
    "which etf",
    "which specific etf",
    "which stock",
    "what stock should",
    "promise me",
    "will not lose",
    "won't lose",
    "skip the risk",
    "skip the warning",
    "omit the risk",
    "top up my cpf",
    "buy insurance",
)

FORMULA_TERMS = (
    "exact calculation",
    "source code",
    "algorithm you use",
    "how do you compute",
    "how do you score",
    "how is my score",
    "what are the weights",
    "show me the weights",
    "scoring model",
    "scoring formula",
    "point system",
    "how is the score calculated",
    "appendix b",
    "risk score formula",
)

# "formula" by itself is ordinary financial language ("explain this formula").
# It is the confidential scoring model only together with one of these cues.
_SCORING_FORMULA_CUE = re.compile(
    r"you use|you used|your formula|scoring|risk score|risk model|produced|"
    r"appendix b|\bweights?\b|calculate my|my score|point system|"
    r"pemarkahan|puntuaci|bewertung|评分|評分",
    re.IGNORECASE,
)

FORMULA_REPLY = (
    "I can't share the scoring model itself -- it is confidential business logic "
    "(Appendix B). I can tell you, qualitatively, what shaped your result: your "
    "stated investment horizon, how much loss you told us you could tolerate, your "
    "liquidity needs, your monthly commitments and dependants, how stable your "
    "income is, your investment objective and your prior experience. Those factors "
    "are combined and the result places you in one of eight risk categories. "
    "I will not disclose the scoring constants, cut-offs or arithmetic."
)

#: "What can you help me with?" is the usual Week 10 control prompt. It is
#: answered without retrieval, because no policy passage describes the bot itself.
_CAPABILITY = re.compile(
    r"\bwhat (?:can|do) (?:you|the [\w\s-]{0,30}?(?:chatbot|bot|assistant|advisor|adviser))"
    r" (?:help (?:me )?with|do(?: for me)?)\b"
    r"|\bhow can you help\b|\bwhat are you able to (?:help with|do)\b",
    re.IGNORECASE,
)

CAPABILITY_REPLY = (
    "I can help with your own account on this platform. I can explain your risk "
    "category and, in plain language, the factors behind it; show what your "
    "portfolio holds on the session date; explain rebalancing and why your "
    "weights drift; and answer questions about the firm's suitability policy, "
    "such as which portfolios you are eligible for. I can't pick individual "
    "stocks or ETFs, forecast markets, promise returns, or give tax, CPF or "
    "insurance advice."
)


def is_capability_question(message: str) -> bool:
    return bool(_CAPABILITY.search(message))


RULES: tuple[tuple[Intent, tuple[str, ...]], ...] = (
    (Intent.FORMULA_REQUEST, FORMULA_TERMS),
    (Intent.OUT_OF_SCOPE, OUT_OF_SCOPE_TERMS),
    (
        Intent.REBALANCE_EXPLANATION,
        (
            "rebalanc",
            "drift",
            "back to target",
            "off target",
            "再平衡",
            "重新平衡",
            "pengimbangan",
            "rebalanceo",
            "reequilibr",
            "rééquilibr",
            "ribilanci",
            "neugewicht",
            "herbalans",
            "リバランス",
            "리밸런싱",
            "ребаланс",
            "إعادة التوازن",
            "पुनर्संतुलन",
            "tái cân",
            "การปรับสมดุล",
            "yeniden dengele",
            "rebalanceamento",
            "herbelegging",
            "rebalans",
        ),
    ),
    (
        Intent.RISK_EXPLANATION,
        (
            "risk category",
            "risk profile",
            "risk score",
            "why am i",
            "risiko",
            "风险",
            "riesgo",
            "risque",
            "risikokategorie",
            "risco",
            "rischio",
            "リスク",
            "위험",
            "जोखिम",
            "مخاطر",
        ),
    ),
    (
        Intent.TRANSACTION_HISTORY,
        (
            "invested",
            "my trades",
            "my trade",
            "transaction",
            "what did i buy",
            "what did i sell",
            "when i invested",
            "my order",
            "last order",
            "order ticket",
            "last transaction",
        ),
    ),
    (
        Intent.HOLDINGS_QUERY,
        (
            "my holdings",
            "how much do i have",
            "portfolio value",
            "my units",
            "how am i doing",
            "performance",
            "my cash",
            "what does my portfolio hold",
            "what do i hold",
            "portfolio hold",
            "我的持仓",
            "我的持股",
            "pegangan saya",
            "mis tenencias",
            "mes avoirs",
            "meine bestände",
            "minhas posições",
            "保有資産",
            "보유 종목",
        ),
    ),
    (
        Intent.PORTFOLIO_EXPLANATION,
        ("recommend", "which portfolio", "why this portfolio", "suitable", "allocation"),
    ),
    (
        Intent.POLICY_QUESTION,
        (
            "policy",
            "cka",
            "knowledge assessment",
            "satellite",
            "eligib",
            "allowed to",
            "can i invest in",
            "rules",
        ),
    ),
)

#: Questions with more than one reasonable reading. Different from "we lack the
#: information to answer": here we have it, but do not know which was meant.
AMBIGUOUS: tuple[tuple[re.Pattern, tuple[str, ...]], ...] = (
    (
        re.compile(r"\bwhat'?s my risk (category|profile)\b", re.IGNORECASE),
        (
            "the risk category currently assigned to you",
            "what your category would be if we re-assessed you today",
        ),
    ),
    (
        re.compile(r"\bhow much (can|should) i invest\b", re.IGNORECASE),
        (
            "the cash you have available to invest right now",
            "how much the suitability policy permits you to put into a given portfolio",
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Classification:
    intent: Intent
    confident: bool
    #: Populated when the query has more than one reasonable reading; the
    #: pipeline asks which was meant rather than picking one (C.4).
    ambiguity_options: tuple[str, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.ambiguity_options)


#: A legitimate question asked in another language. Retrieval searches the English
#: topic, because the policy corpus is English. Attack phrases never reach this:
#: they are refused before retrieval.
_QUERY_EXPANSIONS = (
    ("再平衡", "rebalancing portfolio drift recommended asset allocation"),
    ("pengimbangan", "rebalancing portfolio drift recommended asset allocation"),
    ("rebalanceo", "rebalancing portfolio drift recommended asset allocation"),
    ("risiko", "eligibility matrix risk category portfolios"),
    ("风险", "eligibility matrix risk category portfolios"),
)


def is_scoring_formula_request(text: str) -> bool:
    """True for the confidential scoring model, not for a textbook formula."""
    from app.orchestration.semantics import match_text

    folded = match_text(text)
    if any(term in folded for term in FORMULA_TERMS):
        return True
    if not re.search(r"\bformu", folded):
        return False
    return _SCORING_FORMULA_CUE.search(folded) is not None


def retrieval_query(message: str) -> str:
    """English search text for a question the keyword index can actually match.

    The full sentence is a bad query: "why does it matter?" is turned into
    "rebalancing AND matter", and "matter" is not in the policy, so the real
    section is missed. A long message is not embedded whole; the concept, or
    the tail where the question usually sits, is enough.
    """
    from app.orchestration.semantics import semantic_query

    focused = semantic_query(message)
    if focused:
        return focused
    low = message.lower()
    extras = [english for needle, english in _QUERY_EXPANSIONS if needle.lower() in low]
    if extras:
        return " ".join(dict.fromkeys(extras))
    intent = classify(message).intent
    if intent is Intent.REBALANCE_EXPLANATION:
        # Same string as the rebalance concept in `semantics.py`: the bare word
        # no longer finds the only passages that still cover the topic.
        return "rebalancing portfolio drift recommended asset allocation"
    if intent is Intent.RISK_EXPLANATION:
        # Same string as the risk concept in `semantics.py`, for the same
        # measured reason: the bare phrase no longer finds the policy.
        return "eligibility matrix risk category portfolios"
    if len(message) > 500:
        return message[-500:]
    return message


def narrative_is_off_topic(text: str) -> bool:
    """True when the message asks for a story, poem or song about something the
    assistant does not cover.

    Checked against the words immediately following the phrase rather than the
    whole message, because a domain word can appear anywhere in an off-topic
    request -- "a poem about autumn for my portfolio newsletter" mentions a
    portfolio but is not about one.
    """
    for phrase in NARRATIVE_FORMS:
        start = text.find(phrase)
        if start == -1:
            continue
        tail = text[start + len(phrase) :].lstrip()
        for lead in ("about", "regarding", "on"):
            if tail.startswith(lead + " "):
                tail = tail[len(lead) :].lstrip()
                break
        window = " ".join(tail.split()[:NARRATIVE_SUBJECT_WORDS])
        if not any(subject in window for subject in NARRATIVE_SUBJECTS):
            return True
    return False


def classify(message: str, *, route_intent: Intent | None = None) -> Classification:
    """Route a message. `route_intent` is the endpoint's own knowledge and wins."""
    if route_intent is not None:
        return Classification(route_intent, confident=True)

    text = message.lower().strip()
    if not text:
        return Classification(Intent.GENERAL, confident=True)

    for pattern, options in AMBIGUOUS:
        if pattern.search(message):
            return Classification(
                Intent.RISK_EXPLANATION, confident=True, ambiguity_options=options
            )

    if is_scoring_formula_request(message):
        return Classification(Intent.FORMULA_REQUEST, confident=True)

    # "insights" is too broad to be a keyword, but "insights when I invested"
    # is an account-record question. Require a ledger word next to it.
    if re.search(
        r"\binsights?\b.{0,80}\b(invest|trade|transaction|holding|portfolio|order)\b"
        r"|\b(invest|trade|transaction|holding|portfolio|order)\b.{0,80}\binsights?\b",
        text,
        re.IGNORECASE,
    ):
        return Classification(Intent.TRANSACTION_HISTORY, confident=True)

    for intent, terms in RULES:
        if intent is Intent.FORMULA_REQUEST:
            continue
        # Narrative framings are ranked with the other out-of-scope topics, but
        # judged on their subject rather than on the phrase alone.
        if intent is Intent.OUT_OF_SCOPE and narrative_is_off_topic(text):
            return Classification(intent, confident=True)
        if any(term in text for term in terms):
            return Classification(intent, confident=True)

    from app.orchestration.semantics import semantic_intent_name

    semantic = semantic_intent_name(message)
    if semantic is not None:
        return Classification(Intent(semantic), confident=True)

    return Classification(Intent.GENERAL, confident=False)
