"""Tests for slash command handlers."""

import io
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console
from rich.table import Table

from wichy.hooks import (
    clear_hooks,
    HookResult,
    post_slash_command,
    pre_slash_command,
    pre_tool,
    session_start,
    slash_command,
)
from wichy.slash_commands import (
    ContextResetException,
    SlashCommandChecker,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the hook registry before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


def _table_to_text(table: Table) -> str:
    """Render a Rich Table to a string for assertions.

    Width is fixed wide so long cells (e.g. a hook name plus its command
    description in /hooks) are not truncated or wrapped by the test
    console's default 80 columns.
    """
    console = Console(file=io.StringIO(), force_terminal=False, width=300)
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


class TestHookCommandDispatch:
    """Full check_command flows for hook-registered commands and observers."""

    def test_custom_command_returns_modified_output(self):
        @slash_command("/deploy", description="Deploy the current branch")
        def run_deploy(ctx) -> HookResult:
            return HookResult.modify_output("deployed!")

        checker = SlashCommandChecker(root_agent=None)
        assert checker.check_command("/deploy") == "deployed!"

    def test_custom_command_consumes_line_even_without_output(self):
        """A hook command that produces nothing is still NOT sent to the
        agent: the empty string (not None) is returned."""

        @slash_command("/silent")
        def silent(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        result = checker.check_command("/silent")
        assert result == ""  # "" is falsy but NOT None: callers print it.

    def test_builtin_wins_over_hook_command(self):
        seen = []

        @slash_command("/status")
        def fake_status(ctx) -> HookResult:
            seen.append("hook ran")
            return HookResult.modify_output("hook status")

        checker = SlashCommandChecker(root_agent=_stub_agent())
        result = checker.check_command("/status")
        assert result.startswith("[Status]")  # the built-in's output
        assert seen == []  # the hook never ran

    def test_unknown_command_still_unknown_without_hook(self):
        checker = SlashCommandChecker(root_agent=None)
        assert checker.check_command("/nope") == "Unknown command: /nope"

    def test_args_passed_to_custom_command(self):
        seen = {}

        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            seen["args"] = ctx.event_data["args"]
            seen["command"] = ctx.event_data["command"]
            seen["line"] = ctx.event_data["line"]
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        checker.check_command("/deploy prod --fast")
        assert seen == {
            "args": "prod --fast",
            "command": "/deploy",
            "line": "/deploy prod --fast",
        }

    def test_command_matching_is_case_insensitive(self):
        seen = []

        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            seen.append(ctx.event_data["command"])
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        checker.check_command("/DEPLOY")
        assert seen == ["/deploy"]

    def test_custom_command_control_exception_propagates(self):
        from wichy.root_agent.root_agent import ContextResetStrategies

        @slash_command("/nuke")
        def nuke(ctx) -> HookResult:
            raise ContextResetException(ContextResetStrategies.NUKE)

        checker = SlashCommandChecker(root_agent=None)
        with pytest.raises(ContextResetException) as excinfo:
            checker.check_command("/nuke")
        assert excinfo.value.strategy == ContextResetStrategies.NUKE

    def test_custom_command_eof_propagates(self):
        @slash_command("/quit")
        def quitter(ctx) -> HookResult:
            raise EOFError

        checker = SlashCommandChecker(root_agent=None)
        with pytest.raises(EOFError):
            checker.check_command("/quit")

    def test_non_command_line_returns_none(self):
        checker = SlashCommandChecker(root_agent=None)
        assert checker.check_command("hello world") is None

    def test_denied_custom_command_shows_blocked_message(self):
        @slash_command("/deploy")
        def blocked(ctx) -> HookResult:
            return HookResult.deny("no Friday deploys")

        checker = SlashCommandChecker(root_agent=None)
        result = checker.check_command("/deploy")
        assert "Blocked by hook blocked" in result
        assert "no Friday deploys" in result


class TestPreSlashDispatch:
    """PRE_SLASH_COMMAND observer flows through check_command."""

    def test_pre_hook_sees_every_line_including_unknown(self):
        seen = []

        @pre_slash_command()
        def observe(ctx) -> HookResult:
            seen.append((ctx.event_data["command"], ctx.event_data["source"]))
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=_stub_agent())
        checker.check_command("/status")
        checker.check_command("/nope")
        assert seen == [("/status", "builtin"), ("/nope", "unknown")]

    def test_pre_hook_deny_blocks_builtin(self):
        @pre_slash_command("/reset")
        def guard(ctx) -> HookResult:
            return HookResult.deny("reset disabled in prod")

        checker = SlashCommandChecker(root_agent=_stub_agent())
        result = checker.check_command("/reset")
        assert "Blocked by hook guard" in result
        assert "reset disabled in prod" in result
        # The built-in never ran: no ContextResetException was raised.

    def test_pre_hook_deny_suppresses_post_hooks(self):
        calls = []

        @pre_slash_command("/status")
        def deny_status(ctx) -> HookResult:
            calls.append("pre")
            return HookResult.deny("no")

        @post_slash_command()
        def never(ctx) -> HookResult:
            calls.append("post")
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=_stub_agent())
        checker.check_command("/status")
        assert calls == ["pre"]

    def test_pre_hook_rewrites_args_for_builtin(self):
        seen = []

        @pre_slash_command("/name")
        def rewrite(ctx) -> HookResult:
            return HookResult.modify_input({"args": "NewName"})

        checker = SlashCommandChecker(root_agent=_stub_agent(display_name="Old"))
        result = checker.check_command("/name")
        assert "NewName" in result  # the built-in saw the rewritten args
        assert seen == []

    def test_pre_hook_cannot_rewrite_command_token(self):
        @pre_slash_command()
        def try_rewrite(ctx) -> HookResult:
            return HookResult.modify_input({"command": "/reset", "args": ""})

        checker = SlashCommandChecker(root_agent=_stub_agent())
        # /status dispatches as /status; the injected "command" key is ignored.
        result = checker.check_command("/status")
        assert result.startswith("[Status]")

    def test_pre_hook_rewritten_args_reach_custom_command(self):
        seen = {}

        @pre_slash_command("/deploy")
        def rewrite(ctx) -> HookResult:
            return HookResult.modify_input({"args": "staging"})

        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            seen["args"] = ctx.event_data["args"]
            seen["line"] = ctx.event_data["line"]
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        checker.check_command("/deploy prod")
        assert seen == {"args": "staging", "line": "/deploy staging"}


class TestPostSlashDispatch:
    """POST_SLASH_COMMAND observer flows through check_command."""

    def test_post_hook_sees_builtin_result(self):
        seen = {}

        @post_slash_command("/status")
        def observe(ctx) -> HookResult:
            seen["result"] = ctx.event_data["result"]
            seen["source"] = ctx.event_data["source"]
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=_stub_agent())
        checker.check_command("/status")
        assert seen["source"] == "builtin"
        assert "[Status]" in seen["result"]

    def test_post_hook_can_replace_result(self):
        @post_slash_command("/status")
        def censor(ctx) -> HookResult:
            return HookResult.modify_output("[REDACTED]")

        checker = SlashCommandChecker(root_agent=_stub_agent())
        assert checker.check_command("/status") == "[REDACTED]"

    def test_post_hook_does_not_run_when_builtin_raises(self):
        calls = []

        @post_slash_command("/reset")
        def never(ctx) -> HookResult:
            calls.append("post")
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=_stub_agent())
        with pytest.raises(ContextResetException):
            checker.check_command("/reset")
        assert calls == []

    def test_post_hook_sees_unknown_command_result(self):
        seen = {}

        @post_slash_command()
        def observe(ctx) -> HookResult:
            seen["source"] = ctx.event_data["source"]
            seen["result"] = ctx.event_data["result"]
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        checker.check_command("/nope")
        assert seen == {"source": "unknown", "result": "Unknown command: /nope"}

    def test_post_hook_scoped_to_command(self):
        calls = []

        @post_slash_command("/deploy")
        def deploy_post(ctx) -> HookResult:
            calls.append("deploy_post")
            return HookResult.approve()

        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=_stub_agent())
        checker.check_command("/status")
        assert calls == []
        checker.check_command("/deploy")
        assert calls == ["deploy_post"]


class TestDiscoverySurfaces:
    """list_commands, /help, and the /hooks table expose hook commands."""

    def test_list_commands_merges_hook_commands(self):
        @slash_command("/deploy", description="Deploy the current branch")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        names = [c["name"] for c in checker.list_commands()]
        assert "/deploy" in names
        entry = next(c for c in checker.list_commands() if c["name"] == "/deploy")
        assert entry["description"] == "Deploy the current branch"

    def test_list_commands_builtin_wins_on_collision(self):
        @slash_command("/status")
        def fake_status(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        statuses = [c for c in checker.list_commands() if c["name"] == "/status"]
        assert len(statuses) == 1
        assert "session summary" in statuses[0]["description"]

    def test_help_table_lists_hook_commands(self):
        @slash_command("/deploy", description="Deploy the current branch")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        table = checker.check_command("/help")
        text = _table_to_text(table)
        assert "/deploy" in text
        assert "Deploy the current branch" in text

    def test_help_specific_command_documents_hook_command(self):
        @slash_command("/deploy", description="Deploy the current branch")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        assert (
            checker.check_command("/help /deploy")
            == "[bold]/deploy[/bold]: Deploy the current branch"
        )

    def test_help_specific_unknown_still_says_no_description(self):
        checker = SlashCommandChecker(root_agent=None)
        assert (
            checker.check_command("/help /nope")
            == "[bold]/nope[/bold]: No description available."
        )

    def test_hooks_table_shows_command_not_lifecycle_dash(self):
        @pre_slash_command("/reset")
        def guard(ctx) -> HookResult:
            return HookResult.approve()

        @patch("wichy.slash_commands.hook_loader")
        def run_table(mock_loader):
            checker = SlashCommandChecker(root_agent=None)
            result = checker.check_command("/hooks")
            return _table_to_text(result)

        text = run_table()
        # The Tool column must show the command name, not the lifecycle "-".
        assert "pre_slash_command" in text
        assert "/reset" in text

    def test_hooks_table_shows_custom_command_description(self):
        @slash_command("/deploy", description="Deploy the current branch")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        @patch("wichy.slash_commands.hook_loader")
        def run_table(mock_loader):
            checker = SlashCommandChecker(root_agent=None)
            return _table_to_text(checker.check_command("/hooks"))

        text = run_table()
        assert "Deploy the current branch" in text


class TestReloadLiveness:
    """Discovery and dispatch must reflect /hooks reload (registry wiped)."""

    def test_command_disappears_after_clear_hooks(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.modify_output("deployed!")

        checker = SlashCommandChecker(root_agent=None)
        assert checker.check_command("/deploy") == "deployed!"

        clear_hooks()  # what /hooks reload does

        assert checker.check_command("/deploy") == "Unknown command: /deploy"
        names = [c["name"] for c in checker.list_commands()]
        assert "/deploy" not in names

    def test_reregistered_command_works_again(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.modify_output("first")

        checker = SlashCommandChecker(root_agent=None)
        assert checker.check_command("/deploy") == "first"
        clear_hooks()

        @slash_command("/deploy")
        def run_deploy2(ctx) -> HookResult:
            return HookResult.modify_output("second")

        assert checker.check_command("/deploy") == "second"


class TestLiveCompletion:
    """Completion must reflect registry state at access time."""

    def test_completion_dict_includes_hook_commands(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        from wichy.slash_commands import completion_dict

        assert "/deploy" in completion_dict()
        assert completion_dict()["/deploy"] is None  # no args hint

    def test_completion_dict_uses_args_metadata(self):
        @slash_command(
            "/deploy",
            args={"--env": {"prod": None, "staging": None}},
        )
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        from wichy.slash_commands import completion_dict

        assert completion_dict()["/deploy"] == {
            "--env": {"prod": None, "staging": None}
        }

    def test_completion_dict_builtin_wins_on_collision(self):
        @slash_command("/logging", args={"hijack": None})
        def fake_logging(ctx) -> HookResult:
            return HookResult.approve()

        from wichy.slash_commands import completion_dict

        # The built-in's on/off structure, not the hook's args.
        assert completion_dict()["/logging"] == {"on": None, "off": None}

    def test_completion_dict_reflects_reload(self):
        from wichy.slash_commands import completion_dict

        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        assert "/deploy" in completion_dict()
        clear_hooks()
        assert "/deploy" not in completion_dict()

    def test_checker_completer_property_builds_live(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        checker = SlashCommandChecker(root_agent=None)
        completer = checker.completer
        # NestedCompleter's options dict carries the hook command.
        assert "/deploy" in completer.options

        clear_hooks()
        completer2 = checker.completer
        assert "/deploy" not in completer2.options

    def test_module_completer_is_dynamic(self):
        # The PromptSession binds this object BEFORE hooks load, so it must
        # re-evaluate on every completion request, not snapshot at import.
        from prompt_toolkit.completion import DynamicCompleter

        from wichy.slash_commands import slash_completer

        assert isinstance(slash_completer, DynamicCompleter)
