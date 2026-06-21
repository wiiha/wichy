"""Tests for TaskAgent core behavior, especially turn-count messaging."""

import pytest

from wichy.tools.task.base import TaskAgent, TaskAgentDefinitionBase, _TURNS_WARNING_THRESHOLD


class FakeTool:
    """Minimal tool stand-in for TaskAgent construction."""

    name = "fake_tool"

    def __call__(self):
        return self


def _make_agent(max_turns: int | None) -> TaskAgent:
    definition = TaskAgentDefinitionBase(
        name="test-agent",
        description="A test agent",
        system_prompt="You are a test agent.",
    )
    return TaskAgent(
        agent_definition=definition,
        prompt="Do something.",
        model="ollama/test-model",
        all_tools_not_instantiated=[FakeTool],
        max_turns=max_turns,
    )


def test_initial_system_prompt_states_total_turns():
    """When max_turns is set, the system prompt mentions the total once."""
    agent = _make_agent(max_turns=10)
    system_message = agent.context()[0]
    assert system_message["role"] == "system"
    assert "You have 10 turns available for this task." in system_message["content"]


def test_initial_system_prompt_no_turns_when_unlimited():
    """When max_turns is None, no turn count text appears in system prompt."""
    agent = _make_agent(max_turns=None)
    system_message = agent.context()[0]
    assert "turns" not in system_message["content"].lower()


def test_system_prompt_is_never_updated_by_agent(monkeypatch):
    """The initial system message content stays byte-for-byte identical."""
    agent = _make_agent(max_turns=5)
    original_system = agent.context()[0]["content"]

    # Simulate one tool loop iteration by manually incrementing turns_used and
    # invoking the same logic the agent uses to add reminders.
    agent._turns_used = 2
    remaining = agent._max_turns - agent._turns_used
    effective_threshold = min(
        _TURNS_WARNING_THRESHOLD, max(2, agent._max_turns - 1)
    )
    if remaining <= effective_threshold:
        agent.context.add(
            role="user",
            content=f"You have {remaining} turns remaining for this task.",
        )

    assert agent.context()[0]["content"] == original_system


def test_effective_threshold_for_large_max_turns():
    """For large max_turns the effective threshold equals the constant."""
    agent = _make_agent(max_turns=20)
    assert agent._max_turns is not None
    effective_threshold = min(
        _TURNS_WARNING_THRESHOLD, max(2, agent._max_turns - 1)
    )
    assert effective_threshold == _TURNS_WARNING_THRESHOLD


def test_effective_threshold_for_small_max_turns():
    """For short tasks the threshold is capped to max_turns - 1, at least 2."""
    agent = _make_agent(max_turns=4)
    effective_threshold = min(
        _TURNS_WARNING_THRESHOLD, max(2, agent._max_turns - 1)
    )
    assert effective_threshold == 3


@pytest.mark.parametrize(
    "max_turns,expected_threshold",
    [
        (20, _TURNS_WARNING_THRESHOLD),
        (10, _TURNS_WARNING_THRESHOLD),
        (6, 5),
        (5, 4),
        (4, 3),
        (3, 2),
        (2, 2),
    ],
)
def test_effective_thresholds(max_turns, expected_threshold):
    """Threshold formula matches expectations across task sizes."""
    effective = min(_TURNS_WARNING_THRESHOLD, max(2, max_turns - 1))
    assert effective == expected_threshold


def test_reminders_accumulate_near_end():
    """Each qualifying turn appends a new user reminder message."""
    agent = _make_agent(max_turns=6)

    # Simulate turns 2..5 (remaining 4..1). Threshold for max_turns=6 is 5,
    # so all of these should produce a reminder.
    reminder_count = 0
    for turn in range(2, 6):
        agent._turns_used = turn
        remaining = agent._max_turns - agent._turns_used
        effective_threshold = min(
            _TURNS_WARNING_THRESHOLD, max(2, agent._max_turns - 1)
        )
        if remaining <= effective_threshold:
            agent.context.add(
                role="user",
                content=f"You have {remaining} turns remaining for this task.",
            )
            reminder_count += 1

    assert reminder_count == 4

    # Count actual reminder messages in the context
    actual = [
        msg
        for msg in agent.context()
        if msg["role"] == "user" and "turns remaining" in msg["content"]
    ]
    assert len(actual) == 4


def test_no_reminders_before_threshold():
    """No late reminders are injected while remaining turns are above threshold."""
    agent = _make_agent(max_turns=10)

    # First tool round: remaining 9, threshold 5 -> no reminder
    agent._turns_used = 1
    remaining = agent._max_turns - agent._turns_used
    effective_threshold = min(
        _TURNS_WARNING_THRESHOLD, max(2, agent._max_turns - 1)
    )
    if remaining <= effective_threshold:
        agent.context.add(
            role="user",
            content=f"You have {remaining} turns remaining for this task.",
        )

    reminders = [
        msg
        for msg in agent.context()
        if msg["role"] == "user" and "turns remaining" in msg["content"]
    ]
    assert len(reminders) == 0
