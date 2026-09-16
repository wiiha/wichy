"""Tests for kill-registry integration in the tool execution path
(src/wichy/tools/base.py validate_and_execute + src/wichy/agent/core.py).

Covers the Stage 2 wiring contract:
- hidden `_tool_call_id` / `_agent_id` kwargs are popped before pydantic
  validation and the call is registered as in-flight
- a kill requested while the call is blocked before execute() starts
  (pre-start kill) skips execute entirely and returns the kill string
- a kill requested mid-execute swaps the result for the kill string,
  treats the kill as NOT an error, and never result-offloads the nudge
- a killed call is never reported as "completed" and post-tool hooks
  receive killed=True so they can tell a user-stopped call apart from
  a normal success
- a post-hook's modified_output never replaces the kill notice
- the finally tail is exception-safe: a kill straggler landing mid-tail
  cannot escape validate_and_execute
- the hook-denied path (pre-hook denies execution) never touches the
  kill flow variables
"""

import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import wichy.tools.kill_registry as kill_registry
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.kill_registry import _reset_for_tests


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the process-global registry for each test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


class KillableParams(ParametersModel):
    pass


class FakePreHookResult:
    """Pre-hook result that optionally kills the call mid-hook.

    All attributes live outside the instance dict (object.__setattr__)
    so __getattr__ fires on EVERY .approved read: the kill request is
    issued on the first read, before execute() ever starts.
    """

    def __init__(self, kill_id: Optional[str] = None, approved: bool = True):
        object.__setattr__(self, "_kill_id", kill_id)
        object.__setattr__(self, "_approved", approved)
        object.__setattr__(self, "_fired", False)

    def __getattr__(self, name):
        if name == "approved":
            kill_id = object.__getattribute__(self, "_kill_id")
            if kill_id and not object.__getattribute__(self, "_fired"):
                object.__setattr__(self, "_fired", True)
                kill_registry.request_kill(kill_id, reason="user stopped")
            return object.__getattribute__(self, "_approved")
        if name == "modified_input":
            return None
        raise AttributeError(name)


class HookProbe:
    """Post-hook result that records what run_post_hooks was called with."""

    def __init__(self, modified_output: Optional[str] = None):
        self.modified_output = modified_output
        self.seen: dict = {}


class WaitTool(BaseTool):
    """Tool that busy-waits until released.

    Uses a polling loop (not Event.wait / time.sleep) because
    PyThreadState_SetAsyncExc delivers only at bytecode boundaries
    or GIL reacquire: a single long blocking sleep would swallow the
    kill until it returns, making the test nondeterministic. A
    polling loop hits a bytecode boundary every iteration, which is
    exactly how real pure-Python tool code (glob loops, network
    retries, chart building) behaves.
    """

    name = "waittool"
    description = "blocks until released"
    parameters_model = KillableParams

    def __init__(self):
        self._release = threading.Event()
        self.execute_started = threading.Event()

    def execute(self, **kwargs):
        self.execute_started.set()
        while not self._release.is_set():
            time.sleep(0.01)
        return "finished normally"


class InstantTool(BaseTool):
    name = "instanttool"
    description = "returns immediately"
    parameters_model = KillableParams

    def execute(self, **kwargs):
        return "real result"


def _patch_hooks(pre=None, post=None):
    """Patch HookExecutor in base.py with given pre/post results."""
    he = MagicMock()
    he.run_pre_hooks.return_value = pre if pre is not None else FakePreHookResult()
    he.run_post_hooks.return_value = post if post is not None else HookProbe()
    return patch("wichy.tools.base.HookExecutor", he)


class TestHiddenKwargsRegistration:
    """Hidden kwargs are popped and the call is registered in-flight."""

    def test_hidden_kwargs_never_reach_pydantic(self):
        tool = InstantTool()
        with _patch_hooks():
            res = tool.validate_and_execute(
                arg=1, _tool_call_id="call-h1", _agent_id="root"
            )
        assert res == "real result"
        assert not kill_registry.is_in_flight("call-h1")

    def test_call_registered_as_in_flight(self):
        tool = WaitTool()
        with _patch_hooks():
            runner = threading.Thread(
                target=lambda: tool.validate_and_execute(
                    _tool_call_id="call-h2", _agent_id="root"
                ),
                daemon=True,
            )
            runner.start()
            tool.execute_started.wait(timeout=5)
            assert kill_registry.is_in_flight("call-h2")
            tool._release.set()
            runner.join(timeout=5)

    def test_synthetic_id_when_missing(self):
        # Direct invocation without hidden kwargs still registers and
        # cleans up; the id is synthetic so kill paths can target it.
        tool = InstantTool()
        real_register = kill_registry.register
        in_flight = {}

        def spy_register(**kwargs):
            record = real_register(**kwargs)
            in_flight["id"] = record.tool_call_id
            return record

        with _patch_hooks():
            with patch.object(kill_registry, "register", side_effect=spy_register):
                tool.validate_and_execute(arg=1)
        assert in_flight["id"], "no synthetic id was generated"


class TestPreStartKill:
    """Kill arriving while blocked on hooks/verification skips execute."""

    def test_kill_during_pre_hook_skips_execute(self):
        tool = WaitTool()
        pre = FakePreHookResult(kill_id="call-p1")
        with _patch_hooks(pre=pre):
            res = tool.validate_and_execute(_tool_call_id="call-p1", _agent_id="root")
        assert not tool.execute_started.is_set(), "execute ran despite pre-start kill"
        assert res.startswith("[TOOL_KILLED]")
        assert not kill_registry.is_in_flight("call-p1")

    def test_pre_start_kill_not_reported_as_completed(self):
        import io

        from rich.console import Console

        tool = InstantTool()
        pre = FakePreHookResult(kill_id="call-p2")
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        with patch("wichy.tools.base.user_console", console):
            with _patch_hooks(pre=pre):
                tool.validate_and_execute(_tool_call_id="call-p2", _agent_id="root")
        assert "completed" not in buf.getvalue()
        # exactly ONE killed banner (no duplicate from the mid-execute check)
        assert buf.getvalue().count("killed") == 1


class TestMidExecuteKill:
    """Kill arriving during execute swaps the result for the kill string."""

    def test_kill_swaps_result_and_thread_exits(self):
        tool = WaitTool()
        result: dict = {}

        def run():
            with _patch_hooks():
                result["res"] = tool.validate_and_execute(
                    _tool_call_id="call-m1", _agent_id="root"
                )

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        tool.execute_started.wait(timeout=5)
        assert kill_registry.request_kill("call-m1", reason="too slow") is True
        runner.join(timeout=5)
        assert not runner.is_alive(), "thread must exit promptly after kill"
        assert result["res"].startswith("[TOOL_KILLED]")
        assert "too slow" in result["res"]
        assert not kill_registry.is_in_flight("call-m1")

    def test_killed_result_not_offloaded(self):
        # The offload path must never wrap the kill notice into a
        # result reference: gate is `not execution_error and not killed`.
        tool = WaitTool()
        result: dict = {}

        def run():
            with _patch_hooks():
                with patch(
                    "wichy.result_offload.result_or_ref",
                    side_effect=AssertionError("offload must not run"),
                ) as mock_offload:
                    # import inside base is lazy; patch at source module
                    result["mock"] = mock_offload
                    result["res"] = tool.validate_and_execute(
                        _tool_call_id="call-m2", _agent_id="root"
                    )

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        tool.execute_started.wait(timeout=5)
        kill_registry.request_kill("call-m2")
        runner.join(timeout=5)
        assert result["res"].startswith("[TOOL_KILLED]")
        assert result["mock"].call_count == 0, "offload ran on a killed call"

    def test_kill_is_not_an_error_for_post_hooks(self):
        tool = WaitTool()
        probe = HookProbe()
        post_seen: dict = {}

        def record_post(
            tool_instance, tool_name, args, output, error=None, killed=False
        ):
            post_seen["error"] = error
            post_seen["killed"] = killed
            post_seen["output"] = output
            return probe

        he = MagicMock()
        he.run_pre_hooks.return_value = FakePreHookResult()
        he.run_post_hooks.side_effect = record_post

        result: dict = {}

        def run():
            with patch("wichy.tools.base.HookExecutor", he):
                result["res"] = tool.validate_and_execute(
                    _tool_call_id="call-m3", _agent_id="root"
                )

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        tool.execute_started.wait(timeout=5)
        kill_registry.request_kill("call-m3")
        runner.join(timeout=5)
        assert post_seen["killed"] is True
        assert post_seen["error"] is None, "kill must not surface as an error"
        assert str(post_seen["output"]).startswith("[TOOL_KILLED]")

    def test_post_hook_modified_output_cannot_replace_kill_notice(self):
        tool = WaitTool()
        result: dict = {}

        def run():
            probe = HookProbe(modified_output="hook rewrote everything")
            with _patch_hooks(post=probe):
                result["res"] = tool.validate_and_execute(
                    _tool_call_id="call-m4", _agent_id="root"
                )

        runner = threading.Thread(target=run, daemon=True)
        runner.start()
        tool.execute_started.wait(timeout=5)
        kill_registry.request_kill("call-m4")
        runner.join(timeout=5)
        assert result["res"].startswith(
            "[TOOL_KILLED]"
        ), "post-hook modified_output replaced the kill notice"


class TestHookDeniedPath:
    """Pre-hook denial must work independently of the kill flow."""

    def test_hook_denied_returns_error_without_kill_flow(self):
        tool = InstantTool()
        executed = []
        real_execute = tool.execute

        def execute_probe(**kwargs):
            executed.append(1)
            return real_execute(**kwargs)

        tool.execute = execute_probe
        pre = MagicMock()
        pre.approved = False
        pre.error_message = "denied by policy"
        pre.modified_input = None
        with _patch_hooks(pre=pre):
            res = tool.validate_and_execute(arg=1)
        assert res == "error: denied by policy"
        assert executed == [], "execute ran despite hook denial"

    def test_hook_denied_with_kill_afterwards_still_returns_kill_string(self):
        # Kill arrives after denial: the kill mark wins per contract.
        tool = InstantTool()
        fired = []

        def deny_then_kill(*args, **kwargs):
            if not fired:
                fired.append(1)
                kill_registry.request_kill("call-d1", reason="nope")
            return MagicMock(
                approved=False, error_message="denied", modified_input=None
            )

        he = MagicMock()
        he.run_pre_hooks.side_effect = deny_then_kill
        he.run_post_hooks.return_value = HookProbe()
        with patch("wichy.tools.base.HookExecutor", he):
            res = tool.validate_and_execute(_tool_call_id="call-d1", _agent_id="root")
        assert res.startswith("[TOOL_KILLED]"), "kill mark must win over denial"


class TestFinallyTailSafety:
    """The finally tail cannot let a kill straggler escape."""

    def test_finish_call_runs_even_when_validation_fails(self):
        tool = InstantTool()

        class ExplodingParams(ParametersModel):
            pass

        tool.parameters_model = ExplodingParams

        # force a pydantic validation failure with an unexpected kwarg
        def bad_params(**kwargs):
            raise ValueError("validation exploded")

        with patch.object(tool, "parameters_model", side_effect=bad_params):
            res = tool.validate_and_execute(arg=1)
        assert res.startswith("error:")
        assert not kill_registry.is_in_flight("call-d2")

    def test_finish_call_failure_propagates_loudly(self):
        # A registry failure must not be swallowed silently: records
        # leaking in-flight entries are worse than a loud crash, so
        # finish_call errors propagate to the caller (who sees a real
        # error instead of a zombie entry blocking future kills).
        tool = InstantTool()
        pre = FakePreHookResult(kill_id="call-f1")
        with _patch_hooks(pre=pre):
            with patch.object(
                kill_registry,
                "finish_call",
                side_effect=RuntimeError("registry lock exploded"),
            ):
                with pytest.raises(RuntimeError, match="registry lock exploded"):
                    tool.validate_and_execute(_tool_call_id="call-f1", _agent_id="root")
