"""Tests that task-agent events are emitted to per-agent logs."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wichy.tools.task import TaskAgent
from wichy.tools.task.base import TaskAgentDefinitionBase


def _make_def() -> TaskAgentDefinitionBase:
    return TaskAgentDefinitionBase(
        name="coder",
        description="A coder agent",
        system_prompt="You are a coding assistant.",
    )


@pytest.fixture
def temp_contexts_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with patch("wichy.context.handler.settings") as mock_settings:
            mock_settings.contexts_dir = tmp_path
            # Build a real settings-like object so EventStore defaults work.
            class FakeSettings:
                events_dir = tmp_path / "events"
                events_max_count = 50_000
                events_max_size_mb = 10
                events_retention_days = 7
                events_queue_size = 10_000

            with patch("wichy.event_log.store.settings", FakeSettings()):
                with patch("wichy.event_log.paths.settings", FakeSettings()):
                    yield tmp_path


class TestTaskAgentEvents:
    def test_task_agent_emits_registered_and_completed_events(self, temp_contexts_dir):
        agent = TaskAgent(
            agent_definition=_make_def(),
            prompt="write hello world",
            model="test/model",
            all_tools_not_instantiated=[],
            max_turns=1,
        )

        with patch("wichy.tools.task.base.call") as mock_call:
            response = MagicMock()
            response.message = MagicMock()
            response.message.content = "done"
            response.message.finish_reason = "stop"
            response.message.tool_calls = None
            response.message.reasoning = None
            response.usage = None
            mock_call.return_value = response
            agent.run()

        # Flush and close the agent's event store so queued events hit disk.
        from wichy.event_log import get_agent_event_store

        store = get_agent_event_store(agent.context.session_id, agent.context.custom_suffix)
        store.flush(timeout=2.0)
        store.close(timeout=2.0)

        events_dir = temp_contexts_dir / "events" / "sessions" / agent.context.session_id / "agents"
        log_file = events_dir / f"{agent.context.custom_suffix}.events.jsonl"
        assert log_file.exists()

        entries = [json.loads(line) for line in log_file.read_text().strip().splitlines()]
        types = [e["event_type"] for e in entries]
        assert "task_agent_registered" in types
        assert "task_agent_llm_call_started" in types
        assert "task_agent_llm_call_completed" in types
        assert "task_agent_completed" in types
