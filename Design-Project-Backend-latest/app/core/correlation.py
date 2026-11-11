"""Per-request correlation id, visible in the frontend, the engineering log and the audit row."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

HEADER = "X-Correlation-Id"

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(HEADER)
        cid = incoming or uuid.uuid4().hex
        set_correlation_id(cid)
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers[HEADER] = cid
        return response
