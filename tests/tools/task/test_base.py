"""Tests for TaskAgent turn-count messaging, driven through the real loop."""

from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.task.base import TaskAgent, TaskAgentDefinitionBase


class EchoParams(ParametersModel):
    pass


class EchoTool(BaseTool):
    name = "echo"
    description = "returns a fixed string"
    parameters_model = EchoParams

    def execute(self, **kwargs: Any) -> str:
        return "echoed"


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Context/event files are CWD-relative; keep them out of the repo, and
    silence the event log (this file tests turn messaging, not events)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(TaskAgent, "_emit_event", lambda self, *a, **k: None)


def _make_agent(max_turns: Optional[int]) -> TaskAgent:
    definition = TaskAgentDefinitionBase(
        name="test-agent",
        description="A test agent",
        system_prompt="You are a test agent.",
    )
    return TaskAgent(
        agent_definition=definition,
        prompt="Do something.",
        model="test/model",
        all_tools_not_instantiated=[EchoTool],
        max_turns=max_turns,
    )


def _response(with_tool_call: bool) -> MagicMock:
    response = MagicMock()
    response.message.content = "ok"
    response.message.reasoning = None
    if with_tool_call:
        item = MagicMock()
        item.id = "call-1"
        item.function.name = "echo"
        item.function.arguments = json.dumps({})
        response.message.tool_calls = [item]
        response.message.finish_reason = "tool_calls"
    else:
        response.message.tool_calls = None
        response.message.finish_reason = "stop"
    return response


def _drive(max_turns: int) -> TaskAgent:
    """Run the real loop, replying with a tool call until the last turn.

    The loop asks for tools on every round except the final tool-free one, so
    the reminder path executes for real instead of being re-implemented here.
    """
    agent = _make_agent(max_turns)
    state = {"n": 0}

    def mock_call(*_args: Any, **_kwargs: Any) -> MagicMock:
        n = state["n"]
        state["n"] += 1
        return _response(with_tool_call=n < max_turns - 1)

    with patch("wichy.tools.task.base.call", side_effect=mock_call):
        agent._process()
    return agent


def _reminders(agent: TaskAgent) -> list[str]:
    return [
        msg["content"]
        for msg in agent.context()
        if msg["role"] == "user" and "turns remaining" in msg["content"]
    ]


def test_initial_system_prompt_states_total_turns() -> None:
    """When max_turns is set, the system prompt mentions the total once."""
    agent = _make_agent(max_turns=10)
    system_message = agent.context()[0]
    assert system_message["role"] == "system"
    assert "You have 10 turns available for this task." in system_message["content"]


def test_initial_system_prompt_no_turns_when_unlimited() -> None:
    """When max_turns is None, no turn count text appears in system prompt."""
    agent = _make_agent(max_turns=None)
    assert "turns" not in agent.context()[0]["content"].lower()


@pytest.mark.parametrize(
    "max_turns,expected_remaining",
    [
        (2, [0]),
        (3, [1, 0]),
        (4, [2, 1, 0]),
        (6, [4, 3, 2, 1, 0]),
        (10, [5, 4, 3, 2, 1, 0]),
    ],
)
def test_the_loop_injects_one_reminder_per_late_turn(
    max_turns: int, expected_remaining: list[int]
) -> None:
    """The real loop reminds exactly on the turns at or below the threshold."""
    agent = _drive(max_turns)
    assert _reminders(agent) == [
        f"You have {n} turns remaining for this task." for n in expected_remaining
    ]


def test_the_loop_leaves_the_system_prompt_untouched() -> None:
    """Late reminders are appended; the initial system message never changes."""
    agent = _make_agent(max_turns=5)
    original = agent.context()[0]["content"]

    state = {"n": 0}

    def mock_call(*_args: Any, **_kwargs: Any) -> MagicMock:
        n = state["n"]
        state["n"] += 1
        return _response(with_tool_call=n < 4)

    with patch("wichy.tools.task.base.call", side_effect=mock_call):
        agent._process()

    assert agent.context()[0]["content"] == original
    assert _reminders(agent) != []
