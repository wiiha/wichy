"""Tests for stable session identity across context reset, compact, and load."""

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from wichy.context.handler import (
    SESSION_BOUND_EVENT,
    ContextHandler,
    context_from_file,
    new_context,
)
from wichy.root_agent.root_agent import ContextResetStrategies, RootAgent
from wichy.tools.base import BaseTool, ParametersModel


@pytest.fixture
def temp_contexts_dir():
    """Create a temporary directory for contexts and patch settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with patch("wichy.context.handler.settings") as mock_settings:
            mock_settings.contexts_dir = tmp_path
            yield tmp_path


class MockToolParameters(ParametersModel):
    """Parameters model for mock tool."""

    pass


class MockTool(BaseTool):
    """Mock tool for testing."""

    name: str = "mock_tool"
    description: str = "A mock tool for testing"
    parameters_model = MockToolParameters

    def execute(self, **kwargs) -> str:
        return "Mocked result"


def _root_agent_with_context(context: ContextHandler) -> RootAgent:
    return RootAgent(
        model_str="test/model",
        tools=[MockTool()],
        context=context,
        name="test-agent",
        agent_has_first_initiative=False,
        print_info_lines=False,
    )


class TestNewContext:
    """Fresh contexts receive a session_bound log."""

    def test_new_context_writes_session_bound(self, temp_contexts_dir):
        ctx = new_context()
        assert ctx.session_id
        assert len(ctx.logs) == 1
        assert ctx.logs[0]["event"] == SESSION_BOUND_EVENT
        assert ctx.logs[0]["session_id"] == ctx.session_id

    def test_new_context_persists_session_bound_to_disk(self, temp_contexts_dir):
        ctx = new_context()
        ctx.append({"role": "system", "content": "sys"})
        lines = ctx.path.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]
        assert entries[0]["event"] == SESSION_BOUND_EVENT
        assert entries[0]["session_id"] == ctx.session_id


class TestContextFromFile:
    """Loading a context preserves or assigns session id."""

    def test_context_from_file_preserves_existing_session_id(self, temp_contexts_dir):
        ctx = new_context()
        ctx.append({"role": "system", "content": "sys"})
        ctx.append({"role": "user", "content": "hi"})

        loaded = context_from_file(ctx.path)
        assert loaded.session_id == ctx.session_id
        assert loaded.logs[0]["event"] == SESSION_BOUND_EVENT

    def test_context_from_file_generates_session_id_for_legacy_file(
        self, temp_contexts_dir
    ):
        # context_from_file parses id from the filename, so use the expected format.
        context_file = temp_contexts_dir / "2024-01-01_12345_legacy.json"
        lines = [
            json.dumps({"role": "system", "content": "sys"}),
            json.dumps({"role": "user", "content": "hi"}),
        ]
        context_file.write_text("\n".join(lines))

        loaded = context_from_file(context_file)
        assert loaded.session_id
        # File must not be mutated on load.
        assert context_file.read_text().strip() == "\n".join(lines)


class TestSessionIdentityAcrossResetCompact:
    """Reset and compact preserve the stable session id."""

    def test_reset_preserves_session_id(self, temp_contexts_dir):
        ctx = new_context()
        ctx.append({"role": "system", "content": "sys"})
        ctx.append({"role": "user", "content": "hi"})
        original_session_id = ctx.session_id

        # Sleep to ensure the new context file gets a different filename/id.
        time.sleep(1.1)
        agent = _root_agent_with_context(ctx)
        agent.reset_context(ContextResetStrategies.NUKE)

        assert agent.context.session_id == original_session_id
        # New file should contain exactly one session_bound with resumed_after=reset.
        lines = agent.context.path.read_text().strip().split("\n")
        bound = [json.loads(line) for line in lines if "session_bound" in line]
        assert len(bound) == 1
        assert bound[0]["resumed_after"] == "reset"

    def test_compact_preserves_session_id(self, temp_contexts_dir):
        ctx = new_context()
        ctx.append({"role": "system", "content": "sys"})
        ctx.append({"role": "user", "content": "hi"})
        original_session_id = ctx.session_id

        time.sleep(1.1)
        agent = _root_agent_with_context(ctx)

        # Mock the LLM call used for summarization.
        class FakeResponse:
            message = type("M", (), {"content": "summary"})()

        with patch("wichy.root_agent.root_agent.call", return_value=FakeResponse()):
            agent.compact_context(is_auto_compact=True)

        assert agent.context.session_id == original_session_id
        lines = agent.context.path.read_text().strip().split("\n")
        bound = [json.loads(line) for line in lines if "session_bound" in line]
        assert len(bound) == 1
        assert bound[0]["resumed_after"] == "compact"
