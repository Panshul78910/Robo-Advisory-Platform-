"""Run the API. Use this rather than `uvicorn app.main:app` directly.

Why a launcher exists at all: uvicorn creates its event loop *before* importing
the application, so the policy call at the top of `app/main.py` lands inside an
already-running loop and has no effect. On Windows that leaves psycopg on the
ProactorEventLoop it cannot use, and every database-backed endpoint fails with
`InterfaceError` -- the API comes up, `/healthz` reports `db: false`, and every
real request 500s.

Setting the policy here, before `uvicorn.run`, is early enough.

    python -m scripts.serve [--port 8000] [--reload]
"""

from __future__ import annotations

import argparse

from app.core.eventloop import use_compatible_event_loop

# Must precede the uvicorn import and call: it is what decides which loop
# implementation `uvicorn.run` will construct.
use_compatible_event_loop()


def main() -> int:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        # The whole reason this launcher exists: uvicorn would otherwise pick
        # ProactorEventLoop on Windows, which psycopg cannot use.
        loop="app.core.eventloop:selector_event_loop",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
