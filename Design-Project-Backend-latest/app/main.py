from __future__ import annotations

from app.core.eventloop import use_compatible_event_loop

# Must run before anything creates an event loop: on Windows the default
# ProactorEventLoop is incompatible with psycopg's async mode, and every
# database-backed endpoint would fail on its first connection.
use_compatible_event_loop()

from contextlib import asynccontextmanager  # noqa: E402

import sqlalchemy as sa  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from app.api.routers import admin, auth, chat, clients, market, portfolio, profiling, session  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.correlation import CorrelationMiddleware, get_correlation_id  # noqa: E402
from app.core.errors import AppError, app_error_handler, unhandled_error_handler  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.core.version import prompt_version  # noqa: E402
from app.db.session import dispose_engine, get_engine  # noqa: E402
from app.llm.faults import FaultInjectionMiddleware  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(
        settings.log_level, access_log=settings.access_log, log_format=settings.log_format
    )
    prompt_version()  # load and hash templates once, at startup
    yield
    await dispose_engine()


app = FastAPI(
    title="RoboAdvisor API",
    version="0.1.0",
    summary="Middle tier for the SC3098 simulated robo-advisory platform.",
    lifespan=lifespan,
)

app.add_middleware(CorrelationMiddleware)
# Inert unless ALLOW_FAULT_INJECTION is set; see app/llm/faults.py.
app.add_middleware(FaultInjectionMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-Id"],
)

app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: object, exc: RequestValidationError) -> JSONResponse:
    """Same error shape as the rest of the API, with the length message kept."""
    message = "Some of the values aren't valid. Please check them and try again."
    for err in exc.errors():
        text = str(err.get("msg") or "")
        if "too long" in text.lower() or "shorten" in text.lower():
            message = text.split(", ", 1)[-1]
            break
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": message,
                "correlationId": get_correlation_id(),
            }
        },
    )

for router in (auth, session, market, clients, portfolio, profiling, chat, admin):
    app.include_router(router.router, prefix="/api")


@app.get("/healthz", tags=["ops"])
async def healthz() -> dict:
    settings = get_settings()
    try:
        async with get_engine().connect() as conn:
            await conn.execute(sa.text("select 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "llm_backend": settings.llm_backend,
    }
