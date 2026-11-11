"""Hybrid retrieval with a relevance floor (B.4).

Dense search alone is weak on rare literal tokens -- and the policy corpus is
full of them: category names like *Capital Preservation*, terms like *satellite
exposure* and *CKA*, explicit appendix references. Those are exactly what a client
asks about. So a keyword arm runs alongside the vector arm and the two are fused
by reciprocal rank fusion.

Similarity scores are first-class output, not an internal detail: the relevance
floor below is what turns an ungroundable question into an honest abstention
instead of a fluent answer generated from parametric knowledge (C.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import get_settings
from app.core.timing import stage
from app.rag.embedder import embedder_identity, get_embedder

log = logging.getLogger("app.rag.retrieve")

#: RRF damping. 60 is the value from the original Cormack et al. formulation.
RRF_K = 60

#: corpus_version id -> mismatch description (or None). Checked once each.
_MISMATCH_CHECKED: dict = {}


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_ref: str
    doc_id: str
    title: str
    section: str | None
    page: int | None
    text: str
    score: Decimal

    def to_citation(self) -> dict:
        """Field names for `PolicyChunkDto`, which owns the wire format.

        Deliberately snake_case: the DTO carries `serialization_alias="docId"`,
        and a serialization alias does not apply on input. Emitting camelCase
        here made `ChatAnswerDto` reject every citation with "Field required:
        doc_id" -- so `/api/chat` returned 500 whenever retrieval actually
        grounded, and only looked healthy while it was abstaining.
        """
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "section": self.section,
            "page": self.page,
            "text": self.text,
            "chunk_ref": self.chunk_ref,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: tuple[RetrievedChunk, ...]
    grounded: bool
    #: Best fused score seen before the floor was applied, for diagnostics.
    top_score: Decimal | None = None

    def __bool__(self) -> bool:
        return self.grounded


DENSE_SQL = sa.text(
    """
    select c.id, c.chunk_ref, d.doc_id, d.title, c.section, c.page, c.text,
           1 - (c.embedding <=> cast(:query_vector as vector)) as similarity
      from chunk c
      join document d on d.id = c.document_id
     where c.embedding is not null
       -- cast required: Postgres cannot infer a parameter's type when it
       -- is only compared to NULL, and raises AmbiguousParameter.
       and (cast(:audience as text) is null or c.audience = cast(:audience as text))
       and d.corpus_version_id = :corpus_version_id
     order by c.embedding <=> cast(:query_vector as vector)
     limit :limit
    """
)

KEYWORD_SQL = sa.text(
    """
    select c.id, c.chunk_ref, d.doc_id, d.title, c.section, c.page, c.text,
           ts_rank_cd(c.tsv, websearch_to_tsquery('english', :query)) as rank
      from chunk c
      join document d on d.id = c.document_id
     where c.tsv @@ websearch_to_tsquery('english', :query)
       -- cast required: Postgres cannot infer a parameter's type when it
       -- is only compared to NULL, and raises AmbiguousParameter.
       and (cast(:audience as text) is null or c.audience = cast(:audience as text))
       and d.corpus_version_id = :corpus_version_id
     order by rank desc
     limit :limit
    """
)


async def warn_on_embedder_mismatch(conn: AsyncConnection, corpus_version_id) -> str | None:
    """Shout if the index was built by a different embedder than we query with.

    Vectors from two models are not comparable: every similarity collapses
    towards zero, nothing clears the relevance floor, and the system abstains on
    every question while looking like it is merely being cautious. Checked once
    per corpus version, then cached.
    """
    if corpus_version_id in _MISMATCH_CHECKED:
        return _MISMATCH_CHECKED[corpus_version_id]

    note = (
        await conn.execute(
            sa.text("select note from corpus_version where id = :id"),
            {"id": corpus_version_id},
        )
    ).scalar_one_or_none() or ""
    recorded = next(
        (part.split("=", 1)[1] for part in note.split("|") if "embedder=" in part), None
    )
    current = embedder_identity()
    problem = None
    if recorded is None:
        problem = "this corpus predates embedder stamping, so a mismatch cannot be ruled out"
    elif recorded.strip() != current:
        problem = f"index built with {recorded.strip()!r} but querying with {current!r}"

    if problem:
        log.error(
            "EMBEDDER MISMATCH -- retrieval will not ground anything. Re-ingest the "
            "corpus with the current embedder. %s",
            problem,
        )
    _MISMATCH_CHECKED[corpus_version_id] = problem
    return problem


async def active_corpus_version(conn: AsyncConnection):
    """Newest corpus version. Re-ingesting creates a new one rather than
    overwriting, so old citations stay resolvable."""
    return (
        await conn.execute(
            sa.text("select id from corpus_version order by created_at desc limit 1")
        )
    ).scalar_one_or_none()


_TOPICS_CACHED: dict = {}


async def corpus_topics(conn: AsyncConnection) -> tuple[str, ...]:
    """Titles of the documents in the active corpus.

    The semantic judge decides whether a question is in scope, and it was doing
    so against a topic list hardcoded in its prompt. That list and the corpus are
    two answers to the same question -- "what is this assistant about?" -- and
    they drifted the moment new documents were ingested: questions about fair
    dealing were judged off-topic minutes after the fair dealing guidelines were
    indexed. Feeding the judge the real document titles keeps scope and corpus in
    step with nothing to maintain.

    Cached per corpus version; ingesting creates a new version, so a new corpus
    refreshes this without a restart.
    """
    version_id = await active_corpus_version(conn)
    if version_id is None:
        return ()
    if version_id in _TOPICS_CACHED:
        return _TOPICS_CACHED[version_id]
    rows = (
        await conn.execute(
            sa.text(
                "select distinct title from document where corpus_version_id = :v order by title"
            ),
            {"v": version_id},
        )
    ).scalars()
    topics = tuple(t for t in rows if t)
    _TOPICS_CACHED[version_id] = topics
    return topics


class Retriever:
    def __init__(self, conn: AsyncConnection, *, audience: str | None = "client"):
        """`audience='client'` is the default and is never widened for a client
        request -- it is the second, independent barrier around admin-only
        material, alongside excluding it at ingestion."""
        self._conn = conn
        self._audience = audience
        self._settings = get_settings()

    async def search(
        self, query: str, *, top_k: int | None = None, apply_floor: bool = True
    ) -> RetrievalResult:
        if not self._settings.rag_enabled:
            # D.1(1): grounding removed deliberately. Logged loudly because an
            # ungrounded answer is indistinguishable from a grounded one in the
            # response, and this must never be mistaken for a retrieval failure.
            log.warning("retrieval disabled by RAG_ENABLED=false; answering without evidence")
            return RetrievalResult(chunks=(), grounded=False)

        top_k = top_k or self._settings.retrieval_top_k
        corpus_version_id = await active_corpus_version(self._conn)
        if corpus_version_id is None:
            log.warning("retrieval attempted with no ingested corpus")
            return RetrievalResult(chunks=(), grounded=False)

        await warn_on_embedder_mismatch(self._conn, corpus_version_id)

        candidates = self._settings.retrieval_candidates
        params = {
            "audience": self._audience,
            "corpus_version_id": corpus_version_id,
            "limit": candidates,
        }

        with stage("retrieval.embed_query"):
            vector = get_embedder().encode([query])[0]
        with stage("retrieval.dense_sql"):
            dense = (
                (await self._conn.execute(DENSE_SQL, {**params, "query_vector": _pgvector(vector)}))
                .mappings()
                .all()
            )
        with stage("retrieval.keyword_sql"):
            keyword = (
                (await self._conn.execute(KEYWORD_SQL, {**params, "query": query})).mappings().all()
            )

        with stage("retrieval.fuse"):
            fused = self._fuse(dense, keyword)
        if not fused:
            return RetrievalResult(chunks=(), grounded=False)

        # Relevance is judged on actual cosine similarity, never on the fused
        # rank. RRF scores positions, and the dense arm always returns top-k
        # however irrelevant the corpus is -- so its best hit always lands at
        # rank 1 and scores a constant 0.5. Gating on that, the floor could
        # never fire and `grounded` was true for literally any query.
        #
        # A full-text hit is different: the policy wording itself matched. The
        # hash embedder's cosine is not semantic, so without this a question
        # that quotes the rebalancing section is reported as unsupported.
        similarity = {row["id"]: Decimal(str(round(float(row["similarity"]), 5))) for row in dense}
        keyword_ids = {row["id"] for row in keyword}

        top_score = fused[0][1]
        floor = self._settings.relevance_floor
        kept, best_similarity, keyword_grounded = _keep(
            fused, keyword_ids, similarity, floor=floor, top_k=top_k, apply_floor=apply_floor
        )

        if not kept:
            # Nothing cleared the floor. Returning the least-bad matches here is
            # how a system ends up citing an irrelevant passage as if it were
            # authority; returning nothing lets the caller abstain honestly.
            log.info(
                "retrieval below relevance floor",
                extra={
                    "best_similarity": float(best_similarity),
                    "fused_top": float(top_score),
                    "floor": float(floor),
                },
            )
            return RetrievalResult(chunks=(), grounded=False, top_score=best_similarity)

        # B.4(1) evidence: a semantic search ran, these passages came back, and
        # this is what each scored. Logged on success as well as failure --
        # previously only the floor's rejection was recorded, so a *working*
        # retrieval left no trace at all and the layer could only be observed
        # through an admin endpoint.
        log.info(
            "retrieval complete",
            extra={
                "dense_candidates": len(dense),
                "keyword_candidates": len(keyword),
                "fused_candidates": len(fused),
                "kept": len(kept),
                "top_k": top_k,
                "relevance_floor": float(floor),
                "best_similarity": float(best_similarity),
                "floor_applied": apply_floor,
                # `title` is deliberately absent: `chunk_ref` already names the
                # document, and carrying both pushed `section` -- the field that
                # actually explains *why* a chunk matched -- off a terminal line.
                "chunks": [
                    {
                        "chunk_ref": row["chunk_ref"],
                        "similarity": float(similarity.get(row["id"], Decimal(0))),
                        "fused_score": round(float(score), 5),
                        "section": row["section"],
                    }
                    for row, score in kept
                ],
            },
        )

        return RetrievalResult(
            chunks=tuple(
                RetrievedChunk(
                    chunk_ref=row["chunk_ref"],
                    doc_id=row["doc_id"],
                    title=row["title"],
                    section=row["section"],
                    page=row["page"],
                    text=row["text"],
                    score=score if keyword_grounded else similarity.get(row["id"], score),
                )
                for row, score in kept
            ),
            grounded=True,
            top_score=best_similarity,
        )

    @staticmethod
    def _fuse(dense, keyword) -> list[tuple]:
        """Reciprocal rank fusion, normalised to [0,1] so the floor is comparable
        across queries regardless of how many arms matched."""
        scores: dict = {}
        rows: dict = {}
        for result_set in (dense, keyword):
            for rank, row in enumerate(result_set, start=1):
                key = row["id"]
                rows[key] = row
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)

        if not scores:
            return []
        # Best possible score: rank 1 in both arms.
        ceiling = 2.0 / (RRF_K + 1)
        return sorted(
            (
                (rows[key], Decimal(str(round(min(score / ceiling, 1.0), 5))))
                for key, score in scores.items()
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )


def _keep(fused, keyword_ids, similarity, *, floor, top_k, apply_floor):
    """Hits to return, the best cosine score, and whether text search saved them."""
    top = fused[:top_k]
    best_similarity = max(
        (similarity.get(row["id"], Decimal(0)) for row, _ in top), default=Decimal(0)
    )
    if not apply_floor:
        return top, best_similarity, False
    if best_similarity >= floor:
        # The floor is per passage, not per result set. It used to be asked only
        # of the best hit, and if that one cleared, all `top_k` went through
        # unexamined -- so `top_k` behaved as a quota the retriever had to fill
        # rather than a cap. Measured on "why was I assigned this risk
        # category?", one chunk at 0.777 carried five others at 0.35-0.38 into
        # the prompt, and on "is SPY one of the instruments you use?" a 0.345
        # hit carried passages down to 0.20. The model is given no scores, so it
        # cannot tell which of the six it was handed are worth reading.
        #
        # A keyword hit is kept whatever its cosine: the policy wording itself
        # matched, which is the whole reason for the second arm, and a
        # keyword-only chunk has no cosine to test.
        kept = [
            (row, score)
            for row, score in top
            if similarity.get(row["id"], Decimal(0)) >= floor or row["id"] in keyword_ids
        ]
        return kept, best_similarity, False
    keyword_kept = [(row, score) for row, score in top if row["id"] in keyword_ids]
    if not keyword_kept:
        return [], best_similarity, False
    # A literal policy match is strong evidence. The fused rank of a keyword-only
    # hit tops out at 0.5, which would still be flagged for review.
    boosted = [(row, max(score, Decimal("0.86"))) for row, score in keyword_kept]
    return boosted, best_similarity, True


def _pgvector(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"
