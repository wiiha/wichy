"""Tests for the slash command hook decorators."""

import pytest

from wichy.hooks import (
    clear_hooks,
    get_hooks_for_tool,
    get_hooks_for_type,
    get_slash_commands,
    HookResult,
    post_slash_command,
    pre_slash_command,
    slash_command,
)
from wichy.hooks.types import HookType


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the hook registry before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


class TestSlashCommandDecorator:
    """Registration behavior of @slash_command."""

    def test_registers_under_slash_command_type(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_tool(HookType.SLASH_COMMAND, "/deploy")
        assert len(hooks) == 1
        assert hooks[0].function is run_deploy
        assert hooks[0].name == "run_deploy"

    def test_command_name_normalized_lowercase_and_slash(self):
        @slash_command("Deploy")
        def handler_a(ctx) -> HookResult:
            return HookResult.approve()

        assert len(get_hooks_for_tool(HookType.SLASH_COMMAND, "/deploy")) == 1
        # Both spellings resolve to the same registration target.
        assert "/DEPLOY" not in get_slash_commands(HookType.SLASH_COMMAND)

    def test_leading_slash_already_present_is_not_doubled(self):
        @slash_command("/git-status")
        def handler(ctx) -> HookResult:
            return HookResult.approve()

        assert len(get_hooks_for_tool(HookType.SLASH_COMMAND, "/git-status")) == 1

    def test_empty_command_name_raises(self):
        with pytest.raises(ValueError):
            slash_command("   ")

        with pytest.raises(ValueError):
            slash_command("")

    def test_metadata_stores_description_and_args(self):
        @slash_command(
            "/deploy",
            description="Deploy the current branch",
            args={"--env": {"prod": None, "staging": None}},
        )
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_tool(HookType.SLASH_COMMAND, "/deploy")
        assert hooks[0].metadata["description"] == "Deploy the current branch"
        assert hooks[0].metadata["args"] == {"--env": {"prod": None, "staging": None}}

    def test_default_metadata_is_empty_description_none_args(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_tool(HookType.SLASH_COMMAND, "/deploy")
        assert hooks[0].metadata == {"description": "", "args": None}

    def test_priority_orders_multiple_handlers(self):
        @slash_command("/deploy", priority=90)
        def late(ctx) -> HookResult:
            return HookResult.approve()

        @slash_command("/deploy", priority=10)
        def early(ctx) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_tool(HookType.SLASH_COMMAND, "/deploy")
        assert [h.name for h in hooks] == ["early", "late"]

    def test_explicit_hook_name(self):
        @slash_command("/deploy", name="custom_name")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_tool(HookType.SLASH_COMMAND, "/deploy")
        assert hooks[0].name == "custom_name"


class TestPreSlashCommandDecorator:
    """Registration behavior of @pre_slash_command."""

    def test_wildcard_when_command_omitted(self):
        @pre_slash_command()
        def guard(ctx) -> HookResult:
            return HookResult.approve()

        # Wildcards are not in get_slash_commands; check via the registry.
        hooks = get_hooks_for_type(HookType.PRE_SLASH_COMMAND)
        assert len(hooks) == 1
        assert hooks[0].tool_name is None
        assert hooks[0].name == "guard"

    def test_command_specific_registration(self):
        @pre_slash_command("/reset")
        def guard_reset(ctx) -> HookResult:
            return HookResult.approve()

        specific = get_hooks_for_tool(HookType.PRE_SLASH_COMMAND, "/reset")
        assert len(specific) == 1
        assert specific[0].tool_name == "/reset"

    def test_command_name_normalized(self):
        @pre_slash_command("Reset")
        def guard_reset(ctx) -> HookResult:
            return HookResult.approve()

        assert len(get_hooks_for_tool(HookType.PRE_SLASH_COMMAND, "/reset")) == 1

    def test_specific_and_wildcard_merge_priority_sorted(self):
        @pre_slash_command(priority=90)
        def late_guard(ctx) -> HookResult:
            return HookResult.approve()

        @pre_slash_command("/reset", priority=10)
        def early_reset_guard(ctx) -> HookResult:
            return HookResult.approve()

        merged = get_hooks_for_tool(HookType.PRE_SLASH_COMMAND, "/reset")
        assert [h.name for h in merged] == ["early_reset_guard", "late_guard"]

    def test_empty_command_raises(self):
        with pytest.raises(ValueError):
            pre_slash_command("  ")


class TestPostSlashCommandDecorator:
    """Registration behavior of @post_slash_command."""

    def test_wildcard_when_command_omitted(self):
        @post_slash_command()
        def stamp(ctx) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.POST_SLASH_COMMAND)
        assert len(hooks) == 1
        assert hooks[0].tool_name is None

    def test_command_specific_registration(self):
        @post_slash_command("/status")
        def stamp_status(ctx) -> HookResult:
            return HookResult.approve()

        assert len(get_hooks_for_tool(HookType.POST_SLASH_COMMAND, "/status")) == 1

    def test_command_name_normalized(self):
        @post_slash_command("Status")
        def stamp_status(ctx) -> HookResult:
            return HookResult.approve()

        assert len(get_hooks_for_tool(HookType.POST_SLASH_COMMAND, "/status")) == 1


class TestGetSlashCommandsHelper:
    """The registry helper used by /help, list_commands, and completion."""

    def test_returns_command_name_map(self):
        @slash_command("/deploy", description="Deploy the current branch")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        @slash_command("/git-status")
        def git_status(ctx) -> HookResult:
            return HookResult.approve()

        commands = get_slash_commands(HookType.SLASH_COMMAND)
        assert set(commands.keys()) == {"/deploy", "/git-status"}
        assert (
            commands["/deploy"].metadata["description"] == "Deploy the current branch"
        )

    def test_excludes_wildcard_hooks(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        @pre_slash_command()
        def guard(ctx) -> HookResult:
            return HookResult.approve()

        assert set(get_slash_commands(HookType.PRE_SLASH_COMMAND).keys()) == set()

    def test_live_lookup_reflects_reload(self):
        @slash_command("/deploy")
        def run_deploy(ctx) -> HookResult:
            return HookResult.approve()

        assert "/deploy" in get_slash_commands(HookType.SLASH_COMMAND)

        clear_hooks()  # what /hooks reload does to the registry

        assert get_slash_commands(HookType.SLASH_COMMAND) == {}
