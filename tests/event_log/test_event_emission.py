"""Tests that root-level v1 events are emitted from the expected code paths."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wichy.context.handler import ContextHandler, new_context
from wichy.root_agent.root_agent import ContextResetStrategies, RootAgent
from wichy.tools.base import BaseTool, ParametersModel


class MockToolParameters(ParametersModel):
    """Parameters model for mock tool."""

    value: str


class MockTool(BaseTool):
    """Mock tool for testing."""

    name: str = "mock_tool"
    description: str = "A mock tool for testing"
    parameters_model = MockToolParameters

    def execute(self, **kwargs) -> str:
        return f"result: {kwargs.get('value')}"


def _root_agent_with_context(context: ContextHandler) -> RootAgent:
    return RootAgent(
        model_str="test/model",
        tools=[MockTool()],
        context=context,
        name="test-agent",
        agent_has_first_initiative=False,
        print_info_lines=False,
    )


@pytest.fixture
def temp_contexts_dir():
    """Create a temporary directory for contexts and patch settings."""
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with patch("wichy.context.handler.settings") as mock_settings:
            mock_settings.contexts_dir = tmp_path
            yield tmp_path


class TestRootAgentProcessEvents:
    """Events emitted during RootAgent.process()."""

    def test_process_emits_user_message_and_llm_events(self, temp_contexts_dir):
        ctx = new_context()
        agent = _root_agent_with_context(ctx)

        emitted = []

        def fake_emit(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))

        with patch.object(agent, "_emit_event", side_effect=fake_emit):
            with patch("wichy.root_agent.root_agent.call") as mock_call:
                response = MagicMock()
                response.message = MagicMock()
                response.message.content = "hello"
                response.message.finish_reason = "stop"
                response.message.tool_calls = None
                response.message.reasoning = None
                response.usage = None
                mock_call.return_value = response
                agent.process("hi")

        types = [e[0] for e in emitted]
        assert "user_message_received" in types
        assert "llm_call_started" in types
        assert "llm_call_completed" in types
        assert "root_agent_response_ready" in types

    def test_process_emits_tool_call_events(self, temp_contexts_dir):
        ctx = new_context()
        agent = _root_agent_with_context(ctx)

        emitted = []

        def fake_emit(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))

        with patch.object(agent, "_emit_event", side_effect=fake_emit):
            with patch("wichy.root_agent.root_agent.call") as mock_call:
                first_response = MagicMock()
                first_response.message = MagicMock()
                first_response.message.content = ""
                first_response.message.finish_reason = "tool_calls"
                first_response.message.reasoning = None
                first_response.message.tool_calls = [
                    MagicMock(
                        id="call_1",
                        function=MagicMock(name="mock_tool", arguments='{"value": "x"}'),
                        model_dump=lambda: {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "mock_tool", "arguments": '{"value": "x"}'},
                        },
                    )
                ]
                first_response.usage = None

                final_response = MagicMock()
                final_response.message = MagicMock()
                final_response.message.content = "done"
                final_response.message.finish_reason = "stop"
                final_response.message.tool_calls = None
                final_response.message.reasoning = None
                final_response.usage = None

                mock_call.side_effect = [first_response, final_response]
                agent.process("run tool")

        types = [e[0] for e in emitted]
        assert "tool_call_batch_started" in types
        assert "tool_call_started" in types
        assert "tool_call_completed" in types
        assert types.count("llm_call_started") == 2
        assert types.count("llm_call_completed") == 2

    def test_steer_emits_steer_injected(self, temp_contexts_dir):
        ctx = new_context()
        agent = _root_agent_with_context(ctx)

        emitted = []

        def fake_emit(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))

        with patch.object(agent, "_emit_event", side_effect=fake_emit):
            agent.steer(role="user", content="steer message")

        assert any(e[0] == "steer_injected" for e in emitted)

    def test_reset_emits_context_reset(self, temp_contexts_dir):
        ctx = new_context()
        ctx.append({"role": "system", "content": "sys"})
        agent = _root_agent_with_context(ctx)
        time.sleep(1.1)

        emitted = []

        def fake_emit(event_type: str, payload: dict) -> None:
            emitted.append((event_type, payload))

        with patch.object(agent, "_emit_event", side_effect=fake_emit):
            agent.reset_context(ContextResetStrategies.NUKE)

        assert any(e[0] == "context_reset" for e in emitted)
