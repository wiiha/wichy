"""Tests for the REPL Ctrl+C -> kill-running-tools path.

The guard wraps root_agent.process() in the REPL: a SIGINT while tool
calls are in flight kills them (the agent continues with kill-string
results); with none in flight it preserves the old behavior
(KeyboardInterrupt abandoning the turn).

Signal handlers can only be installed on the main thread, so the signal
tests run on the main thread via os.kill (raising SIGINT into this very
test process, inside the guard window).
"""

import os
import signal
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import wichy.repl as repl_module
from wichy.tools import kill_registry
from wichy.tools.kill_registry import _reset_for_tests, repl_interrupt_guard


@pytest.fixture(autouse=True)
def clean_registry():
    _reset_for_tests()
    yield
    _reset_for_tests()


class TestReplInterruptGuard:
    def test_guard_restores_previous_handler(self):
        previous = signal.getsignal(signal.SIGINT)
        with repl_interrupt_guard():
            assert signal.getsignal(signal.SIGINT) is not previous
            assert signal.getsignal(signal.SIGINT) is not signal.SIG_IGN
        assert signal.getsignal(signal.SIGINT) is previous

    def test_sigint_with_no_tools_raises_keyboard_interrupt(self):
        # Old behavior preserved: no in-flight calls -> the signal
        # becomes KeyboardInterrupt in the guarded body.
        with pytest.raises(KeyboardInterrupt):
            with repl_interrupt_guard():
                os.kill(os.getpid(), signal.SIGINT)

    def test_sigint_with_running_tool_kills_it_and_swallows(self):
        outcome: dict = {}
        release = threading.Event()
        started = threading.Event()

        def busy_worker():
            kill_registry.register(
                tool_call_id="call-1",
                tool_name="glob",
                arguments={"pattern": "**/*.py"},
                agent_id="root",
            )
            try:
                started.set()
                # Short ticks: async-raise lands at bytecode boundaries.
                while not release.wait(timeout=0.2):
                    pass
                outcome["result"] = "returned"
            except BaseException as exc:
                outcome["result"] = type(exc).__name__
            finally:
                kill_registry.finish_call("call-1")

        worker = threading.Thread(target=busy_worker, daemon=True)
        worker.start()
        assert started.wait(timeout=5)

        # Ctrl+C arrives while the tool runs: killed, NOT interrupted.
        body_completed = False
        with repl_interrupt_guard():
            os.kill(os.getpid(), signal.SIGINT)
            # The kill mark must already be set (synchronously in the
            # signal handler).
            assert kill_registry.was_killed("call-1")
            assert kill_registry.killed_reason("call-1") == "REPL interrupt"
            body_completed = True

        assert body_completed, "guard must swallow the signal while tools run"
        worker.join(timeout=15)
        assert not worker.is_alive()
        assert outcome["result"] == "ToolKilledError"

    def test_sigint_with_bash_like_call_uses_process_handle(self):
        # A call WITH a process handle must be killed via killpg, no
        # async-raise monitor armed (registry contract).
        import subprocess

        kill_registry.register(
            tool_call_id="call-bash",
            tool_name="bash",
            arguments={"command": "sleep 300"},
            agent_id="root",
        )
        record = kill_registry.get_record("call-bash")
        proc = subprocess.Popen(["sleep", "300"], start_new_session=True)
        record.set_process(proc)
        try:
            with repl_interrupt_guard():
                os.kill(os.getpid(), signal.SIGINT)
            assert record.kill_requested.is_set()
            deadline = time.monotonic() + 5
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert proc.poll() is not None, "process group must die"
        finally:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=5)
            kill_registry.finish_call("call-bash")

    def test_on_kill_callback_receives_summaries(self):
        kill_registry.register(
            tool_call_id="call-1",
            tool_name="find",
            arguments={"pattern": "/"},
            agent_id="root",
        )
        seen: list = []
        with repl_interrupt_guard(on_kill=seen.extend):
            os.kill(os.getpid(), signal.SIGINT)
        assert len(seen) == 1
        assert seen[0]["tool_call_id"] == "call-1"
        assert seen[0]["tool_name"] == "find"
        kill_registry.finish_call("call-1")

    def test_off_main_thread_is_noop_that_runs_body(self):
        ran = threading.Event()
        before = signal.getsignal(signal.SIGINT)

        def worker():
            with repl_interrupt_guard():
                ran.set()

        th = threading.Thread(target=worker, daemon=True)
        th.start()
        assert ran.wait(timeout=5)
        th.join(timeout=5)
        # Main-thread handler untouched.
        assert signal.getsignal(signal.SIGINT) is before

    def test_kill_all_in_flight_returns_names(self):
        kill_registry.register(
            tool_call_id="a", tool_name="glob", arguments={}, agent_id="root"
        )
        kill_registry.register(
            tool_call_id="b", tool_name="bash", arguments={}, agent_id="root"
        )
        killed = kill_registry.kill_all_in_flight(reason="test")
        names = {entry["tool_name"] for entry in killed}
        assert names == {"glob", "bash"}
        assert kill_registry.was_killed("a")
        assert kill_registry.was_killed("b")
        kill_registry.finish_call("a")
        kill_registry.finish_call("b")


class TestReplNotice:
    def test_print_killed_notice_lists_tools(self):
        import unittest.mock as mock

        from wichy.repl import Repl

        repl_instance = Repl.__new__(Repl)
        with mock.patch("wichy.repl.user_console") as fake_console:
            repl_instance._print_killed_notice(
                [{"tool_call_id": "x", "tool_name": "bash"}]
            )
            fake_console.print.assert_called_once()
            args = fake_console.print.call_args.args
            assert "Killed 1 running tool call(s):" in args[0]
            assert "bash(...)" in args[0]


class TestUnwrappedReplPathsGuarded:
    """Ctrl+C during /btw, /reset (summary), /compact must behave like
    the main process() path: kill running tools instead of a raw
    KeyboardInterrupt escaping the REPL loop."""

    def _make_repl_with_btw(self):
        """Minimal REPL instance with a stubbed root agent."""
        repl = object.__new__(repl_module.Repl)
        repl.root_agent = MagicMock()
        repl.root_agent.agent_has_first_initiative = False
        repl.root_agent.context.return_value = []
        repl._print_killed_notice = MagicMock()
        return repl

    def test_btw_process_runs_inside_guard(self):
        repl = self._make_repl_with_btw()
        repl._print_btw_prompt = MagicMock()

        guard_calls = []
        original_guard = repl_module.kill_registry.repl_interrupt_guard

        def spy_guard(**kw):
            guard_calls.append(kw)
            return original_guard(**kw)

        with patch.object(
            repl_module.kill_registry, "repl_interrupt_guard", side_effect=spy_guard
        ):
            with patch.object(repl_module, "strip_thinking_content", lambda s: s):
                with patch.object(
                    repl_module,
                    "RootAgent",
                    return_value=MagicMock(process=MagicMock(return_value="btw resp")),
                ):
                    repl._run_btw("what?", "test/model", [])

        assert len(guard_calls) == 1, "/btw LLM call ran outside the guard"
        assert guard_calls[0].get("on_kill") is repl._print_killed_notice

    def test_reset_context_runs_inside_guard(self):
        # Drive the real run() loop: the prompt raises
        # ContextResetException (as /reset does), and reset_context runs
        # inside the guard on the exception path.
        repl = self._make_repl_with_btw()
        repl._print_user_prompt = MagicMock()
        repl._print_separator = MagicMock()
        repl.cmd_checker = MagicMock()
        repl.cmd_checker.check_command.return_value = None
        repl.prompt_session = MagicMock()
        repl._print_assistant_response = MagicMock()

        calls = {"n": 0}
        reset_event = threading.Event()
        guard_depth = {"on_reset": False}

        def prompt_side_effect(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise repl_module.ContextResetException(strategy=MagicMock())
            reset_event.set()
            raise EOFError()  # exit the loop

        repl.prompt_session.prompt.side_effect = prompt_side_effect

        guard_calls = []
        original_guard = repl_module.kill_registry.repl_interrupt_guard

        def spy_guard(**kw):
            guard_calls.append(kw)
            return original_guard(**kw)

        def reset_side_effect(*a, **kw):
            # Runs on the exception path; must be inside the guard, so
            # the guard entry must already have happened.
            guard_depth["on_reset"] = len(guard_calls) > 0
            return None

        repl.root_agent.reset_context.side_effect = reset_side_effect

        with patch.object(
            repl_module.kill_registry, "repl_interrupt_guard", side_effect=spy_guard
        ):
            # EOFError at the prompt makes run() exit cleanly.
            with pytest.raises(SystemExit):
                repl.run()

        repl.root_agent.reset_context.assert_called_once()
        assert guard_depth["on_reset"], "reset_context ran outside the guard"


class TestVerificationPromptCtrlC:
    """Ctrl+C at the y/n verification prompt denies the action (with a
    printed notice) instead of silently abandoning the in-flight call."""

    def test_ctrl_c_at_prompt_denies_instead_of_abandoning(self):
        import wichy.tools.human_verification as hv
        from wichy.tools.kill_registry import finish_call, register

        registered = {}

        @hv.require_human_verification
        def do_it(self):
            return "ran"

        calls = {"prompt": 0}

        def fake_prompt(*a, **kw):
            calls["prompt"] += 1
            if calls["prompt"] == 1:
                raise KeyboardInterrupt()
            return "y"

        session = MagicMock()
        session.prompt.side_effect = fake_prompt

        # In-flight call record, as validate_and_execute would have made.
        registered["rec"] = register("call-v1", "bash", {"command": "x"})

        with patch.object(hv, "prompt_session", session):
            with patch.object(hv.settings, "skip_human_verification", False):
                with patch.object(hv, "in_pipeline_mode", return_value=False):
                    with patch.object(hv, "needs_user_attention"):
                        try:
                            do_it(None)
                            outcome = "ran"
                        except PermissionError as e:
                            outcome = str(e)

        finish_call("call-v1")
        assert "aborted the verification prompt" in outcome, outcome
