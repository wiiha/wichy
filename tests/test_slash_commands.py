"""Tests for slash command handlers."""

import io
from unittest.mock import patch

import pytest
from rich.console import Console
from rich.table import Table

from wichy.hooks import clear_hooks, HookResult, pre_tool, session_start
from wichy.slash_commands import SlashCommandChecker


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the hook registry before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


def _table_to_text(table: Table) -> str:
    """Render a Rich Table to a string for assertions."""
    console = Console(file=io.StringIO(), force_terminal=False)
    console.print(table)
    return console.file.getvalue()


@patch("wichy.slash_commands.hook_loader")
def test_hooks_command_lists_lifecycle_and_tool_hooks(mock_hook_loader):
    """/hooks should display every registered hook type, not only PRE_TOOL/POST_TOOL."""
    mock_hook_loader.reload_hooks.return_value = None

    @pre_tool("bash")
    def pre_bash(ctx) -> HookResult:
        return HookResult.approve()

    @session_start
    def on_session_start(ctx) -> HookResult:
        return HookResult.approve()

    checker = SlashCommandChecker(root_agent=None)
    result = checker.check_command("/hooks")

    assert isinstance(result, Table)
    text = _table_to_text(result)

    assert "pre_tool" in text
    assert "bash" in text
    assert "pre_bash" in text
    assert "session_start" in text
    assert "on_session_start" in text
    # Lifecycle hooks should not be mislabelled as wildcard tool hooks.
    assert " - " in text


@patch("wichy.slash_commands.hook_loader")
def test_hooks_command_empty_registry(mock_hook_loader):
    """/hooks should return a clear message when no hooks are registered."""
    mock_hook_loader.reload_hooks.return_value = None

    checker = SlashCommandChecker(root_agent=None)
    result = checker.check_command("/hooks")
    assert result == "[Hooks] No hooks registered"
