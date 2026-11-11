"""Local embeddings (B.3).

Runs on CPU, in-process. Ingesting the corpus is several hundred embedding calls;
spending a scarce shared HPC allocation on a batch job a local model handles in
under a minute would be poor engineering as well as poor citizenship. It also
means ingestion works offline and can be re-run at any time.

The `hash` backend is a deterministic, dependency-free stand-in so tests and CI
never need to download a 90MB model.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import math
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger("app.rag.embedder")

DIMENSIONS = 384  # must match `vector(384)` in 006_session_and_rag.sql


def _dimension_of(model) -> int:
    """Vector width, across the sentence-transformers rename.

    v5 renamed `get_sentence_embedding_dimension` to `get_embedding_dimension`
    and the old name now emits a FutureWarning. Both are supported so the
    dimension check below keeps working either side of the upgrade.
    """
    getter = getattr(model, "get_embedding_dimension", None) or (
        model.get_sentence_embedding_dimension
    )
    return int(getter())


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """sentence-transformers all-MiniLM-L6-v2, loaded lazily on first use."""

    def __init__(self, model_name: str):
        self._model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # heavy import

            log.info("loading embedding model", extra={"model": self._model_name})
            self._model = SentenceTransformer(self._model_name, device="cpu")
            actual = _dimension_of(self._model)
            if actual != DIMENSIONS:
                raise ValueError(
                    f"{self._model_name} produces {actual}-dim vectors, but the chunk "
                    f"table declares vector({DIMENSIONS}). Change both together."
                )
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._load().encode(
            texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
        )
        return [v.tolist() for v in vectors]


class HashEmbedder:
    """Deterministic bag-of-tokens hashing. Not semantic -- for tests only.

    Real synonym matching is what the local model provides; this exists so the
    ingestion and retrieval *plumbing* can be tested without torch.
    """

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for token in text.lower().split():
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % DIMENSIONS
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


@functools.lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_backend == "hash":
        return HashEmbedder()
    return LocalEmbedder(settings.embedding_model)


def embedder_identity() -> str:
    """Which embedder is in use, recorded with every corpus version.

    Vectors are only comparable to vectors from the same model. Without this
    recorded, an index built by one embedder and queried by another returns
    near-zero similarity for *everything* -- which looks like "retrieval is
    poor", not "the index is unusable", and silently defeats the relevance
    floor and C.4's abstention.
    """
    settings = get_settings()
    if settings.embedding_backend == "hash":
        return f"hash:{DIMENSIONS}"
    return f"local:{settings.embedding_model}"
