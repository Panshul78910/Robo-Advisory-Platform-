"""The relevance floor is asked of every passage, not just the best one.

`_keep` used to take the top `top_k` by fused rank and test only the strongest
cosine among them. If that one cleared, all of them went through unexamined, so
`top_k` behaved as a quota the retriever had to fill rather than a ceiling it
must not exceed.

Measured on the live corpus before the change: "why was I assigned this risk
category?" returned one passage at 0.777 and five between 0.354 and 0.377, and
"is SPY one of the instruments you use?" returned a 0.345 hit carrying five
more down to 0.20. The model is handed these six identically formatted, with no
scores, so it cannot tell which are worth reading.
"""

from __future__ import annotations

from decimal import Decimal

from app.rag.retrieve import _keep

FLOOR = Decimal("0.34")


def row(chunk_id: int):
    return {"id": chunk_id, "chunk_ref": f"DOC#{chunk_id}"}


def scenario(*cosines: str):
    """Chunks in fused order, each with the given cosine and no keyword hit."""
    fused = [(row(i), Decimal("0.5")) for i, _ in enumerate(cosines)]
    similarity = {i: Decimal(c) for i, c in enumerate(cosines)}
    return fused, similarity


def kept_ids(*cosines: str, keyword_ids=frozenset()):
    fused, similarity = scenario(*cosines)
    kept, _, _ = _keep(
        fused, keyword_ids, similarity, floor=FLOOR, top_k=6, apply_floor=True
    )
    return [r["id"] for r, _ in kept]


class TestOneStrongHitNoLongerCarriesTheRest:
    def test_the_risk_category_shape(self):
        """One relevant passage, five that merely cleared the floor, all kept --
        the floor is doing its job on each of them."""
        assert kept_ids("0.777", "0.377", "0.374", "0.369", "0.360", "0.354") == [
            0, 1, 2, 3, 4, 5
        ]

    def test_the_spy_shape(self):
        """0.345 clears; 0.31 and below do not. Before, all six went through."""
        assert kept_ids("0.345", "0.31", "0.29", "0.22", "0.20", "0.20") == [0]

    def test_a_single_good_hit_among_noise(self):
        assert kept_ids("0.82", "0.11", "0.09", "0.05") == [0]

    def test_a_genuine_plateau_is_untouched(self):
        """Six comparable passages are six real sources, not padding."""
        assert kept_ids("0.488", "0.47", "0.47", "0.46", "0.44", "0.43") == [
            0, 1, 2, 3, 4, 5
        ]


class TestTheContractThatHeld:
    def test_a_set_below_the_floor_still_returns_nothing(self):
        fused, similarity = scenario("0.30", "0.22", "0.10")
        kept, best, rescued = _keep(
            fused, frozenset(), similarity, floor=FLOOR, top_k=6, apply_floor=True
        )
        assert kept == [] and not rescued and best == Decimal("0.30")

    def test_the_keyword_rescue_still_fires_and_boosts(self):
        fused, similarity = scenario("0.30", "0.22")
        kept, _, rescued = _keep(
            fused, {1}, similarity, floor=FLOOR, top_k=6, apply_floor=True
        )
        assert rescued
        assert [r["id"] for r, _ in kept] == [1]
        assert all(score >= Decimal("0.86") for _, score in kept)

    def test_a_keyword_hit_survives_a_weak_cosine(self):
        """The policy wording itself matched, which is the second arm's whole
        purpose; a keyword-only chunk has no cosine to test at all."""
        assert kept_ids("0.82", "0.04", "0.03", keyword_ids={1}) == [0, 1]

    def test_apply_floor_false_is_still_unfiltered(self):
        fused, similarity = scenario("0.82", "0.04", "0.03")
        kept, _, _ = _keep(
            fused, frozenset(), similarity, floor=FLOOR, top_k=6, apply_floor=False
        )
        assert [r["id"] for r, _ in kept] == [0, 1, 2]

    def test_top_k_is_still_a_ceiling(self):
        fused, similarity = scenario(*(["0.90"] * 9))
        kept, _, _ = _keep(
            fused, frozenset(), similarity, floor=FLOOR, top_k=6, apply_floor=True
        )
        assert len(kept) == 6

    def test_a_chunk_exactly_on_the_floor_is_kept(self):
        assert kept_ids("0.90", "0.34") == [0, 1]
