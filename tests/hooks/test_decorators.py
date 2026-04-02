"""
Tests for the hook decorator functions.

This module tests the @pre_tool and @post_tool decorators which provide
a convenient API for registering hooks.
"""

import pytest

from wichy.hooks import (
    pre_tool,
    post_tool,
    HookType,
    HookResult,
    HookContext,
    clear_hooks,
    get_hooks_for_tool,
)


@pytest.fixture(autouse=True)
def setup_hooks():
    """Clear hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


def test_pre_tool_decorator():
    """Test that @pre_tool decorator registers a pre-tool hook."""

    @pre_tool("bash")
    def my_hook(ctx):
        return HookResult.approve()

    # Verify registration
    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 1
    assert hooks[0].name == "my_hook"
    assert hooks[0].tool_name == "bash"
    assert hooks[0].hook_type == HookType.PRE_TOOL


def test_post_tool_decorator():
    """Test that @post_tool decorator registers a post-tool hook."""

    @post_tool("read_file")
    def my_hook(ctx):
        return HookResult.approve()

    # Verify registration
    hooks = get_hooks_for_tool(HookType.POST_TOOL, "read_file")
    assert len(hooks) == 1
    assert hooks[0].name == "my_hook"
    assert hooks[0].tool_name == "read_file"
    assert hooks[0].hook_type == HookType.POST_TOOL


def test_decorator_with_tool_name():
    """Test decorator with explicit tool name specified."""

    @pre_tool("write_file")
    def check_write(ctx):
        return HookResult.approve()

    # Should be registered for write_file
    hooks_for_write = get_hooks_for_tool(HookType.PRE_TOOL, "write_file")
    assert len(hooks_for_write) == 1
    assert hooks_for_write[0].name == "check_write"
    assert hooks_for_write[0].tool_name == "write_file"

    # Should NOT be registered for other tools
    hooks_for_bash = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks_for_bash) == 0


def test_decorator_wildcard():
    """Test decorator without tool name (wildcard - applies to all tools)."""

    @pre_tool()  # No tool name = wildcard
    def log_all_calls(ctx):
        return HookResult.approve()

    # Should be available for any tool (wildcard)
    hooks_for_bash = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks_for_bash) == 1
    assert hooks_for_bash[0].name == "log_all_calls"
    assert hooks_for_bash[0].tool_name is None  # Wildcard

    hooks_for_write = get_hooks_for_tool(HookType.PRE_TOOL, "write_file")
    assert len(hooks_for_write) == 1
    assert hooks_for_write[0].name == "log_all_calls"


def test_decorator_with_priority():
    """Test decorator with custom priority value."""

    @pre_tool("bash", priority=10)
    def early_hook(ctx):
        return HookResult.approve()

    @pre_tool("bash", priority=90)
    def late_hook(ctx):
        return HookResult.approve()

    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 2

    # Should be sorted by priority (lower first)
    assert hooks[0].name == "early_hook"
    assert hooks[0].priority == 10
    assert hooks[1].name == "late_hook"
    assert hooks[1].priority == 90


def test_decorator_with_name():
    """Test decorator with custom hook name."""

    @pre_tool("bash", name="custom_name")
    def my_function(ctx):
        return HookResult.approve()

    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 1
    # Should use custom name, not function name
    assert hooks[0].name == "custom_name"


def test_function_not_modified():
    """Test that decorated function is still callable and works correctly."""
    call_count = 0

    @pre_tool("bash")
    def my_hook(ctx):
        nonlocal call_count
        call_count += 1
        return HookResult.approve()

    # Function should still be callable
    assert callable(my_hook)

    # Create a mock context
    mock_ctx = HookContext(
        tool_name="bash",
        tool_instance=None,
        input_args={},
        raw_input_args={},
        execution_id="test-123",
        timestamp=None,
        working_directory="/tmp",
        environment={},
    )

    # Function should work normally when called directly
    result = my_hook(mock_ctx)
    assert call_count == 1
    assert result.action == HookResult.approve().action

    # Call again to verify it still works
    my_hook(mock_ctx)
    assert call_count == 2


def test_multiple_decorators_same_function():
    """Test that registering multiple decorators on the same function replaces the hook."""

    # First registration
    @pre_tool("bash")
    def shared_hook(ctx):
        return HookResult.approve()

    # Should have one hook
    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 1
    assert hooks[0].name == "shared_hook"

    # Same function decorated again (this would typically be a mistake)
    # In practice, this registers another hook with the same function
    @pre_tool("read_file")
    def shared_hook(ctx):  # noqa: F811
        return HookResult.deny("Not allowed")

    # Now both hooks should be registered with the same function name
    # but for different tools
    bash_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    read_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "read_file")

    # Both registrations exist because they're for different tools
    assert len(bash_hooks) == 1
    assert len(read_hooks) == 1


def test_pre_and_post_on_same_tool():
    """Test that same tool can have both pre and post hooks."""

    @pre_tool("bash")
    def pre_bash(ctx):
        return HookResult.approve()

    @post_tool("bash")
    def post_bash(ctx):
        return HookResult.approve()

    pre_hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    post_hooks = get_hooks_for_tool(HookType.POST_TOOL, "bash")

    assert len(pre_hooks) == 1
    assert pre_hooks[0].name == "pre_bash"

    assert len(post_hooks) == 1
    assert post_hooks[0].name == "post_bash"


def test_multiple_hooks_same_tool_same_type():
    """Test that multiple hooks can be registered for the same tool and type."""

    @pre_tool("bash", priority=10)
    def hook1(ctx):
        return HookResult.approve()

    @pre_tool("bash", priority=50)
    def hook2(ctx):
        return HookResult.approve()

    @pre_tool("bash", priority=90)
    def hook3(ctx):
        return HookResult.approve()

    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 3

    # Should be sorted by priority
    names = [h.name for h in hooks]
    assert names == ["hook1", "hook2", "hook3"]


def test_wildcard_and_specific_hooks_combined():
    """Test that wildcard and tool-specific hooks are both returned."""

    @pre_tool()  # Wildcard
    def all_tools_hook(ctx):
        return HookResult.approve()

    @pre_tool("bash")  # Tool-specific
    def bash_only_hook(ctx):
        return HookResult.approve()

    # Get hooks for bash - should include both
    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 2

    names = [h.name for h in hooks]
    assert "all_tools_hook" in names
    assert "bash_only_hook" in names

    # Get hooks for read_file - should only have wildcard
    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "read_file")
    assert len(hooks) == 1
    assert hooks[0].name == "all_tools_hook"


def test_default_priority():
    """Test that default priority is 50."""

    @pre_tool("bash")
    def hook_with_default_priority(ctx):
        return HookResult.approve()

    hooks = get_hooks_for_tool(HookType.PRE_TOOL, "bash")
    assert len(hooks) == 1
    assert hooks[0].priority == 50
