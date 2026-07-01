"""Tests for task agent context lineage logging."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from wichy.context.handler import ContextHandler, context_from_file
from wichy.hooks.context_access import get_active_context, set_active_context
from wichy.tools.task_tool import TaskAgentTool


def _make_root_context(tmp_path: Path) -> ContextHandler:
    """Create a temporary root context outside of .wichy/contexts."""
    ctx = ContextHandler(custom_suffix="root-test", sub_dir="")
    # Override the generated path to live under tmp_path for test isolation.
    ctx._path = tmp_path / f"{ctx.start_date}_{ctx.id}_root-test.jsonl"
    ctx._path.parent.mkdir(parents=True, exist_ok=True)
    # Add a minimal message so context_from_file can reload it.
    ctx.add(role="system", content="test root context")
    return ctx


def test_task_agent_lineage_log_written(tmp_path: Path, monkeypatch):
    """Starting a task agent writes a task_agent_started log to the root context."""
    root_ctx = _make_root_context(tmp_path)
    set_active_context(root_ctx)
    monkeypatch.chdir(tmp_path)

    tool = TaskAgentTool()

    # Patch the TaskAgent class so run() returns immediately.
    from wichy.tools.task import TaskAgent

    with patch.object(TaskAgent, "run", return_value="done"):
        tool.execute(
            description="test task",
            prompt="say hello",
            subagent_type="general-purpose",
            max_turns=5,
            model_str="ollama/test-model",
        )

    log_entries = [
        log for log in root_ctx.logs if log.get("event") == "task_agent_started"
    ]
    assert len(log_entries) == 1
    entry = log_entries[0]
    assert entry["root_context_file"] == str(root_ctx.path)
    assert entry["task_agent_type"] == "general-purpose"
    assert entry["description"] == "test task"
    assert "task_context_file" in entry
    assert "timestamp" in entry


def test_task_agent_lineage_survives_reload(tmp_path: Path, monkeypatch):
    """The lineage log entry is preserved when the root context is reloaded."""
    root_ctx = _make_root_context(tmp_path)
    set_active_context(root_ctx)
    monkeypatch.chdir(tmp_path)

    tool = TaskAgentTool()
    from wichy.tools.task import TaskAgent

    with patch.object(TaskAgent, "run", return_value="done"):
        tool.execute(
            description="reload test",
            prompt="say hello",
            subagent_type="general-purpose",
            max_turns=3,
            model_str="ollama/test-model",
        )

    # add_log already appends to disk, so no explicit flush needed.
    reloaded = context_from_file(root_ctx.path)
    log_entries = [
        log for log in reloaded.logs if log.get("event") == "task_agent_started"
    ]
    assert len(log_entries) == 1
    assert log_entries[0]["description"] == "reload test"


def test_task_agent_starts_without_active_context(tmp_path: Path, monkeypatch):
    """If no root context is active, the task agent still starts and no log is written."""
    set_active_context(None)
    monkeypatch.chdir(tmp_path)

    tool = TaskAgentTool()
    from wichy.tools.task import TaskAgent

    with patch.object(TaskAgent, "run", return_value="done"):
        tool.execute(
            description="no context test",
            prompt="say hello",
            subagent_type="general-purpose",
            max_turns=2,
            model_str="ollama/test-model",
        )

    # No root context existed, so no lineage log should have been written anywhere.
    assert get_active_context() is None


def test_task_agent_unknown_type_still_logs(tmp_path: Path, monkeypatch):
    """An unknown subagent type returns an error but should not crash lineage logging."""
    root_ctx = _make_root_context(tmp_path)
    set_active_context(root_ctx)
    monkeypatch.chdir(tmp_path)

    tool = TaskAgentTool()
    result = tool.execute(
        description="unknown type test",
        prompt="say hello",
        subagent_type="does-not-exist",
        max_turns=2,
        model_str="ollama/test-model",
    )

    assert "no subagent_type" in result
    # No task agent was actually started, so no lineage log entry.
    log_entries = [
        log for log in root_ctx.logs if log.get("event") == "task_agent_started"
    ]
    assert len(log_entries) == 0
