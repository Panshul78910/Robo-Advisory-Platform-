"""One OpenAI-compatible call, tried against each configured key in turn.

Three callers need the same thing -- the judges, the refusal rewriter and the
vagueness check -- and each had its own copy of the request. The copies drifted:
only one of them treated an empty completion as a failure. More to the point, a
key chain implemented three times is a key chain that works in two of them.

The chain is primary key, then backup key, then the caller's own fallback, which
is the local model for the judges and "do nothing" for the other two. A free
tier's rate limit is the case this exists for: it arrives as a fast 429, so the
second key costs about a hundred milliseconds. An expired or revoked key fails
just as fast and is covered by the same path.

A timeout is the exception -- it costs its full budget before the next key is
tried, so a provider that hangs can spend two timeouts before the local model
gets its turn. That is the price of not letting one exhausted key take the
assistant offline, and the timeouts are set small for this reason.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

log = logging.getLogger("app.llm.hosted")


class HostedUnavailable(RuntimeError):
    """Every configured key failed. The caller falls back however it falls back."""


@dataclass(frozen=True, slots=True)
class Credential:
    #: "primary" or "backup". Logged, so a run that quietly spent the whole
    #: demo on the backup key is visible rather than inferred from the bill.
    name: str
    key: str
    base_url: str


def credentials() -> tuple[Credential, ...]:
    """The keys to try, in order. Empty when nothing hosted is configured.

    `hosted_judge_enabled` is deliberately not consulted here. It means "force
    the judges local", and the judges check it themselves before they ever get
    this far. The refusal rewriter and the vagueness check are not judges and
    have no local model to be forced onto, so reading it here would silently
    switch them off instead.
    """
    s = get_settings()
    found: list[Credential] = []
    if s.judge_api_key:
        found.append(Credential("primary", s.judge_api_key, s.judge_base_url))
    if s.backup_api_key:
        # Blank backup URL means the same provider with a second key, which is
        # the usual arrangement. A different URL lets the backup be a different
        # provider -- in which case the model names have to exist there too, or
        # it 404s and the local fallback takes over.
        found.append(Credential("backup", s.backup_api_key, s.backup_base_url or s.judge_base_url))
    return tuple(found)


async def chat(body: dict, *, timeout: float, purpose: str) -> tuple[str, int, str]:
    """Completion text, elapsed ms, and which key answered.

    Raises `HostedUnavailable` once every key has failed, with the last error
    attached. Never returns an empty completion: empty parses as nothing and
    would be read as a verdict, so it counts as a provider failure and the next
    key gets its turn.
    """
    chain = credentials()
    if not chain:
        raise HostedUnavailable("no hosted credential configured")

    started = time.perf_counter()
    last = "no attempt made"
    for credential in chain:
        attempt = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{credential.base_url.rstrip('/')}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {credential.key}"},
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"].get("content")
            if not (content or "").strip():
                raise ValueError("empty completion")
        except Exception as exc:  # noqa: BLE001 -- any failure means: try the next key
            last = f"{exc.__class__.__name__}: {exc}"[:200]
            log.warning(
                "hosted key failed",
                extra={
                    "purpose": purpose,
                    "credential": credential.name,
                    "model": body.get("model"),
                    "status": getattr(getattr(exc, "response", None), "status_code", None),
                    "error": exc.__class__.__name__,
                    "detail": str(exc)[:160],
                    "latency_ms": int((time.perf_counter() - attempt) * 1000),
                    "keys_remaining": len(chain) - chain.index(credential) - 1,
                },
            )
            continue
        if credential.name != "primary":
            log.info(
                "hosted call served by backup key",
                extra={
                    "purpose": purpose,
                    "credential": credential.name,
                    "model": body.get("model"),
                },
            )
        return content, int((time.perf_counter() - started) * 1000), credential.name

    raise HostedUnavailable(last)
