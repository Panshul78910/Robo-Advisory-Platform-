"""Windows event-loop selection.

psycopg's async mode cannot run on the `ProactorEventLoop` that Python selects
by default on Windows -- it raises `InterfaceError` on the first connection, so
every DB-backed endpoint fails. Switching to the selector loop fixes it.

This has to happen *before* any loop is created, which is why it is called at
import time from `app/main.py` (uvicorn imports the app module before starting
its loop) and from any script that opens an async connection.

No effect on Linux or macOS, where the default loop is already compatible.
"""

from __future__ import annotations

import asyncio
import sys


def use_compatible_event_loop() -> bool:
    """Select the selector loop on Windows. Returns True if it changed anything."""
    if sys.platform != "win32":
        return False
    policy = asyncio.get_event_loop_policy()
    if isinstance(policy, asyncio.WindowsSelectorEventLoopPolicy):
        return False
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return True


def selector_event_loop() -> asyncio.AbstractEventLoop:
    """Loop factory for uvicorn: `--loop app.core.eventloop:selector_event_loop`.

    uvicorn 0.36+ builds its loop from a factory rather than the asyncio policy,
    so `use_compatible_event_loop()` above cannot reach it -- and uvicorn's own
    factory picks `ProactorEventLoop` on Windows deliberately, except when
    `--reload` is set:

        if sys.platform == "win32" and not use_subprocess:
            return asyncio.ProactorEventLoop

    psycopg's async mode refuses that loop, so the server starts, `/healthz`
    reports `db: false`, and every database-backed request 500s -- with reload
    on, by luck, it works. This factory removes the difference.

    Safe on every platform: SelectorEventLoop is already the default off Windows.
    """
    return asyncio.SelectorEventLoop()
