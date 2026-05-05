"""Tests for RootAgent.steer()."""

from unittest.mock import MagicMock, patch

import pytest

from wichy.root_agent.root_agent import RootAgent
from wichy.constants import ROLE_USER


class MockContext:
    """Minimal mock context that tracks steer() calls."""

    def __init__(self):
        self.steer_calls = []

    def steer(self, role, content):
        self.steer_calls.append((role, content))

    def add_log(self, log_entry):
        pass

    def start_watching(self, interval):
        pass


class TestRootAgentSteer:
    """Tests for the RootAgent.steer() delegation method."""

    @pytest.fixture(autouse=True)
    def setup_agent(self):
        """Set up a minimal RootAgent with mocked dependencies."""
        self.mock_context = MockContext()
        self.mock_tool = MagicMock()
        self.mock_tool.name = "mock_tool"

        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
        ):
            self.agent = RootAgent(
                model_str="ollama/test",
                tools=[self.mock_tool],
                context=self.mock_context,
                name="test-agent",
                agent_has_first_initiative=False,
            )

    def test_steer_delegates_with_default_args(self):
        """steer() should delegate to context.steer() with default role=user and empty content."""
        self.agent.steer()
        assert len(self.mock_context.steer_calls) == 1
        role, content = self.mock_context.steer_calls[0]
        assert role == ROLE_USER
        assert content == ""

    def test_steer_delegates_with_custom_args(self):
        """steer() should delegate to context.steer() with the provided role and content."""
        self.agent.steer(role="system", content="Be helpful")
        assert len(self.mock_context.steer_calls) == 1
        role, content = self.mock_context.steer_calls[0]
        assert role == "system"
        assert content == "Be helpful"
