"""Tests for ContextHandler.steer() method."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from wichy.context.handler import ContextHandler


@pytest.fixture
def temp_contexts_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with patch("wichy.context.handler.settings") as mock_settings:
            mock_settings.contexts_dir = tmp_path
            yield tmp_path


class TestContextHandlerSteer:
    """Test ContextHandler.steer() method."""

    def test_steer_appends_message_with_correct_role_and_content(
        self, temp_contexts_dir
    ):
        """Test steer() appends message to context with correct role and content."""
        with patch("wichy.console.user_console.print"):
            ctx = ContextHandler()
            ctx.steer("user", "Hello world")
            assert len(ctx) == 1
            assert ctx.context[0] == {
                "role": "user",
                "content": "Hello world",
                "_tick": 0,
            }

    def test_steer_calls_user_console_print(self, temp_contexts_dir):
        """Test steer() calls user_console.print with formatted italic message."""
        with patch("wichy.console.user_console.print") as mock_print:
            ctx = ContextHandler()
            ctx.steer("user", "Hello world")
            expected = "[italic]steer injected (user): Hello world[/italic]"
            mock_print.assert_called_once_with(expected)

    def test_steer_works_with_different_roles(self, temp_contexts_dir):
        """Test steer() works with various roles."""
        with patch("wichy.console.user_console.print"):
            ctx = ContextHandler()
            ctx.steer("system", "System instruction")
            ctx.steer("user", "User message")
            ctx.steer("assistant", "Assistant reply")
            assert len(ctx) == 3
            assert ctx.context[0]["role"] == "system"
            assert ctx.context[1]["role"] == "user"
            assert ctx.context[2]["role"] == "assistant"

    def test_steer_long_content_truncates_print_message(self, temp_contexts_dir):
        """Test steer() truncates console print for long content but stores full content."""
        long_content = "a" * 100
        with patch("wichy.console.user_console.print") as mock_print:
            ctx = ContextHandler()
            ctx.steer("user", long_content)
            expected = f"[italic]steer injected (user): {'a' * 80}...[/italic]"
            mock_print.assert_called_once_with(expected)
            assert ctx.context[0]["content"] == long_content

    def test_steer_persists_to_jsonl_file(self, temp_contexts_dir):
        """Test steer() persists the full message to the JSONL file."""
        with patch("wichy.console.user_console.print"):
            ctx = ContextHandler()
            ctx.steer("system", "Persist this")
            save_path = ctx._gen_save_path()
            assert save_path.exists()
            line = save_path.read_text().strip().split("\n")[0]
            data = json.loads(line)
            assert data["type"] == "message"
            assert "timestamp" in data
            assert data["role"] == "system"
            assert data["content"] == "Persist this"
            assert data["_tick"] == 0
