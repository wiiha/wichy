"""Tests for the hook executor module."""

import time


from wichy.hooks.decorators import post_tool, pre_tool, session_start
from wichy.hooks.executor import HookExecutionResult, HookExecutor
from wichy.hooks.registry import clear_hooks
from wichy.hooks.result import HookResult
from wichy.hooks.types import HookType

# =============================================================================
# HookExecutionResult tests
# =============================================================================


def test_result_defaults():
    """All fields have correct defaults."""
    result = HookExecutionResult()

    assert result.approved is True
    assert result.modified_input is None
    assert result.modified_output is None
    assert result.error_message is None
    assert result.total_time_ms == 0.0


def test_result_lists_initialized():
    """hooks_executed and hooks_denied are lists."""
    result = HookExecutionResult()

    assert isinstance(result.hooks_executed, list)
    assert isinstance(result.hooks_denied, list)
    assert len(result.hooks_executed) == 0
    assert len(result.hooks_denied) == 0


# =============================================================================
# build_context tests
# =============================================================================


def test_build_context_basic():
    """All fields populated correctly."""
    clear_hooks()

    class MockTool:
        name = "test_tool"

    mock_tool = MockTool()
    input_args = {"arg1": "value1", "arg2": 123}

    context = HookExecutor.build_context(
        tool_instance=mock_tool,
        tool_name="test_tool",
        input_args=input_args,
    )

    assert context.tool_name == "test_tool"
    assert context.tool_instance is mock_tool
    assert context.input_args == input_args
    assert context.raw_input_args == input_args
    assert context.output is None
    assert context.error is None
    assert context.execution_id.startswith("hook_")
    assert context.working_directory is not None
    assert isinstance(context.environment, dict)


def test_build_context_with_output():
    """Output field set for post-hooks."""
    clear_hooks()

    class MockTool:
        name = "test_tool"

    mock_tool = MockTool()
    input_args = {"arg1": "value1"}
    output = "Tool output result"

    context = HookExecutor.build_context(
        tool_instance=mock_tool,
        tool_name="test_tool",
        input_args=input_args,
        output=output,
    )

    assert context.output == output
    assert context.error is None


def test_build_context_with_error():
    """Error field set when error provided."""
    clear_hooks()

    class MockTool:
        name = "test_tool"

    mock_tool = MockTool()
    input_args = {"arg1": "value1"}
    error = RuntimeError("Something went wrong")

    context = HookExecutor.build_context(
        tool_instance=mock_tool,
        tool_name="test_tool",
        input_args=input_args,
        error=error,
    )

    assert context.output is None
    assert context.error is error


# =============================================================================
# run_pre_hooks tests
# =============================================================================


def test_no_hooks_registered():
    """Returns approved result when no hooks registered."""
    clear_hooks()

    class MockTool:
        name = "test"

    result = HookExecutor.run_pre_hooks(MockTool(), "test", {"arg": "value"})

    assert result.approved is True
    assert result.error_message is None
    assert len(result.hooks_executed) == 0
    assert len(result.hooks_denied) == 0


def test_single_approve_hook():
    """Single hook approves execution."""
    clear_hooks()

    @pre_tool("test")
    def approve_hook(ctx):
        return HookResult.approve()

    class MockTool:
        name = "test"

    result = HookExecutor.run_pre_hooks(MockTool(), "test", {"arg": "value"})

    assert result.approved is True
    assert result.error_message is None
    assert "approve_hook" in result.hooks_executed
    assert len(result.hooks_denied) == 0


def test_single_deny_hook():
    """Single hook denies execution."""
    clear_hooks()

    @pre_tool("test")
    def deny_hook(ctx):
        return HookResult.deny("Blocked for testing")

    class MockTool:
        name = "test"

    result = HookExecutor.run_pre_hooks(MockTool(), "test", {"arg": "value"})

    assert result.approved is False
    assert "Blocked" in result.error_message
    assert "deny_hook" in result.hooks_denied


def test_hook_denies_execution():
    """approved=False when denied."""
    clear_hooks()

    @pre_tool("bash")
    def block_dangerous(ctx):
        return HookResult.deny("Dangerous command blocked")

    class MockTool:
        name = "bash"

    result = HookExecutor.run_pre_hooks(MockTool(), "bash", {"command": "rm -rf /"})

    assert result.approved is False
    assert result.error_message == "Dangerous command blocked"
    assert "block_dangerous" in result.hooks_denied


def test_hook_modifies_input():
    """modified_input set correctly."""
    clear_hooks()

    @pre_tool("write_file")
    def sanitize_path(ctx):
        return HookResult.modify_input({"path": "/safe/path"})

    class MockTool:
        name = "write_file"

    result = HookExecutor.run_pre_hooks(
        MockTool(), "write_file", {"path": "/unsafe/path", "content": "data"}
    )

    assert result.approved is True
    assert result.modified_input is not None
    assert result.modified_input["path"] == "/safe/path"
    assert result.modified_input["content"] == "data"


def test_multiple_hooks_priority():
    """Hooks run in priority order."""
    clear_hooks()

    execution_order = []

    @pre_tool("test", priority=10)
    def first_hook(ctx):
        execution_order.append("first")
        return HookResult.approve()

    @pre_tool("test", priority=50)
    def second_hook(ctx):
        execution_order.append("second")
        return HookResult.approve()

    @pre_tool("test", priority=90)
    def third_hook(ctx):
        execution_order.append("third")
        return HookResult.approve()

    class MockTool:
        name = "test"

    result = HookExecutor.run_pre_hooks(MockTool(), "test", {"arg": "value"})

    assert result.approved is True
    assert execution_order == ["first", "second", "third"]
    assert result.hooks_executed == ["first_hook", "second_hook", "third_hook"]


def test_hook_exception_handled():
    """Hook exception doesn't crash execution."""
    clear_hooks()

    @pre_tool("test")
    def failing_hook(ctx):
        raise ValueError("Something went wrong in the hook")

    @pre_tool("test", priority=100)
    def after_failing_hook(ctx):
        return HookResult.approve()

    class MockTool:
        name = "test"

    # Should not raise, the exception is caught
    result = HookExecutor.run_pre_hooks(MockTool(), "test", {"arg": "value"})

    # The failing hook is NOT recorded as executed (it threw before completion)
    assert "failing_hook" not in result.hooks_executed
    # The hook after should still run since exception is handled
    assert "after_failing_hook" in result.hooks_executed
    # Overall execution should still be approved
    assert result.approved is True


def test_hook_timing_captured():
    """total_time_ms > 0 after hook execution."""
    clear_hooks()

    @pre_tool("test")
    def slow_hook(ctx):
        time.sleep(0.01)  # 10ms delay
        return HookResult.approve()

    class MockTool:
        name = "test"

    result = HookExecutor.run_pre_hooks(MockTool(), "test", {"arg": "value"})

    assert result.total_time_ms > 0
    assert result.total_time_ms >= 10  # At least 10ms


# =============================================================================
# run_post_hooks tests
# =============================================================================


def test_post_hook_approve():
    """Post hook approves."""
    clear_hooks()

    @post_tool("test")
    def approve_output(ctx):
        return HookResult.approve()

    class MockTool:
        name = "test"

    result = HookExecutor.run_post_hooks(
        MockTool(), "test", {"arg": "value"}, output="result"
    )

    assert result.approved is True
    assert result.error_message is None
    assert "approve_output" in result.hooks_executed


def test_post_hook_modify_output():
    """Post hook modifies output."""
    clear_hooks()

    @post_tool("test")
    def modify_output_hook(ctx):
        return HookResult.modify_output("Sanitized output")

    class MockTool:
        name = "test"

    result = HookExecutor.run_post_hooks(
        MockTool(), "test", {"arg": "value"}, output="Original output"
    )

    assert result.approved is True
    assert result.modified_output == "Sanitized output"


def test_post_hook_on_error():
    """Post hooks run even on error."""
    clear_hooks()

    hook_called = []

    @post_tool("test")
    def error_handler_hook(ctx):
        hook_called.append("called")
        assert ctx.error is not None
        assert "Test error" in str(ctx.error)
        return HookResult.approve()

    class MockTool:
        name = "test"

    error = RuntimeError("Test error occurred")
    result = HookExecutor.run_post_hooks(
        MockTool(), "test", {"arg": "value"}, output="", error=error
    )

    assert "called" in hook_called
    assert "error_handler_hook" in result.hooks_executed


def test_post_hook_deny():
    """Post hook can deny and set error message."""
    clear_hooks()

    @post_tool("test")
    def deny_output_hook(ctx):
        return HookResult.deny("Output blocked due to policy")

    class MockTool:
        name = "test"

    result = HookExecutor.run_post_hooks(
        MockTool(), "test", {"arg": "value"}, output="some output"
    )

    assert result.approved is False
    assert result.error_message == "Output blocked due to policy"
    assert "deny_output_hook" in result.hooks_denied


def test_post_hook_multiple_modifications():
    """Multiple post hooks can chain modifications."""
    clear_hooks()

    @post_tool("test", priority=10)
    def first_modifier(ctx):
        return HookResult.modify_output("Modified once")

    @post_tool("test", priority=50)
    def second_modifier(ctx):
        # Context output should show previous modification
        assert ctx.output == "Modified once"
        return HookResult.modify_output("Modified twice")

    class MockTool:
        name = "test"

    result = HookExecutor.run_post_hooks(
        MockTool(), "test", {"arg": "value"}, output="Original"
    )

    assert result.modified_output == "Modified twice"


def test_post_hook_log_action():
    """LOG action doesn't affect execution flow."""
    clear_hooks()

    log_data = []

    @post_tool("test")
    def logging_hook(ctx):
        log_data.append({"tool": ctx.tool_name, "output": ctx.output})
        return HookResult.log({"logged": True})

    class MockTool:
        name = "test"

    result = HookExecutor.run_post_hooks(
        MockTool(), "test", {"arg": "value"}, output="result"
    )

    assert result.approved is True
    assert len(log_data) == 1
    assert log_data[0]["tool"] == "test"
    assert "logging_hook" in result.hooks_executed


# =============================================================================
# run_context_hooks baseline + regression tests
# =============================================================================


def test_run_context_hooks_no_hooks():
    """When no hooks registered, returns quickly with empty result."""
    clear_hooks()

    class MockRootAgent:
        pass

    result = HookExecutor.run_context_hooks(
        HookType.SESSION_START,
        root_agent=MockRootAgent(),
    )

    assert result.approved is True
    assert result.modified_output is None
    assert result.hooks_executed == []
    assert result.total_time_ms >= 0


def test_run_context_hooks_session_start_ignores_return_values():
    """Existing lifecycle hooks returning DENY or MODIFY_OUTPUT have no effect."""
    clear_hooks()

    class MockRootAgent:
        pass

    @session_start
    def deny_session_start(ctx):
        return HookResult.deny("should be ignored")

    @session_start
    def modify_session_start(ctx):
        return HookResult.modify_output("should be ignored")

    result = HookExecutor.run_context_hooks(
        HookType.SESSION_START,
        root_agent=MockRootAgent(),
    )

    assert result.approved is True
    assert result.error_message is None
    assert result.modified_output is None
    assert len(result.hooks_executed) == 2


def test_run_context_hooks_exception_continues():
    """A failing lifecycle hook does not stop other hooks."""
    clear_hooks()

    class MockRootAgent:
        pass

    executed = []

    @session_start
    def failing_start_hook(ctx):
        executed.append("failing")
        raise RuntimeError("boom")

    @session_start
    def ok_start_hook(ctx):
        executed.append("ok")
        return HookResult.approve()

    result = HookExecutor.run_context_hooks(
        HookType.SESSION_START,
        root_agent=MockRootAgent(),
    )

    assert executed == ["failing", "ok"]
    assert len(result.hooks_executed) == 1  # only the ok hook tracked
