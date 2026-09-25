"""Tests for the slash hook executor path (HookExecutor.run_slash_hooks)."""

import pytest

from wichy.console import user_console
from wichy.hooks import (
    clear_hooks,
    HookResult,
    post_slash_command,
    pre_slash_command,
    slash_command,
)
from wichy.hooks.executor import HookExecutor
from wichy.hooks.slash_exceptions import (
    SlashBtwException,
    SlashContextDropException,
    SlashContextResetException,
)
from wichy.hooks.types import HookType


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the hook registry before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


def run_pre(root_agent=None, command="/deploy", args="prod", line="/deploy prod"):
    return HookExecutor.run_slash_hooks(
        hook_type=HookType.PRE_SLASH_COMMAND,
        root_agent=root_agent,
        command=command,
        args=args,
        line=line,
        source="builtin",
    )


def run_command(root_agent=None, command="/deploy", args="", line="/deploy"):
    return HookExecutor.run_slash_hooks(
        hook_type=HookType.SLASH_COMMAND,
        root_agent=root_agent,
        command=command,
        args=args,
        line=line,
    )


def run_post(
    root_agent=None,
    command="/status",
    args="",
    line="/status",
    source="builtin",
    result="the result",
):
    return HookExecutor.run_slash_hooks(
        hook_type=HookType.POST_SLASH_COMMAND,
        root_agent=root_agent,
        command=command,
        args=args,
        line=line,
        source=source,
        result=result,
    )


class TestPreSlashHooks:
    """PRE_SLASH_COMMAND semantics: deny blocks, modify_input rewrites args."""

    def test_approve_by_default(self):
        @pre_slash_command()
        def hook(ctx):
            return HookResult.approve()

        res = run_pre()
        assert res.approved is True
        assert res.error_message is None
        assert res.hooks_executed == ["hook"]

    def test_deny_blocks_and_stops_later_hooks(self):
        @pre_slash_command(priority=10)
        def deny_hook(ctx):
            return HookResult.deny("not on my watch")

        @pre_slash_command(priority=90)
        def never_runs(ctx):
            raise AssertionError("should not run after a deny")

        res = run_pre()
        assert res.approved is False
        assert res.error_message == "not on my watch"
        assert res.hooks_denied == ["deny_hook"]
        assert "never_runs" not in res.hooks_executed

    def test_modify_input_args_recorded(self):
        @pre_slash_command()
        def rewrite(ctx):
            return HookResult.modify_input({"args": "--env staging"})

        res = run_pre()
        assert res.approved is True
        assert res.modified_input == {"args": "--env staging"}

    def test_modify_input_command_key_ignored(self):
        @pre_slash_command()
        def rewrite(ctx):
            # Attempts to rewrite the command token are ignored: only the
            # "args" key is honored, so "command" here is a no-op.
            return HookResult.modify_input({"command": "/reset", "args": "x"})

        res = run_pre()
        assert res.modified_input == {"args": "x"}

    def test_modify_input_command_only_records_nothing(self):
        @pre_slash_command()
        def rewrite(ctx):
            return HookResult.modify_input({"command": "/reset"})

        res = run_pre()
        assert res.modified_input is None

    def test_modify_input_non_string_args_ignored(self):
        @pre_slash_command()
        def rewrite(ctx):
            return HookResult.modify_input({"args": 42})

        res = run_pre()
        assert res.modified_input is None

    def test_event_data_contract(self):
        seen = {}

        @pre_slash_command()
        def observe(ctx):
            seen["command"] = ctx.event_data["command"]
            seen["args"] = ctx.event_data["args"]
            seen["line"] = ctx.event_data["line"]
            seen["source"] = ctx.event_data["source"]
            seen["root_agent"] = ctx.event_data["root_agent"]
            return HookResult.approve()

        run_pre(root_agent="RA", command="/deploy", args="prod", line="/deploy prod")
        assert seen == {
            "command": "/deploy",
            "args": "prod",
            "line": "/deploy prod",
            "source": "builtin",
            "root_agent": "RA",
        }

    def test_specific_command_scoping(self):
        calls = []

        @pre_slash_command("/reset")
        def reset_guard(ctx):
            calls.append("reset_guard")
            return HookResult.approve()

        @pre_slash_command()
        def wildcard(ctx):
            calls.append("wildcard")
            return HookResult.approve()

        HookExecutor.run_slash_hooks(
            hook_type=HookType.PRE_SLASH_COMMAND,
            root_agent=None,
            command="/status",
            args="",
            line="/status",
            source="builtin",
        )
        assert calls == ["wildcard"]

        run_pre(command="/reset", args="", line="/reset")
        # Both runs' entries are in `calls`; at equal priority the
        # wildcard runs before the specific hook.
        assert calls == ["wildcard", "wildcard", "reset_guard"]


class TestCustomCommandHooks:
    """SLASH_COMMAND semantics: output chaining, deny, exception handling."""

    def test_modify_output_chains_through_ctx_output(self):
        @slash_command("/deploy", priority=10)
        def first(ctx):
            return HookResult.modify_output(ctx.event_data["args"] + "!")

        @slash_command("/deploy", priority=90)
        def second(ctx):
            return HookResult.modify_output("[" + ctx.output + "]")

        res = run_command(args="prod", line="/deploy prod")
        assert res.approved is True
        assert res.modified_output == "[prod!]"

    def test_deny_produces_blocked_result(self):
        @slash_command("/deploy")
        def blocker(ctx):
            return HookResult.deny("no deploys on Friday")

        res = run_command()
        assert res.approved is False
        assert res.error_message == "no deploys on Friday"
        assert res.hooks_denied == ["blocker"]

    def test_all_approve_leaves_modified_output_none(self):
        @slash_command("/deploy")
        def silent(ctx):
            return HookResult.approve()

        res = run_command()
        assert res.approved is True
        assert res.modified_output is None

    def test_control_exception_propagates(self):
        from wichy.root_agent.root_agent import ContextResetStrategies

        @slash_command("/nuke")
        def nuke(ctx):
            raise SlashContextResetException(ContextResetStrategies.NUKE)

        with pytest.raises(SlashContextResetException):
            run_command(command="/nuke", line="/nuke")

    def test_drop_and_btw_control_exceptions_propagate(self):
        @slash_command("/dropit")
        def dropit(ctx):
            raise SlashContextDropException()

        with pytest.raises(SlashContextDropException):
            run_command(command="/dropit", line="/dropit")

        @slash_command("/ask")
        def ask(ctx):
            raise SlashBtwException("what?", "model", [])

        with pytest.raises(SlashBtwException):
            run_command(command="/ask", line="/ask")

    def test_eof_propagates(self):
        @slash_command("/quit")
        def quitter(ctx):
            raise EOFError

        with pytest.raises(EOFError):
            run_command(command="/quit", line="/quit")

    def test_other_exceptions_isolated(self):
        @slash_command("/deploy", priority=10)
        def broken(ctx):
            raise RuntimeError("boom")

        @slash_command("/deploy", priority=90)
        def healthy(ctx):
            return HookResult.modify_output("still ran")

        user_console.quiet = True
        try:
            res = run_command()
        finally:
            user_console.quiet = False
        assert res.approved is True
        # A hook that raised is not recorded as executed; the following
        # hooks still run.
        assert res.hooks_executed == ["healthy"]
        assert res.modified_output == "still ran"

    def test_disabled_hook_skipped(self):
        from wichy.hooks import hook_registry

        @slash_command("/deploy")
        def silent(ctx):
            return HookResult.approve()

        for h in hook_registry.list_all()[HookType.SLASH_COMMAND]["/deploy"]:
            h.enabled = False

        res = run_command()
        assert res.hooks_executed == []


class TestPostSlashHooks:
    """POST_SLASH_COMMAND semantics: result visible, modify_output replaces."""

    def test_result_in_event_data_and_ctx_output(self):
        seen = {}

        @post_slash_command()
        def observe(ctx):
            seen["result"] = ctx.event_data["result"]
            seen["output"] = ctx.output
            return HookResult.approve()

        run_post(result="hello")
        assert seen == {"result": "hello", "output": "hello"}

    def test_modify_output_replaces_result(self):
        @post_slash_command("/status")
        def stamp(ctx):
            return HookResult.modify_output(ctx.output + " -- stamped")

        res = run_post(result="the result")
        assert res.modified_output == "the result -- stamped"

    def test_modify_output_chains_across_hooks(self):
        @post_slash_command(priority=10)
        def add_a(ctx):
            return HookResult.modify_output(ctx.output + "a")

        @post_slash_command(priority=90)
        def add_b(ctx):
            return HookResult.modify_output(ctx.output + "b")

        res = run_post(result="x")
        assert res.modified_output == "xab"

    def test_no_modification_leaves_modified_output_none(self):
        @post_slash_command()
        def silent(ctx):
            return HookResult.approve()

        res = run_post(result="the result")
        assert res.modified_output is None

    def test_none_result_visible_to_hooks(self):
        seen = {}

        @post_slash_command()
        def observe(ctx):
            seen["result"] = ctx.event_data["result"]
            return HookResult.approve()

        run_post(result=None)
        assert seen["result"] is None

    def test_table_result_supported(self):
        from rich.table import Table

        table = Table(title="demo")

        seen = {}

        @post_slash_command()
        def observe(ctx):
            seen["result"] = ctx.event_data["result"]
            return HookResult.approve()

        run_post(result=table)
        assert seen["result"] is table

    def test_deny_records_blocked(self):
        @post_slash_command()
        def censor(ctx):
            return HookResult.deny("censored")

        res = run_post()
        assert res.approved is False
        assert res.error_message == "censored"

    def test_exception_isolated(self):
        @post_slash_command()
        def broken(ctx):
            raise RuntimeError("bang")

        user_console.quiet = True
        try:
            res = run_post()
        finally:
            user_console.quiet = False
        assert res.approved is True
        # A raised hook is not recorded as executed, and no modification
        # happened.
        assert res.hooks_executed == []
        assert res.modified_output is None


class TestEmptyRegistryFastPath:
    """No registered hooks -> immediate empty success result."""

    def test_pre_without_hooks(self):
        res = run_pre()
        assert res.approved is True
        assert res.hooks_executed == []
        assert res.modified_input is None

    def test_command_without_hooks(self):
        res = run_command()
        assert res.approved is True
        assert res.modified_output is None

    def test_post_without_hooks(self):
        res = run_post()
        assert res.approved is True
        assert res.modified_output is None
