"""Tests for slash command handlers."""

import io
from types import SimpleNamespace
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


def _stub_agent(
    model_str: str = "ollama/test",
    display_name: str = "Assistant",
    messages: int = 3,
    tokens: int = 0,
    threshold: int | None = None,
):
    """Build a stub root agent covering every field handle_status reads."""
    return SimpleNamespace(
        model_str=model_str,
        display_name=display_name,
        context=SimpleNamespace(context=[f"m{i}" for i in range(messages)]),
        current_prompt_tokens=tokens,
        auto_compact_threshold=threshold,
    )


class TestStatusCommand:
    """Output-shape tests for /status (exact string matches)."""

    def test_status_threshold_none(self):
        checker = SlashCommandChecker(root_agent=_stub_agent(threshold=None))
        result = checker.check_command("/status")
        assert result == (
            "[Status] Session summary\n"
            "Model: ollama/test\n"
            "Name: Assistant\n"
            "Messages: 3\n"
            "Tokens (last request): 0\n"
            "Auto-compact: off"
        )

    def test_status_threshold_zero_treated_as_off(self):
        """Threshold 0 is falsy in check_token_threshold; display must match."""
        checker = SlashCommandChecker(root_agent=_stub_agent(threshold=0))
        result = checker.check_command("/status")
        assert result.endswith("Auto-compact: off")

    def test_status_threshold_set_tokens_zero_no_percent(self):
        """Fresh session (tokens 0) shows bare threshold, not a 0% noise line."""
        checker = SlashCommandChecker(root_agent=_stub_agent(threshold=20000))
        result = checker.check_command("/status")
        assert result.endswith("Auto-compact: 20000")

    def test_status_threshold_set_with_percent(self):
        """tokens=5000 threshold=20000 -> floor(25)% exactly."""
        checker = SlashCommandChecker(
            root_agent=_stub_agent(threshold=20000, tokens=5000)
        )
        result = checker.check_command("/status")
        assert result.endswith("Auto-compact: 20000 (25%)")

    def test_status_percent_over_100_renders(self):
        """Compaction overdue: pct > 100 displays without clamping."""
        checker = SlashCommandChecker(
            root_agent=_stub_agent(threshold=1000, tokens=2500)
        )
        result = checker.check_command("/status")
        assert result.endswith("Auto-compact: 1000 (250%)")

    def test_status_percent_floors_not_rounds(self):
        """5000/30000 = 16.67% -> 16, not 17."""
        checker = SlashCommandChecker(
            root_agent=_stub_agent(threshold=30000, tokens=5000)
        )
        result = checker.check_command("/status")
        assert result.endswith("Auto-compact: 30000 (16%)")

    def test_status_reads_all_fields(self):
        checker = SlashCommandChecker(
            root_agent=_stub_agent(
                model_str="openai/gpt-4o",
                display_name="Helper",
                messages=42,
                tokens=12453,
                threshold=200000,
            )
        )
        result = checker.check_command("/status")
        assert "Model: openai/gpt-4o" in result
        assert "Name: Helper" in result
        assert "Messages: 42" in result
        assert "Tokens (last request): 12453" in result
        assert "Auto-compact: 200000 (6%)" in result

    def test_status_plain_text_no_leading_spaces(self):
        """INV-006: no line may start with 4+ spaces (web chat code-block trap)."""
        checker = SlashCommandChecker(
            root_agent=_stub_agent(threshold=20000, tokens=5000)
        )
        for line in checker.check_command("/status").splitlines():
            assert not line.startswith(" ")

    def test_status_case_insensitive_and_ignores_args(self):
        checker = SlashCommandChecker(root_agent=_stub_agent())
        assert checker.check_command("/STATUS").startswith("[Status]")
        assert checker.check_command("/status foo bar").startswith("[Status]")
