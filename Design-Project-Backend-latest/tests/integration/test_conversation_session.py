"""A chat session ends when it goes idle (A.2, D.4(2), B.7).

Continuity inside a session is required: the profiling interview keeps its place
in `conversation.state`, and the ingress judge reads prior turns to catch a probe
spread across several of them. What was missing was an *end*. "Most recent
conversation" meant most recent ever, so one conversation accumulated for days
and a question asked in one sitting was judged against turns from another --
which surfaced as ordinary questions being refused as off-topic.

These tests need aged rows, which is exactly why the behaviour went unnoticed:
every other test runs against a fresh database where nothing is ever stale.
"""

from __future__ import annotations

import datetime as _dt

import sqlalchemy as sa

from app.core.clock import WallClock
from app.core.config import get_settings
from app.db import models as m


async def _age_conversation(conn, conversation_id, minutes: int) -> None:
    """Backdate a conversation and every message in it."""
    when = WallClock.now() - _dt.timedelta(minutes=minutes)
    await conn.execute(
        sa.update(m.conversation)
        .where(m.conversation.c.id == conversation_id)
        .values(created_at=when, updated_at=when)
    )
    await conn.execute(
        sa.update(m.message)
        .where(m.message.c.conversation_id == conversation_id)
        .values(created_at=when)
    )


class TestIdleSessionsEnd:
    async def test_a_second_request_joins_the_same_conversation(self, conn, clients):
        first = await clients.open_conversation("advisory")
        second = await clients.open_conversation("advisory")
        assert first == second, "an active session must stay continuous"

    async def test_an_idle_conversation_is_not_resumed(self, conn, clients):
        stale = await clients.open_conversation("advisory")
        await _age_conversation(conn, stale, get_settings().conversation_idle_minutes + 5)

        fresh = await clients.open_conversation("advisory")
        assert fresh != stale, "a conversation idle past the window must not be resumed"

    async def test_a_recent_conversation_is_still_resumed(self, conn, clients):
        active = await clients.open_conversation("advisory")
        await _age_conversation(conn, active, max(1, get_settings().conversation_idle_minutes - 5))

        assert await clients.open_conversation("advisory") == active

    async def test_recent_messages_keep_a_session_alive(self, conn, clients):
        """Activity is measured from the last message, not `updated_at`.

        Only `save_conversation_state` writes that column, so an advisory
        conversation busy all afternoon still carries its creation time. Keying
        the window off it would expire a conversation mid-use.
        """
        conversation = await clients.open_conversation("advisory")
        await _age_conversation(conn, conversation, get_settings().conversation_idle_minutes + 60)
        await conn.execute(
            sa.insert(m.message).values(
                conversation_id=conversation, role="user", content="still here", seq=1
            )
        )

        assert await clients.open_conversation("advisory") == conversation

    async def test_an_idle_profiling_session_also_ends(self, conn, clients):
        """The window is not advisory-only: a half-finished interview from
        yesterday should not silently continue today."""
        stale = await clients.open_conversation("profiling")
        await _age_conversation(conn, stale, get_settings().conversation_idle_minutes + 5)

        assert await clients.open_conversation("profiling") != stale

    async def test_kinds_do_not_resume_each_other(self, conn, clients):
        advisory = await clients.open_conversation("advisory")
        profiling = await clients.open_conversation("profiling")
        assert advisory != profiling
