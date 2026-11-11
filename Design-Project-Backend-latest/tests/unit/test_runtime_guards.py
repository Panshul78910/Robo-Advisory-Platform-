"""Guards for two failures that are invisible until runtime, on one platform or
one database target.

Neither shows up in ordinary development: the event-loop fault only bites on
Windows, and the prepared-statement fault only against Supabase's transaction
pooler. Both were found by running the real thing, so they are pinned here.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Only this module may open a raw psycopg connection; everything else goes
#: through `connect_sync`, which applies the target's required driver options.
CONNECT_OWNER = ROOT / "app" / "db" / "session.py"


class TestEventLoopSelection:
    """psycopg's async mode cannot run on Windows' default ProactorEventLoop.

    Without the switch, every database-backed endpoint fails on its first
    connection with `InterfaceError` -- so the whole API is dead on Windows.
    """

    def test_main_selects_the_loop_before_importing_anything_async(self):
        """It must run at import time, before a loop can exist."""
        source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        call_line = next(
            (
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "use_compatible_event_loop"
            ),
            None,
        )
        assert call_line is not None, "app/main.py must call use_compatible_event_loop()"

        later_imports = [
            node.lineno
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno > call_line
        ]
        assert later_imports, (
            "the call should precede the app imports, so the policy is set before "
            "anything can create a loop"
        )

    def test_the_server_is_launched_through_the_policy_setting_wrapper(self):
        """`uvicorn app.main:app` is not enough.

        uvicorn builds its event loop before importing the app, so the policy
        call in main.py runs too late and psycopg is left on ProactorEventLoop.
        The API starts, /healthz reports db:false, and every request 500s.
        """
        tree = ast.parse((ROOT / "scripts" / "serve.py").read_text(encoding="utf-8"))
        # Line numbers from the AST, not string positions: the docstring
        # mentions both names and would otherwise decide the comparison.
        policy = next(
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "use_compatible_event_loop"
        )
        run = next(
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run"
        )
        assert policy < run, "the policy must be set before uvicorn.run constructs the loop"

        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert "scripts.serve" in makefile

        # Every recipe line that starts a server must go through the launcher.
        # Checking for one exact spelling of the uvicorn command would miss
        # `uvicorn app.main:app` without `-m`, and there is now more than one
        # serving target (`up` and `serve`) for a regression to hide in.
        for lineno, line in enumerate(makefile.splitlines(), start=1):
            if not line.startswith("	") or "uvicorn" not in line:
                continue
            assert "scripts.serve" in line, (
                f"Makefile:{lineno} invokes uvicorn directly; it must go through "
                f"scripts/serve.py, which selects the event loop psycopg needs: {line.strip()}"
            )

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
    def test_the_selector_policy_is_active_on_windows(self):
        assert isinstance(
            asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy
        ), "psycopg cannot use ProactorEventLoop; see app/core/eventloop.py"

    def test_it_is_a_no_op_off_windows(self):
        from app.core.eventloop import use_compatible_event_loop

        if sys.platform != "win32":
            assert use_compatible_event_loop() is False

    def test_calling_it_twice_is_harmless(self):
        from app.core.eventloop import use_compatible_event_loop

        use_compatible_event_loop()
        assert use_compatible_event_loop() is False


class TestPooledConnections:
    """Supabase's transaction pooler reuses backends across sessions, so
    psycopg's automatic prepared statements collide.

    `executemany` prepares on the first call, so a bulk load fails immediately
    where single statements survive -- which is exactly how this was missed.
    """

    def test_only_session_py_opens_a_raw_connection(self):
        offenders: list[str] = []
        for path in sorted((ROOT / "app").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py")):
            if path == CONNECT_OWNER:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "psycopg"
                ):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")
        assert not offenders, (
            f"these bypass connect_sync and will fail against the transaction pooler: {offenders}"
        )

    def test_the_pooler_port_disables_prepared_statements(self):
        from app.db.session import TRANSACTION_POOLER_PORT, _connect_args

        pooled = f"postgresql://u:p@aws-0.pooler.supabase.com:{TRANSACTION_POOLER_PORT}/postgres"
        assert _connect_args(pooled) == {"prepare_threshold": None}

    def test_connect_sync_applies_those_options(self):
        """The helper must pass the options through, not merely compute them."""
        import inspect

        from app.db.session import connect_sync

        source = inspect.getsource(connect_sync)
        assert "_connect_args" in source


class TestRefusalRecordsSurviveTheRefusal:
    """A.3(3): a refusal must appear as an escalation in the admin view.

    The request's transaction belongs to the `get_connection` dependency, so
    raising out of the route rolls the whole block back. Recording the decision
    and escalation on that transaction and *then* raising therefore discarded
    both -- the client got a correct 403 while the audit trail and the officer
    queue stayed empty.

    Two mechanisms satisfy this, and the guard accepts either, because the
    invariant is "the record outlives the raise", not any one way of getting
    there: commit the request's transaction before raising, or write the record
    on its own transaction so the request's rollback cannot reach it. The
    separate transaction is preferred -- committing mid-handler also commits
    whatever else the handler happened to have written.
    """

    def test_the_refusal_record_cannot_be_rolled_back(self):
        source = (ROOT / "app" / "api" / "routers" / "portfolio.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Connections bound by `async with get_engine().begin() as <name>`: a
        # transaction of their own, independent of the request's.
        independent: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncWith):
                continue
            for item in node.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "begin"
                    and isinstance(call.func.value, ast.Call)
                    and getattr(call.func.value.func, "id", "") == "get_engine"
                    and isinstance(item.optional_vars, ast.Name)
                ):
                    independent.add(item.optional_vars.id)

        durable = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "record_decision"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in independent
        ]
        commits = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Await)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Attribute)
            and n.value.func.attr == "commit"
        ]
        raises = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Raise)
            and isinstance(n.exc, ast.Call)
            and getattr(n.exc.func, "id", "") == "SuitabilityRefused"
        ]
        assert raises, "expected a suitability refusal to be raised somewhere"
        for line in raises:
            assert any(d < line for d in durable) or any(c < line for c in commits), (
                f"SuitabilityRefused at line {line} is raised without the refusal "
                "record being made durable: audit it on its own "
                "`get_engine().begin()` transaction, or commit before raising. "
                "Otherwise the decision and escalation are rolled back."
            )
