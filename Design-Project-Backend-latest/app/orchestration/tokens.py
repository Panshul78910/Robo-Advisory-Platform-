"""Token counting for the context builder.

Uses the real tokenizer of whichever model will actually serve the request;
otherwise a deliberately *over*-counting heuristic. Erring high means the
builder degrades slightly too early, which costs a retrieved chunk. Erring low
would mean the endpoint rejects the request, or silently truncates the system
prompt -- the one failure mode the whole budget scheme exists to prevent.
"""

from __future__ import annotations

import functools
import logging
import math

log = logging.getLogger("app.orchestration.tokens")

#: English averages ~4 characters per token. 3.2 over-counts by roughly 25%,
#: absorbing the delimiter and JSON punctuation that tokenises badly.
CHARS_PER_TOKEN = 3.2
#: Additional safety margin applied to the heuristic total.
HEURISTIC_MARGIN = 1.10
#: Used when tiktoken does not recognise the model name. Correct for the current
#: GPT-4o and GPT-5 families, and close enough for anything newer.
FALLBACK_ENCODING = "o200k_base"

#: Course endpoint model names -> the Hugging Face repo carrying their tokenizer.
#: The endpoint names its models its own way, and those names are not repo ids, so
#: without this the lookup raises `OSError` and counting silently drops to the
#: heuristic -- which over-counts by roughly 1.9x and makes the context builder
#: degrade earlier than it needs to.
#:
#: Only ungated repos are listed. Qwen's are Apache-2.0 and download with no
#: account; Google's Gemma repos are gated, so `gemma-3-12b` is deliberately
#: absent and keeps the heuristic until someone supplies an `HF_TOKEN` with the
#: licence accepted. A quantisation suffix (`-fp8`) does not change the
#: tokenizer, so it maps to the base repo.
TOKENIZER_REPOS: dict[str, str] = {
    "qwen3-4b": "Qwen/Qwen3-4B",
    "qwen3.6-35b-a3b-fp8": "Qwen/Qwen3.6-35B-A3B",
    "qwen3.6-35b-a3b": "Qwen/Qwen3.6-35B-A3B",
}


def _active_model() -> tuple[str, str]:
    """`(backend, model)` for the endpoint that will serve the request.

    Previously this read `hpc_model_name` unconditionally, so running on OpenAI
    counted tokens against a Gemma tokenizer -- or, as the name is not a Hugging
    Face repo id, against nothing at all, falling back to the heuristic.
    """
    from app.core.config import get_settings

    settings = get_settings()
    if settings.llm_backend == "openai":
        return "openai", settings.openai_model
    return settings.llm_backend, settings.hpc_model_name


@functools.lru_cache(maxsize=1)
def _tokenizer():
    backend, name = _active_model()
    if not name:
        return None
    try:
        if backend == "openai":
            import tiktoken

            try:
                return tiktoken.encoding_for_model(name)
            except KeyError:
                # A model newer than this tiktoken release. The encoding is what
                # matters, not the name, so fall back to it rather than to the
                # heuristic -- still exact for every current OpenAI model.
                return tiktoken.get_encoding(FALLBACK_ENCODING)

        from transformers import AutoTokenizer  # type: ignore

        # An unmapped name is still tried verbatim: a future endpoint model might
        # be named after its repo, and a miss costs only the usual fallback.
        return AutoTokenizer.from_pretrained(TOKENIZER_REPOS.get(name.lower(), name))
    except Exception as exc:
        log.info(
            "no tokenizer available; using the conservative heuristic",
            extra={"backend": backend, "model": name, "reason": type(exc).__name__},
        )
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    tokenizer = _tokenizer()
    if tokenizer is not None:
        return len(_encode(tokenizer, text))
    return math.ceil(len(text) / CHARS_PER_TOKEN * HEURISTIC_MARGIN)


def _encode(tokenizer, text: str) -> list[int]:
    # tiktoken has no `add_special_tokens`; transformers needs it off, since the
    # builder counts fragments that are concatenated before a single request.
    if hasattr(tokenizer, "encode_ordinary"):
        return tokenizer.encode_ordinary(text)
    return tokenizer.encode(text, add_special_tokens=False)


def counting_method() -> str:
    return "tokenizer" if _tokenizer() is not None else "heuristic"
