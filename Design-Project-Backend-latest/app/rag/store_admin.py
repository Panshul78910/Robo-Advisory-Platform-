"""Vector store management (B.4), exposed as admin endpoints rather than scripts.

Endpoints rather than a terminal session means the store can be inspected and
repaired during a demonstration, and that management operations are subject to
the same authentication and logging as everything else.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.rag.retrieve import Retriever, active_corpus_version


async def stats(conn: AsyncConnection) -> dict:
    rows = (
        (
            await conn.execute(
                sa.text(
                    """
                select cv.label,
                       cv.created_at,
                       count(distinct d.id) as documents,
                       count(c.id)          as chunks,
                       count(c.id) filter (where c.audience = 'client') as client_chunks,
                       count(c.id) filter (where c.embedding is null)   as unembedded
                  from corpus_version cv
                  left join document d on d.corpus_version_id = cv.id
                  left join chunk c    on c.document_id = d.id
                 group by cv.id, cv.label, cv.created_at
                 order by cv.created_at desc
                """
                )
            )
        )
        .mappings()
        .all()
    )
    active = await active_corpus_version(conn)
    return {
        "activeCorpusVersionId": str(active) if active else None,
        "versions": [
            {
                "label": r["label"],
                "createdAt": r["created_at"],
                "documents": r["documents"],
                "chunks": r["chunks"],
                "clientVisibleChunks": r["client_chunks"],
                "unembedded": r["unembedded"],
            }
            for r in rows
        ],
    }


async def query(
    conn: AsyncConnection, text: str, *, top_k: int = 10, audience: str | None = None
) -> dict:
    """Arbitrary query against the store, returning ranked results *with scores*.

    The relevance floor is not applied here: an admin inspecting the store needs
    to see what was rejected and why, which is precisely what the floor hides
    from a client.
    """
    retriever = Retriever(conn, audience=audience)
    result = await retriever.search(text, top_k=top_k, apply_floor=False)
    return {
        "query": text,
        "audienceFilter": audience,
        "grounded": result.grounded,
        "topScore": float(result.top_score) if result.top_score is not None else None,
        "results": [
            {
                "chunkRef": c.chunk_ref,
                "score": float(c.score),
                "docId": c.doc_id,
                "title": c.title,
                "section": c.section,
                "page": c.page,
                "text": c.text,
            }
            for c in result.chunks
        ],
    }


async def reset(conn: AsyncConnection) -> dict:
    """Clear every corpus version. Chunks and documents cascade."""
    deleted = (await conn.execute(sa.text("delete from corpus_version"))).rowcount
    return {"deletedCorpusVersions": deleted}
