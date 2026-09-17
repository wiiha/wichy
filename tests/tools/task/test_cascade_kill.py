"""Tests for the cascade kill of task agents (Stage 2b).

Covers the nesting contract of the tool-kill feature: the `task` tool
call and its inner tool calls stack on the SAME thread and are ALL
registered in the kill registry simultaneously. Killing the outer
`task` call must:
(a) stop the spawned TaskAgent (request_stop + killed fast-exit flag),
(b) kill every in-flight inner call registered under that agent_id,
(c) fast-exit the task agent loop WITHOUT the _gen_summary() LLM call,
returning a canned string; the outer validate_and_execute swaps it for
the [TOOL_KILLED] notice so the root agent sees the kill result.

An inner-call-only kill is contained inside the task agent: the inner
call gets a kill-string result and the agent loop continues.
"""

import threading
import time
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import wichy.tools.kill_registry as kill_registry
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.kill_registry import (
    _reset_for_tests,
    finish_call,
    is_in_flight,
    kill_calls_for_agent,
    list_in_flight,
    register,
    request_kill,
    was_killed,
)
from wichy.tools.task.base import (
    _TASK_KILLED_RESULT,
    TaskAgent,
    TaskAgentDefinitionBase,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the process-global kill registry per test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


class SlowParams(ParametersModel):
    pass


class SlowTool(BaseTool):
    """Inner tool that busy-waits until released (killable mid-run)."""

    name = "slowtool"
    description = "busy-waits until released"
    parameters_model = SlowParams

    def __init__(self):
        self._release = threading.Event()
        self.started = threading.Event()

    def execute(self, **kwargs):
        self.started.set()
        while not self._release.is_set():
            time.sleep(0.01)
        return "slow tool done"


def _make_agent(tools, max_turns: Optional[int] = None) -> TaskAgent:
    definition = TaskAgentDefinitionBase(
        name="explore-agent",
        description="A test agent",
        system_prompt="You are a test agent.",
    )
    return TaskAgent(
        agent_definition=definition,
        prompt="Run the slow tool.",
        model="test/model",
        all_tools_not_instantiated=tools,
        max_turns=max_turns,
    )


def _llm_response(tool_calls=None, content="thinking..."):
    """Build a mock LLM response message."""
    response = MagicMock()
    response.message = MagicMock()
    response.message.content = content
    response.message.reasoning = None
    response.message.tool_calls = tool_calls
    if tool_calls:
        response.message.finish_reason = "tool_calls"
    else:
        response.message.finish_reason = "stop"
    return response


def _tool_call_item(call_id: str = "call-inner-1", name: str = "slowtool"):
    """Build a called_tool mock for one inner tool call."""
    import json

    item = MagicMock()
    item.id = call_id
    item.function.name = name
    item.function.arguments = json.dumps({})
    return item


class TestCascadeKill:
    """Killing the outer `task` call cascades into agent + inner calls."""

    def test_kill_task_call_stops_agent_and_inner_calls(self):
        tool = SlowTool()
        agent = _make_agent([lambda: tool])
        agent_id = agent.context.custom_suffix

        result: dict = {}
        outer: dict = {}

        # The mocked LLM responds with one tool call; on the second
        # round it would answer stop, but the kill fires first and the
        # agent fast-exits before that round completes.
        call_count = {"n": 0}

        def mock_llm_call(*a, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _llm_response(tool_calls=[_tool_call_item()])
            return _llm_response(tool_calls=None, content="final answer")

        with patch("wichy.tools.task.base.call", side_effect=mock_llm_call):
            # Wrap the task agent's tool run in a fake outer validate:
            # we need the outer record registered before execute starts.
            # Simulate directly: thread runs the "task tool call".
            def run_outer():
                record = register(
                    "call-task-1", "task", {"prompt": "..."}, agent_id="root"
                )
                outer["record"] = record
                record.attach_task_agent(agent_id)
                try:
                    result["res"] = agent.run()
                finally:
                    finish_call("call-task-1")

            th = threading.Thread(target=run_outer, daemon=True)
            th.start()

            # Wait until the inner slowtool is in flight.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if tool.started.is_set() and is_in_flight("call-inner-1"):
                    break
                time.sleep(0.02)
            assert is_in_flight("call-inner-1"), "inner call never registered"
            assert is_in_flight("call-task-1"), "outer task call never registered"

            # Kill the OUTER task call: cascades to agent + inner call.
            assert request_kill("call-task-1", reason="stop this task") is True

            th.join(timeout=15)
            assert not th.is_alive(), "task agent thread did not fast-exit"

        # Agent returned the canned fast-exit string (not a summary).
        assert result["res"] == "Task agent stopped by user."
        # Agent's stop + killed flags set.
        assert agent._stop_event.is_set()
        assert agent._killed.is_set()
        # Registry drained: no leaked records.
        assert list_in_flight() == []
        # The outer kill mark observed (the outer validate would swap
        # the result for the kill notice).
        assert was_killed("call-task-1")

    def test_inner_only_kill_is_contained_in_agent_loop(self):
        """A kill of just the inner call must NOT stop the agent."""
        tool = SlowTool()
        agent = _make_agent([lambda: tool])

        state = {"round": 0}

        def mock_llm_call(*a, **kw):
            state["round"] += 1
            if state["round"] == 1:
                return _llm_response(tool_calls=[_tool_call_item()])
            if state["round"] == 2:
                # inner call was killed; agent continues -> answer stop
                return _llm_response(tool_calls=None, content="continued fine")
            raise AssertionError("too many rounds")

        with patch("wichy.tools.task.base.call", side_effect=mock_llm_call):
            result: dict = {}

            def run_agent():
                result["res"] = agent.run()

            th = threading.Thread(target=run_agent, daemon=True)
            th.start()

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if tool.started.is_set() and is_in_flight("call-inner-1"):
                    break
                time.sleep(0.02)
            assert is_in_flight("call-inner-1")

            # Kill ONLY the inner call.
            assert request_kill("call-inner-1") is True
            th.join(timeout=15)

        assert not th.is_alive(), "agent loop should continue after inner kill"
        # The agent produced a normal final answer, NOT the canned string.
        assert result["res"] == "continued fine"
        # Agent was NOT stopped by the inner kill.
        assert not agent._stop_event.is_set()
        assert not agent._killed.is_set()
        assert list_in_flight() == []

    def test_task_agent_registers_inner_calls_under_its_agent_id(self):
        """Inner calls registered on the agent's thread inherit its agent_id."""
        tool = SlowTool()
        agent = _make_agent([lambda: tool], max_turns=2)
        agent_id = agent.context.custom_suffix

        round_n = {"n": 0}

        def mock_llm_call(*a, **kw):
            round_n["n"] += 1
            if round_n["n"] == 1:
                return _llm_response(tool_calls=[_tool_call_item()])
            return _llm_response(tool_calls=None, content="done")

        with patch("wichy.tools.task.base.call", side_effect=mock_llm_call):
            th = threading.Thread(target=agent.run, daemon=True)
            th.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if tool.started.is_set():
                    break
                time.sleep(0.02)
            # While the inner call is in flight, its agent_id must be
            # the task agent's custom_suffix.
            inner = [r for r in list_in_flight() if r["tool_call_id"] == "call-inner-1"]
            assert inner, "inner call not in flight"
            assert inner[0]["agent_id"] == agent_id
            tool._release.set()
            th.join(timeout=10)
            # A leaked agent thread would hammer the next test's LLM
            # mock and poison its rounds -- the join MUST succeed.
            assert not th.is_alive(), "agent thread leaked past the test"
        # The ambient agent-id stack was popped in run()'s finally.
        assert kill_registry.current_agent_id() is None


class TestCascadeHelpers:
    """kill_calls_for_agent targeting + attach_task_agent races."""

    def test_kill_calls_for_agent_targets_only_that_agent(self):
        register("c1", "bash", {}, agent_id="agent-a")
        register("c2", "glob", {}, agent_id="agent-b")
        killed = kill_calls_for_agent("agent-a", reason="cascade")
        assert killed == ["c1"]
        assert was_killed("c1")
        assert not was_killed("c2")
        finish_call("c1")
        finish_call("c2")

    def test_attach_task_agent_after_kill_fires_cascade_immediately(self):
        # Kill arrives BEFORE the agent is attached (race: kill during
        # TaskAgent construction): attach_task_agent must fire the
        # cascade immediately via the stopper callback.
        record = register("call-task-2", "task", {}, agent_id="root")
        request_kill("call-task-2", reason="early kill")

        calls: list = []

        def fake_stopper(agent_id, reason):
            calls.append((agent_id, reason))

        with patch.object(kill_registry, "_task_agent_stopper", fake_stopper):
            record.attach_task_agent("agent-x")

        assert calls == [
            ("agent-x", "early kill")
        ], "cascade did not fire immediately after late attach"
        finish_call("call-task-2")


class TestRealTaskToolPath:
    """End-to-end through the REAL TaskAgentTool + validate_and_execute.

    Exercises the production wiring end to end: the hidden-kwargs
    registration in validate_and_execute, the thread-local
    current_record, task_tool's pre_register + attach_task_agent, the
    agent loop with a real inner tool, and the outer kill swap.
    """

    def test_kill_via_validate_and_execute_swaps_for_kill_string(self):
        from wichy.tools.task_tool import TaskAgentTool

        # The spawned agent gets ONLY our inner tool (the real tool
        # list pulls every registered tool, which would need a full
        # LLM-free environment).
        inner_started = threading.Event()
        release = threading.Event()

        # The spawned agent instantiates its own InnerTool (TaskAgent
        # __init__ calls each tool class), so the busy-wait events must
        # be CLASS-LEVEL, shared across instances.
        class InnerTool(BaseTool):
            name = "read_file"  # an Explore-agent-allowed tool name
            description = "inner tool that busy-waits"
            parameters_model = SlowParams
            started_evt = inner_started
            release_evt = release

            def execute(self, **kwargs):
                self.started_evt.set()
                while not self.release_evt.is_set():
                    time.sleep(0.01)
                return "inner done"

        with patch("wichy.tools.task_tool.get_all_tools", return_value=[InnerTool]):
            tool = TaskAgentTool()

            call_count = {"n": 0}

            def mock_llm_call(*a, **kw):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    response = MagicMock()
                    response.message = MagicMock()
                    response.message.content = "thinking"
                    response.message.reasoning = None
                    response.message.tool_calls = [
                        _tool_call_item("call-inner-e2e", "read_file")
                    ]
                    response.message.finish_reason = "tool_calls"
                    return response
                response = MagicMock()
                response.message = MagicMock()
                response.message.content = "should not be needed"
                response.message.reasoning = None
                response.message.tool_calls = None
                response.message.finish_reason = "stop"
                return response

            result: dict = {}

            def run():
                result["res"] = tool.validate_and_execute(
                    prompt="Use the read_file tool.",
                    subagent_type="Explore",
                    description="test run",
                    model_str="test/model",
                    _tool_call_id="call-e2e-1",
                    _agent_id="root",
                )

            with patch("wichy.tools.task.base.call", side_effect=mock_llm_call):
                with patch("wichy.tools.base.HookExecutor") as he:
                    he.run_pre_hooks.return_value = MagicMock(
                        approved=True, modified_input=None
                    )
                    he.run_post_hooks.return_value = MagicMock(
                        approved=True, modified_output=None
                    )
                    th = threading.Thread(target=run, daemon=True)
                    th.start()

                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if inner_started.is_set() and is_in_flight("call-inner-e2e"):
                            break
                        time.sleep(0.02)
                    assert is_in_flight(
                        "call-inner-e2e"
                    ), "inner call never registered through real path"
                    assert is_in_flight(
                        "call-e2e-1"
                    ), "outer task call never registered through real path"

                    # Kill the outer `task` call end to end.
                    assert request_kill("call-e2e-1", reason="e2e stop") is True
                    th.join(timeout=20)

            assert not th.is_alive(), "real-path thread did not fast-exit"
            assert result["res"].startswith(
                "[TOOL_KILLED]"
            ), "outer validate_and_execute did not swap in the kill string"
            assert "e2e stop" in result["res"]
            assert list_in_flight() == []


class TestKillDuringLLMRound:
    """Kill arriving while the agent is inside an LLM call.

    The bottom-of-loop stop check cannot fire while the thread blocks
    on the LLM socket; without a check right after the round, the agent
    would run another full tools round before noticing the kill.
    """

    def test_kill_mid_llm_round_stops_before_next_tool_round(self):
        tool = SlowTool()
        agent = _make_agent([lambda: tool])
        result: dict = {}

        llm_started = threading.Event()
        kill_fired = threading.Event()

        def mock_llm_call(*a, **kw):
            # Simulate the kill landing while the thread blocks here.
            llm_started.set()
            if kill_fired.wait(timeout=5):
                agent._stop_event.set()
                agent._killed.set()
            return _llm_response(tool_calls=[_tool_call_item()], content="round 1 done")

        with patch("wichy.tools.task.base.call", side_effect=mock_llm_call):
            run = threading.Thread(
                target=lambda: result.update(res=agent.run()), daemon=True
            )
            run.start()
            assert llm_started.wait(timeout=5)
            kill_fired.set()
            run.join(timeout=10)

        assert not run.is_alive(), "agent hung after mid-round kill"
        # Fast-exit BEFORE _handle_tools ran the tool again.
        assert result["res"] == _TASK_KILLED_RESULT

    def test_kill_in_final_llm_round_suppresses_content(self):
        agent = _make_agent([])
        result: dict = {}

        llm_started = threading.Event()
        kill_fired = threading.Event()

        def mock_llm_call(*a, **kw):
            llm_started.set()
            if kill_fired.wait(timeout=5):
                agent._stop_event.set()
                agent._killed.set()
            return _llm_response(tool_calls=None, content="final answer")

        with patch("wichy.tools.task.base.call", side_effect=mock_llm_call):
            run = threading.Thread(
                target=lambda: result.update(res=agent.run()), daemon=True
            )
            run.start()
            assert llm_started.wait(timeout=5)
            kill_fired.set()
            run.join(timeout=10)

        assert not run.is_alive()
        assert (
            result["res"] == _TASK_KILLED_RESULT
        ), "final-round content must not be returned after a kill"
