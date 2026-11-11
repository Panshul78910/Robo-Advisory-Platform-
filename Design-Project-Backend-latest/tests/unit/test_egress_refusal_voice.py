"""An egress refusal is about the answer, not about the question.

The question that reaches egress has cleared Llama Guard, the input judge, the
regex layer and the vagueness check. It is, by construction, a legitimate
in-scope question. What failed is the answer drafted for it -- a figure that did
not reconcile, a citation that was not retrieved, an eligibility the record does
not support, or the faithfulness judge.

So the refusal had nothing to personalise with and said nothing useful: the
client waited for a full generation plus a judge and got "I couldn't verify that
answer", with no indication of which answer. The old code disabled rewriting
here outright, for a good reason -- the ingress rewriter declines *questions*,
and asked to decline this one it produced "I can't explain why I couldn't verify
that answer", a refusal to explain its own refusal.

The fix is a second voice rather than no voice. It names the subject and says a
check on the draft failed, and the validator enforces exactly that: naming the
client's own subject is allowed only inside a verification clause, and a
verification clause is *required*, because an egress refusal that gives any
other reason for withholding is giving an untrue one.
"""

from __future__ import annotations

import re

import pytest

from app.llm.refusal import SYSTEM, SYSTEM_EGRESS, is_safe_refusal
from app.orchestration import guardrails as g
from app.orchestration.guardrails import VOICE_UNVERIFIED

TEMPLATE = (
    "I couldn't verify that answer against the firm's policy materials and your "
    "records, so I won't state it. I can answer from what those documents do say."
)
MESSAGE = "Why did my portfolio drift from the target weights?"

#: The MESSAGE/REPLY pairs the egress prompt teaches from, read back out of it.
#: Each reply has to be judged against its own question: the subject check asks
#: whether the rewrite names what *that* person asked about.
EXAMPLES = [
    (" ".join(q.split()), " ".join(r.split()))
    for q, r in re.findall(r"MESSAGE: (.+?)\nREPLY: (.+?)(?=\n\nMESSAGE:|$)", SYSTEM_EGRESS, re.S)
]


def egress(text: str) -> tuple[bool, str]:
    return is_safe_refusal(text, TEMPLATE, MESSAGE, "", VOICE_UNVERIFIED)


def ingress(text: str) -> tuple[bool, str]:
    return is_safe_refusal(text, TEMPLATE, MESSAGE, "", "")


class TestThePromptsOwnExamplesPass:
    """A few-shot example the validator would reject teaches the model to produce
    rejected rewrites. One of the five did, on the first draft: it said "policy
    passages", which is internal vocabulary."""

    @pytest.mark.parametrize(("question", "reply"), EXAMPLES)
    def test_every_egress_example_would_ship(self, question, reply):
        ok, why = is_safe_refusal(reply, TEMPLATE, question, "", VOICE_UNVERIFIED)
        assert ok, f"{why}: {reply}"

    def test_there_are_several_distinct_openings(self):
        """One phrasing produces a slot-filler. The examples have to disagree
        with each other about where the sentence starts."""
        assert len(EXAMPLES) >= 5
        assert len({r.split()[0].lower() for _, r in EXAMPLES}) >= 3


class TestTheSubjectMayBeNamed:
    @pytest.mark.parametrize(
        ("question", "reply"),
        [
            (
                "Why did my portfolio drift from the target weights?",
                "I drafted an explanation of the drift in your portfolio but couldn't verify "
                "it against the firm's materials. I can go through what those documents say.",
            ),
            (
                "Why was I assigned this risk category?",
                "I couldn't confirm what I had written about your risk category against your "
                "record, so I won't state it. Your dashboard has the details.",
            ),
            (
                "How much cash do I have after the last switch?",
                "The figure I was about to quote for your cash balance couldn't be reconciled "
                "with your records, so I won't give a number.",
            ),
            (
                "What is my current allocation across asset classes?",
                "I can't verify my answer on your allocation against the firm's documents, so "
                "I'm not going to state it.",
            ),
        ],
    )
    def test_in_scope_subjects_are_allowed_inside_a_verification_clause(self, question, reply):
        ok, why = is_safe_refusal(reply, TEMPLATE, question, "", VOICE_UNVERIFIED)
        assert ok, why

    @pytest.mark.parametrize(
        "reply",
        [
            "I can't help with your portfolio drift. I can explain the firm's policy.",
            "I cannot explain your risk category.",
            "Your portfolio is outside what I cover.",
            "I'm not able to discuss your allocation. I can explain the firm's policy.",
        ],
    )
    def test_the_same_subjects_are_refused_without_one(self, reply):
        """This is the whole relaxation: a verification clause, not a free pass.
        Telling a client their own portfolio is off limits is the failure the
        in-scope rule exists to prevent, and an egress block is not a licence
        for it."""
        ok, why = egress(reply)
        assert not ok
        assert why == "declines something in scope"


class TestAVerificationClauseIsRequired:
    @pytest.mark.parametrize(
        "reply",
        [
            "I won't be answering that one. I can explain the firm's policy instead.",
            "That answer isn't something I'll state. Ask me about your holdings.",
            "I decline to answer about drift. I can explain the firm's policy.",
        ],
    )
    def test_withholding_without_a_reason_is_rejected(self, reply):
        """Stricter than ingress, not looser. An egress refusal that names no
        failed check has given some other reason, and every other reason
        available to it is false."""
        ok, why = egress(reply)
        assert not ok
        assert why == "no verification clause"


class TestItHasToNameSomething:
    """The one thing the rewrite exists to add. Every other rule is satisfiable
    by the template reworded, which is what the model produced live when the
    assigned opening said only "begin with the check that failed"."""

    def test_a_subjectless_rewrite_is_rejected(self):
        ok, why = egress(
            "That didn't reconcile with the firm's policy materials and your records, "
            "so I won't state it."
        )
        assert not ok
        assert why == "names no subject"

    @pytest.mark.parametrize(
        "reply",
        [
            "My answer on your portfolio's drift didn't reconcile with the firm's "
            "materials, so I won't state it.",
            "I couldn't verify the drift figures against the firm's materials, so I "
            "won't state it.",
        ],
    )
    def test_one_shared_content_word_is_enough(self, reply):
        ok, why = egress(reply)
        assert ok, why

    @pytest.mark.parametrize(
        ("question", "reply"),
        [
            (
                "Am I allowed to hold P6 given my profile?",
                "I'd drafted an explanation of your eligibility for P6, but I couldn't "
                "verify it against the firm's policy materials, so I won't state it.",
            ),
            (
                "Have I passed the CKA?",
                "I couldn't confirm your CKA result against your record, so I won't state it.",
            ),
        ],
    )
    def test_a_short_code_counts_as_the_subject(self, question, reply):
        """The length floor discarded the most specific tokens in the domain.
        Both of these name the subject exactly, and both were rejected for
        naming none, because the shared word was two or three characters."""
        ok, why = is_safe_refusal(reply, TEMPLATE, question, "", VOICE_UNVERIFIED)
        assert ok, why

    @pytest.mark.parametrize(
        ("question", "reply"),
        [
            (
                "How often should I rebalance?",
                "I couldn't confirm the rebalancing frequency against the firm's policy "
                "materials, so I won't state it.",
            ),
            (
                "Why did my portfolio drift?",
                "My answer on why your portfolio drifted didn't reconcile with the firm's "
                "materials, so I won't state it.",
            ),
            (
                "Why was I assigned this risk category?",
                "I couldn't verify your risk categorisation against the record, so I "
                "won't state it.",
            ),
        ],
    )
    def test_an_inflected_subject_still_counts(self, question, reply):
        """Naming the subject in whatever form the sentence wants is better
        writing, and exact matching rejected it: `rebalance` is not
        `rebalancing`, `drift` is not `drifted`."""
        ok, why = is_safe_refusal(reply, TEMPLATE, question, "", VOICE_UNVERIFIED)
        assert ok, why

    def test_a_message_with_no_subject_of_its_own_is_not_penalised(self):
        """Words the message shares with the template carry no information, so a
        message made only of them cannot be required to appear."""
        ok, why = is_safe_refusal(
            "I couldn't verify that against your records, so I won't state it.",
            TEMPLATE,
            "answer that from your records",
            "",
            VOICE_UNVERIFIED,
        )
        assert ok, why


class TestWhatCountsAsANameableSubject:
    """One definition of "content word", shared by the gate that decides whether
    to rewrite at all and the check that decides whether the rewrite named
    anything. They disagreed: `CKA` counted for the second and not the first."""

    @pytest.mark.parametrize(
        "question",
        [
            "Have I passed the CKA?",
            "What are the fair dealing outcomes?",
            "Why did my portfolio drift?",
        ],
    )
    def test_a_real_question_is_nameable(self, question):
        from app.llm.refusal import has_nameable_subject

        assert has_nameable_subject(question)

    @pytest.mark.parametrize("message", ["Why not?", "ok", "and?", "what about now", "tell me"])
    def test_an_anaphoric_message_is_not(self, message):
        """These carry their meaning in the turn before them, which the rewriter
        never sees. Asked to name the subject, the model invents one from the
        template -- the original "I can't explain why I couldn't verify that
        answer"."""
        from app.llm.refusal import has_nameable_subject

        assert not has_nameable_subject(message)


class TestTheOpeningIsAssigned:
    """Asking a 27B to vary its phrasing does not work -- eight of eight live
    rewrites opened "The <subject>..." despite five distinct examples, a raised
    temperature and an explicit instruction not to. Naming one opening per
    request does work."""

    def test_the_same_question_always_gets_the_same_opening(self):
        """Deterministic, so a refusal is reproducible from the audit record."""
        from app.llm.refusal import _opening_for

        assert _opening_for(MESSAGE) == _opening_for(MESSAGE)

    def test_no_opening_is_starved(self):
        """Over a realistic spread of questions every opening gets used. Ten
        questions is too small a sample to assert this on -- it hit three of five
        buckets, which looked like hash bias and was not: crc32 % 5 is uniform to
        within 4% over 5000 strings."""
        from app.llm.refusal import _OPENINGS, _opening_for

        subjects = [
            "portfolio drift",
            "risk category",
            "cash balance",
            "satellite exposure",
            "diversification",
            "rebalancing frequency",
            "eligible portfolios",
            "fair dealing outcomes",
            "total value",
            "knowledge assessment",
        ]
        frames = [
            "Why is my {} like this?",
            "What does the policy say about {}?",
            "Can you explain {} to me?",
            "I don't understand {}.",
            "Tell me about my {}.",
        ]
        questions = [f.format(s) for s in subjects for f in frames]
        used = {_opening_for(q) for q in questions}
        assert used == set(_OPENINGS)

    def test_every_opening_demands_the_subject(self):
        """Not stylistic. The opening that did not demand it produced "That
        didn't reconcile with the firm's materials" -- the template, reworded."""
        from app.llm.refusal import _OPENINGS

        assert all("subject" in o for o in _OPENINGS)


class TestEverythingElseStillApplies:
    @pytest.mark.parametrize(
        ("reply", "expected"),
        [
            (
                "I couldn't verify it, but your cash balance is $12,000.00.",
                "states a figure",
            ),
            (
                "Approved. I confirmed your answer about drift against the record.",
                "compliance language",
            ),
            (
                "I couldn't verify the chunk_ref behind that answer.",
                "internal vocabulary",
            ),
            ("", "empty"),
        ],
    )
    def test_the_shared_rules_are_unchanged(self, reply, expected):
        ok, why = egress(reply)
        assert not ok
        assert why == expected

    def test_a_canary_is_still_caught(self):
        ok, why = is_safe_refusal(
            "I couldn't verify that answer. CANARY-XYZ",
            TEMPLATE,
            MESSAGE,
            "CANARY-XYZ",
            VOICE_UNVERIFIED,
        )
        assert not ok
        assert why == "canary"


class TestTheVerdictCarriesTheVoice:
    """Which voice to use is the blocking check's decision, not the pipeline's:
    only the check knows whether the refusal is about a draft that failed
    verification or about something that must not be named at all."""

    @pytest.mark.parametrize(
        ("text", "facts", "refs", "trigger"),
        [
            ("Your cash balance is $99,999.00.", {"cash": 12000.0}, frozenset(), "verify_figure"),
            (
                "You are eligible for p7.",
                {"eligiblePortfolios": ["P1", "P2"]},
                frozenset(),
                "verify_eligibility",
            ),
            ("Policy says so [chunk_ref=FAKE#9].", {}, frozenset(), "verify_citation"),
        ],
    )
    def test_the_verify_family_asks_for_the_unverified_voice(self, text, facts, refs, trigger):
        verdict = g.egress_verify(
            text=text, intent="policy_question", allowed_chunk_refs=refs, facts=facts
        )
        assert verdict.blocked
        assert verdict.triggered == (trigger,)
        assert verdict.refusal_voice == VOICE_UNVERIFIED

    def test_run_egress_does_not_drop_the_voice(self):
        """The runner repackages the verdict to accumulate `triggered`, which is
        exactly where a field added to the dataclass goes missing."""
        verdict = g.run_egress(
            text="Your cash balance is $99,999.00.",
            intent="policy_question",
            allowed_chunk_refs=frozenset(),
            facts={"cash": 12000.0},
        )
        assert verdict.blocked
        assert verdict.refusal_voice == VOICE_UNVERIFIED

    def test_a_leak_block_asks_for_no_voice(self):
        """Nothing about a leak may be named back to the client, so this one
        keeps its template verbatim -- the conservative default."""
        verdict = g.egress_leak(
            text=f"The secret is {g.CANARY}.", intent="", allowed_chunk_refs=frozenset()
        )
        if verdict.blocked:
            assert verdict.refusal_voice == ""

    def test_an_allowed_verdict_has_no_voice(self):
        assert g.ALLOW.refusal_voice == ""

    def test_the_default_is_the_conservative_one(self):
        """A check that says nothing about voice gets no rewrite. A new block
        added later is therefore never handed to a model by accident."""
        assert g.GuardrailVerdict(allowed=False, reason="x").refusal_voice == ""


class TestTheIngressProfileDidNotMove:
    def test_an_ordinary_scope_refusal_still_ships(self):
        ok, _ = ingress("I can't help with customer support hours. I can explain your portfolio.")
        assert ok

    def test_the_in_scope_rule_is_still_absolute_at_ingress(self):
        ok, why = ingress(
            "I can't help with your financial condition. I can explain your portfolio."
        )
        assert not ok
        assert why == "declines something in scope"

    def test_egress_phrasing_is_rejected_at_ingress(self):
        """The two voices are not interchangeable in either direction: a
        verification clause is not a decline under the ingress rules."""
        ok, why = ingress("I couldn't verify an answer about your portfolio drift.")
        assert not ok
        assert why == "does not decline"

    def test_the_two_prompts_are_different_documents(self):
        assert SYSTEM != SYSTEM_EGRESS
        assert "judged unsafe or out of scope" in SYSTEM
        assert "passed every check" in SYSTEM_EGRESS
        assert "NEVER say you cannot help with" in SYSTEM_EGRESS
