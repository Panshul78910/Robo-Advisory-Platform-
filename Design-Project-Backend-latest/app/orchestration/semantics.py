"""Meaning-level routing for finance questions.

The keyword table in `intent.py` catches the obvious wording. This module
catches the same request said another way, in English or in Spanish, Mandarin,
Malay or Tamil, including a mix of those. It matches concepts (risk category,
rebalancing, holdings), not one exact sentence.

The original message is never rewritten here. Callers keep it for the model
and for the audit log. The folded text is only for matching.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\ufeff\u2060]")


def match_text(text: str) -> str:
    """Fold case and Latin accents. CJK and Tamil are left intact."""
    text = unicodedata.normalize("NFKC", text)
    text = _ZERO_WIDTH.sub("", text)
    text = text.casefold()
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True, slots=True)
class _Concept:
    name: str
    query: str
    needles: tuple[str, ...]
    stems: tuple[str, ...] = ()


# First match wins, so a sentence that mentions both risk and a portfolio still
# follows the more specific topic. Needles are concepts, not full questions.
_CONCEPTS: tuple[_Concept, ...] = (
    _Concept(
        "rebalance_explanation",
        # The placeholder policy had a section headed "Rebalancing", so the bare
        # word found it. Appendix E has none, and the only remaining coverage is
        # CMG-G02 paragraphs 19-22, which never use the word as a heading -- so
        # "rebalancing" alone scored 0.312 against a 0.34 floor and retrieved
        # nothing. Echoing paragraph 19's own language ("rebalancing of clients'
        # portfolios to address portfolio drift... back to the most recent
        # advice on the recommended asset allocation") scores 0.463 and returns
        # six passages.
        #
        # Not the highest-scoring phrasing available: adding "authorisation"
        # reaches 0.573 but drops the paragraph that actually defines the term,
        # leaving only the rules about consenting to it. Top score is not the
        # objective; carrying the definition is.
        "rebalancing portfolio drift recommended asset allocation",
        (
            "rebalanc",
            "off target",
            "back to target",
            "再平衡",
            "重新平衡",
            "调整仓位",
            "調倉",
            "pengimbangan",
            "penyeimbangan",
            "imbang semula",
            "rebalanceo",
            "reequilibr",
            "மறுசமநிலை",
            "சமநிலைப்படுத்த",
        ),
        ("rebalance", "rebalancing", "rebalanceo"),
    ),
    _Concept(
        "risk_explanation",
        # Re-tuned when the placeholder suitability document was replaced by the
        # authoritative Appendix E. The old corpus had a section headed "Risk
        # categories", so the bare phrase found it; Appendix E does not explain
        # the categories at all -- it says "per the internal scoring
        # methodology" and then gives the eligibility matrix. Measured against
        # the new corpus, "risk category" alone scored 0.532 and pulled back the
        # platform terms' confidentiality clause, while this scores 0.777 and
        # returns four Appendix E passages.
        "eligibility matrix risk category portfolios",
        (
            "risk categor",
            "risk profile",
            "risk level",
            "risk classif",
            "risk band",
            "risk rating",
            "risk tolerance",
            "investment risk",
            "my risk",
            "this risk",
            # How clients actually ask whether they may move up or down a
            # category. The needles above are all noun phrases taken from the
            # policy's own vocabulary, so "can I invest more aggressively?"
            # matched none of them, embedded as a whole colloquial sentence,
            # and scored 0.30 against a 0.34 floor -- no passage retrieved, so
            # the answer had nothing to stand on and egress refused it as
            # unverifiable. Measured, five of thirteen ordinary client
            # questions failed this way, "should I be more conservative?"
            # worst at 0.12.
            #
            # Comparatives, not bare adjectives: "aggressive sales tactics" is
            # a fair-dealing question and must keep retrieving fair dealing.
            "more aggressive",
            "aggressively",
            "too aggressive",
            "more conservative",
            "too conservative",
            "riskier",
            "more risk",
            "less risk",
            "higher risk",
            "lower risk",
            "too much risk",
            "safer",
            "mas agresiv",
            "mas conservador",
            "lebih agresif",
            "lebih konservatif",
            "激进",
            "保守",
            "why was i assigned",
            "why am i assigned",
            "put me in",
            "my profile says",
            "say balanced",
            "says balanced",
            "behind my categor",
            "my categor",
            "this categor",
            "clasific",
            "把我分到",
            "分到这个",
            "kategori saya",
            "kategori ini",
            "kategori ni",
            "riesgo",
            "categoria de riesgo",
            "nivel de riesgo",
            "perfil de riesgo",
            "clasificaron",
            "风险",
            "風險",
            "风险等级",
            "风险类别",
            "risiko",
            "kategori risiko",
            "tahap risiko",
            "toleransi risiko",
            "diletakkan dalam kategori",
            "அபாய",
            "ஆபத்து",
            "இடர்",
        ),
        ("riesgo", "risiko"),
    ),
    _Concept(
        "holdings_query",
        "holdings cash balance",
        (
            "my holdings",
            "what do i hold",
            "what does my portfolio hold",
            "portfolio hold",
            "how much cash",
            "cash balance",
            "my cash",
            "portfolio value",
            "我的持仓",
            "我的持股",
            "现金余额",
            "我有多少",
            "pegangan",
            "baki tunai",
            "baki saya",
            "apa yang saya pegang",
            "mis tenencias",
            "mis posiciones",
            "mi cartera",
            "cuanto tengo",
            "saldo de caja",
            "இருப்பு",
            "பண இருப்பு",
            "என்னிடம் உள்ள",
        ),
        ("holdings",),
    ),
    _Concept(
        "transaction_history",
        "investment trades orders transactions",
        (
            "my trades",
            "my transactions",
            "when i invested",
            "what did i buy",
            "invested cash",
        ),
        ("transaction",),
    ),
    _Concept(
        "portfolio_explanation",
        # Appendix E section 1 is headed "Model portfolios" and holds the
        # allocation table, so the query says so: 0.463 -> 0.609.
        "model portfolios allocation eligibility",
        (
            "asset allocation",
            "allocation",
            "which portfolio",
            "suitable",
            "suitability",
            "asignacion",
            "asignación",
            "idoneidad",
            "资产配置",
            "配置比例",
            "peruntukan",
            "kesesuaian",
            "ஒதுக்கீடு",
            "பொருத்தம்",
        ),
        ("allocation", "suitability"),
    ),
    # Appendix E section 3 holds the override rules -- the caps applied after
    # scoring, and E-2a, which says a client who can tolerate no loss must be
    # told robo-advisory is unsuitable and escalated to a human. That rule lives
    # in a table two chunks away from anything the other concepts search for, so
    # "what happens if I can tolerate no loss?" retrieved nothing at all (0.246
    # against a 0.34 floor) and the safest answer in the policy was unreachable.
    _Concept(
        "policy_question",
        "suitability override maximum tolerable loss cap",
        (
            "tolerable loss",
            "tolerate no loss",
            "tolerate any loss",
            "no loss",
            "cannot afford to lose",
            "can't afford to lose",
            "suitability override",
            "override rule",
        ),
    ),
    # Before the general policy concept: a FEAT question is about the MAS
    # principles document, not about suitability.
    #
    # The acronym embeds badly on its own -- "Explain FEAT principles" scored
    # 0.271 against a 0.34 floor and pulled back a single chunk, while the words
    # it stands for score 0.552 and pull back the right document. Searching the
    # expansion is the whole point of this entry.
    #
    # Needles are qualified ("feat principle", not "feat") because the concept
    # match is a plain substring test: a bare "feat" would fire on "feature" and
    # "defeat". A question that says only "FEAT" is therefore still missed.
    _Concept(
        "policy_question",
        "fairness ethics accountability transparency",
        (
            "feat principle",
            "feat framework",
            "feat guideline",
            "fairness ethics accountability",
            "fairness, ethics, accountability",
        ),
    ),
    # Before the eligibility concept, and split out of it. Both sets of triggers
    # used to share one search string, so "what is the CKA?", "what happens if I
    # fail the knowledge assessment?" and "why am I not eligible for the
    # aggressive portfolio?" all became "eligible portfolios risk category" and
    # came back with byte-identical passages -- knowledge-assessment questions
    # answered out of the eligibility matrix.
    #
    # The cost was worst on the keyword arm. "What is the CKA?" parses to the
    # single token `cka`, which appears in 3 of 250 chunks: the rare literal the
    # second arm exists to catch. The rewrite replaced it before the arm ever
    # saw it, so the one query it was built for was the one it never got.
    #
    # The split did not actually fix that, because the string it moved to --
    # "customer knowledge assessment specified investment product" -- does not
    # contain `cka` either, so the keyword arm still never saw the token. Worse,
    # every word of it is MAS statutory vocabulary, so it scored 0.68-0.76
    # against FAA-N16 and SFA04-N12 and never returned Appendix E section 4, the
    # only passage that states the question count and the pass mark. Asked "how
    # many CKA questions are there?", retrieval returned six regulatory passages
    # about who counts as knowledgeable -- all far above the 0.34 floor, so
    # nothing abstained -- while the answer sat unretrieved. "knowledge check"
    # was not a trigger, escaped the rewrite, and was the only wording that
    # worked.
    #
    # This string echoes Appendix E's own heading and sentence instead, and adds
    # "knowledge check" to the needles so all three names take one path. Measured
    # over 11 phrasings: answer chunk 0/11 -> 11/11, and the statutory passages
    # are still returned for the questions that want them. Deliberately states
    # the SUBJECT ("how many questions it has and its pass mark") rather than the
    # answer: a string saying "five-question, 4 of 5" scored lower (0.760 vs
    # 0.807) and would have put a second copy of a policy figure in code.
    _Concept(
        "policy_question",
        "Knowledge check (CKA-style): the customer knowledge assessment quiz, how many "
        "questions it has and its pass mark, required before the higher portfolios or "
        "any satellite ETF exposure",
        (
            "cka",
            "knowledge check",
            "knowledge assessment",
            "penilaian pengetahuan",
            "evaluacion de conocimientos",
            "知识评估",
            "அறிவு மதிப்பீடு",
        ),
    ),
    _Concept(
        "policy_question",
        # Likewise re-tuned for Appendix E. "diversification" was carrying the
        # old placeholder; against the new corpus it dragged the whole query
        # into the fair-dealing guidelines (0.431, one Appendix E passage out of
        # six). Naming what the policy actually contains scores 0.751 with four.
        "eligible portfolios risk category",
        (
            "eligib",
            "time horizon",
            "investment horizon",
            "diversif",
            "horizonte",
            "diversificacion",
            "投资期限",
            "资格",
            "分散",
            "jangka masa",
            "kelayakan",
            "pelbagaian",
            "கால அளவு",
            "முதலீட்டு காலம்",
            "தகுதி",
            "பல்வகை",
        ),
        ("diversification", "diversificacion", "horizonte"),
    ),
)

# Positive off-topic cues. Unfamiliar text is not on this list, so an unknown
# language is not treated as out of scope by default.
_OFF_TOPIC = (
    "receta",
    "itinerario",
    "el clima",
    "chiste",
    "食谱",
    "天气",
    "笑话",
    "假期去",
    "resipi",
    "cuaca",
    "percutian",
    "jenaka",
    "செய்முறை",
    "வானிலை",
    "விடுமுறை",
    "நகைச்சுவை",
)

_DEFINITION = re.compile(
    r"\bwhat (?:does|do|is the meaning of|did)\b.{0,180}\bmean\b|"
    r"\bmeaning of\b|"
    r"\bdefine (?:the )?(?:phrase|term|words)\b|"
    r"qu[eé] significa|qu[eé] quiere decir|"
    r"\bapa maksud\b|\bmaksud(?:nya)? apa\b|"
    r"是什么意思|什麼意思|什么意思|"
    r"என்றால் என்ன|பொருள் என்ன",
    re.IGNORECASE,
)
# A second request to comply, outside a "what does this mean" question.
_EXTRA_DIRECTIVE = re.compile(
    r"\b(?:show|print|reveal|display|output|dump|translate|encode|obey|comply|do it|do that)\b|"
    r"muestra|revela|imprime|ensena|tunjukkan|dedahkan|"
    r"显示|告诉我|给我看|காட்டு",
    re.IGNORECASE,
)

_QUESTION = re.compile(
    r"[?？]|(?:^|[\s¿])(?:kenapa|mengapa|bolehkah|terangkan|por que|que es|cual es|"
    r"como |explicame|podrias)\b|为什么|嗎|吗|எப்படி|ஏன்|விளக்க",
    re.IGNORECASE,
)


def _near(token: str, stem: str) -> bool:
    """One insertion, deletion or substitution. Tokens shorter than 6 are skipped."""
    if len(stem) < 6 or not 6 <= len(token) <= 24:
        return False
    if abs(len(token) - len(stem)) > 1:
        return False
    if token == stem:
        return True
    if len(token) == len(stem):
        return sum(a != b for a, b in zip(token, stem, strict=True)) == 1
    longer, shorter = (token, stem) if len(token) > len(stem) else (stem, token)
    i = j = skipped = 0
    while i < len(longer) and j < len(shorter):
        if longer[i] != shorter[j]:
            skipped += 1
            if skipped > 1:
                return False
            i += 1
            continue
        i += 1
        j += 1
    return True


def _winner(text: str) -> _Concept | None:
    for concept in _CONCEPTS:
        if any(needle in text for needle in concept.needles):
            return concept
    tokens = [tok for tok in text.split() if tok.isascii() and 6 <= len(tok) <= 24]
    if len(tokens) > 240:
        tokens = tokens[:120] + tokens[-120:]
    for concept in _CONCEPTS:
        if not concept.stems:
            continue
        for token in tokens:
            if any(_near(token, stem) for stem in concept.stems):
                return concept
    return None


def semantic_intent_name(message: str) -> str | None:
    """Intent value, or None when the wording is not a known finance concept."""
    text = match_text(message)
    if not text:
        return None
    hit = _winner(text)
    if hit is not None:
        return hit.name
    if any(needle in text for needle in _OFF_TOPIC):
        return "out_of_scope"
    return None


def semantic_query(message: str) -> str | None:
    """Short English retrieval text for a recognised concept. None otherwise."""
    hit = _winner(match_text(message))
    return hit.query if hit is not None else None


def is_definition_not_directive(message: str) -> bool:
    """True when the user asks what a phrase means and does not also say to obey it."""
    if not _DEFINITION.search(message):
        return False
    return _EXTRA_DIRECTIVE.search(match_text(message)) is None


def clarification_language(message: str) -> str | None:
    """Language to ask a clarifying question in, when the text is a question we
    cannot place and it is not English.

    English still goes to retrieval. An unknown language is not treated as an attack.
    """
    if _winner(match_text(message)) is not None:
        return None
    if not _QUESTION.search(message):
        return None
    if re.search(r"[\u4e00-\u9fff]", message):
        return "zh"
    if re.search(r"[\u0b80-\u0bff]", message):
        return "ta"
    folded = match_text(message)
    if re.search(r"\b(kenapa|mengapa|bolehkah|terangkan)\b", folded):
        return "ms"
    if "¿" in message or re.search(r"\b(por que|que es|cual es|explicame|podrias)\b", folded):
        return "es"
    return None


CLARIFY = {
    "en": (
        "I want to make sure I understand. Are you asking about your risk category, "
        "your holdings, rebalancing, or the firm's policy?"
    ),
    "es": (
        "Quiero entenderte bien. ¿Preguntas por tu categoría de riesgo, tus posiciones, "
        "el rebalanceo o la política de la firma?"
    ),
    "zh": "我想确认你的问题。你是在问风险类别、持仓、再平衡，还是公司的投资政策？",
    "ms": (
        "Saya mahu pastikan saya faham. Adakah anda bertanya tentang kategori risiko, "
        "pegangan, pengimbangan semula, atau polisi firma?"
    ),
    "ta": (
        "உங்கள் கேள்வியை உறுதிப்படுத்த விரும்புகிறேன். முதலீட்டு அபாய வகை, இருப்புகள், "
        "மறுசமநிலை, அல்லது நிறுவனக் கொள்கை பற்றி கேட்கிறீர்களா?"
    ),
}
