"""Exception hierarchy and its HTTP mapping.

Each LLM failure mode gets its own status and its own user-facing message so a
supervisor can tell a timeout from a rate limit from an outage (B.6). Responses
never carry a stack trace or internal detail (D.4).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.correlation import get_correlation_id


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "Something went wrong on our side."

    def __init__(self, message: str | None = None, *, detail: str | None = None):
        super().__init__(message or self.message)
        self.public_message = message or self.message
        self.detail = detail  # logged, never returned


class ValidationError(AppError):
    status_code, code = 422, "validation_error"
    message = "The request could not be processed as submitted."


class Forbidden(AppError):
    status_code, code = 403, "forbidden"
    message = "You do not have access to this resource."


class Unauthorized(AppError):
    status_code, code = 401, "unauthorized"
    message = "Sign in to continue."


class NotFound(AppError):
    status_code, code = 404, "not_found"
    message = "The requested resource does not exist."


class Conflict(AppError):
    status_code, code = 409, "conflict"
    message = "That action conflicts with the current state."


class PreviewStale(Conflict):
    """C.1: the confirmed trade set is not the one that was previewed."""

    code = "preview_stale"
    message = "That preview is no longer current. Review the updated trades and confirm again."


class PriceUnavailable(AppError):
    status_code, code = 422, "price_unavailable"
    message = "No price is available for one or more instruments on the selected session date."


class SuitabilityRefused(AppError):
    status_code, code = 403, "suitability_refused"
    message = "That portfolio is outside your assessed suitability range."


class ProfileIncoherent(AppError):
    status_code, code = 409, "profile_incoherent"
    message = "Your profile answers conflict, so a risk category cannot be assigned yet."


class ConfigurationError(AppError):
    """The server is set up wrongly -- not the caller's fault and not their business.

    Deliberately not a `ValidationError`: that type suppresses its detail, which
    is right for a request error and wrong here. An operator needs to read
    exactly which variable is missing, so the reason is the exception's own
    message (logs, startup, tests) while the client still sees only a generic
    unavailable state.
    """

    status_code, code = 503, "misconfigured"
    message = "The assistant is not configured correctly. Check the server logs."

    def __init__(self, reason: str):
        Exception.__init__(self, reason)
        self.public_message = self.message
        self.detail = reason


# --- LLM failure modes: distinct states, distinct messages (B.6) ------------
class LLMError(AppError):
    status_code, code = 503, "llm_unavailable"
    message = "The advisory assistant is unavailable. Your portfolio and history are unaffected."


class LLMTimeout(LLMError):
    status_code, code = 504, "llm_timeout"
    message = "The assistant took too long to respond. Please try again."


class LLMRateLimited(LLMError):
    status_code, code = 429, "llm_rate_limited"
    message = "The shared model endpoint is busy. Please retry shortly."

    def __init__(self, message: str | None = None, *, retry_after: float | None = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    status_code, code = 503, "llm_unreachable"
    message = "The assistant cannot be reached right now. Deterministic features still work."


class CircuitOpen(LLMError):
    status_code, code = 503, "llm_circuit_open"
    message = "The assistant is temporarily disabled after repeated failures. Retrying shortly."


def _body(exc: AppError) -> dict:
    return {
        "error": {
            "code": exc.code,
            "message": exc.public_message,
            "correlationId": get_correlation_id(),
        }
    }


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    headers = {}
    if isinstance(exc, LLMRateLimited) and exc.retry_after:
        headers["Retry-After"] = str(int(exc.retry_after))
    return JSONResponse(status_code=exc.status_code, content=_body(exc), headers=headers)


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    import logging

    logging.getLogger("app.unhandled").exception("unhandled exception", exc_info=exc)
    return JSONResponse(status_code=500, content=_body(AppError()))
