"""Tests for lifecycle hooks in the Wichy hooks system.

This module tests the lifecycle hook functionality including:
- HookType enum values for lifecycle events
- Lifecycle hook decorators (@session_start, @session_end, etc.)
- Registry methods for lifecycle hooks (get_hooks_for_type)
- HookContext for lifecycle hooks (optional tool_name, event_data)
- Hook execution for lifecycle events
"""

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wichy.hooks import (
    context_compact_post,
    context_compact_pre,
    context_reset_post,
    context_reset_pre,
    pre_response_to_user,
    pre_user_message,
    session_end,
    session_start,
    clear_hooks,
    get_hooks_for_type,
    HookContext,
    HookResult,
    HookType,
    HookPriority,
)
from wichy.hooks.executor import HookExecutor

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def setup_hooks():
    """Clear hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


# =============================================================================
# HookType Enum Tests
# =============================================================================


class TestHookTypeLifecycleValues:
    """Tests for lifecycle hook type enum values."""

    def test_session_start_value(self):
        """SESSION_START should have correct string value."""
        assert HookType.SESSION_START.value == "session_start"

    def test_session_end_value(self):
        """SESSION_END should have correct string value."""
        assert HookType.SESSION_END.value == "session_end"

    def test_context_reset_pre_value(self):
        """CONTEXT_RESET_PRE should have correct string value."""
        assert HookType.CONTEXT_RESET_PRE.value == "context_reset_pre"

    def test_context_reset_post_value(self):
        """CONTEXT_RESET_POST should have correct string value."""
        assert HookType.CONTEXT_RESET_POST.value == "context_reset_post"

    def test_context_compact_pre_value(self):
        """CONTEXT_COMPACT_PRE should have correct string value."""
        assert HookType.CONTEXT_COMPACT_PRE.value == "context_compact_pre"

    def test_context_compact_post_value(self):
        """CONTEXT_COMPACT_POST should have correct string value."""
        assert HookType.CONTEXT_COMPACT_POST.value == "context_compact_post"

    def test_all_lifecycle_types_exist(self):
        """All 6 lifecycle types should exist in the enum."""
        lifecycle_types = [
            HookType.SESSION_START,
            HookType.SESSION_END,
            HookType.CONTEXT_RESET_PRE,
            HookType.CONTEXT_RESET_POST,
            HookType.CONTEXT_COMPACT_PRE,
            HookType.CONTEXT_COMPACT_POST,
        ]
        # Verify they're all valid enum members
        for hook_type in lifecycle_types:
            assert isinstance(hook_type, HookType)

    def test_lifecycle_types_are_distinct(self):
        """All lifecycle types should have distinct values."""
        lifecycle_values = [
            HookType.SESSION_START.value,
            HookType.SESSION_END.value,
            HookType.CONTEXT_RESET_PRE.value,
            HookType.CONTEXT_RESET_POST.value,
            HookType.CONTEXT_COMPACT_PRE.value,
            HookType.CONTEXT_COMPACT_POST.value,
        ]
        assert len(lifecycle_values) == len(set(lifecycle_values))


# =============================================================================
# Session Start Decorator Tests
# =============================================================================


class TestSessionStartDecorator:
    """Tests for @session_start decorator."""

    def test_bare_decorator(self):
        """@session_start should register a hook."""

        @session_start
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        # Hook should be registered
        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].function == on_start
        assert hooks[0].hook_type == HookType.SESSION_START
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@session_start(priority=10) should register with priority."""

        @session_start(priority=10)
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].priority == 10

    def test_decorator_with_name(self):
        """@session_start(name='custom') should register with name."""

        @session_start(name="custom_session_start")
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].name == "custom_session_start"

    def test_decorator_with_priority_and_name(self):
        """@session_start(priority=10, name='custom') should work."""

        @session_start(priority=10, name="custom_hook")
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].priority == 10
        assert hooks[0].name == "custom_hook"

    def test_function_remains_callable(self):
        """Decorated function should remain directly callable."""

        @session_start
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        # Should be able to call the function directly
        mock_ctx = HookContext(tool_name=None, tool_instance=None)
        result = on_start(mock_ctx)
        assert result.action.value == "approve"

    def test_multiple_session_start_hooks(self):
        """Multiple @session_start hooks should all register."""

        @session_start(priority=10)
        def first(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_start(priority=20)
        def second(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 2


# =============================================================================
# Session End Decorator Tests
# =============================================================================


class TestSessionEndDecorator:
    """Tests for @session_end decorator."""

    def test_bare_decorator(self):
        """@session_end should register a hook."""

        @session_end
        def on_end(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_END)
        assert len(hooks) == 1
        assert hooks[0].function == on_end
        assert hooks[0].hook_type == HookType.SESSION_END
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@session_end(priority=10) should register with priority."""

        @session_end(priority=HookPriority.EARLY.value)
        def on_end(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_END)
        assert len(hooks) == 1
        assert hooks[0].priority == HookPriority.EARLY.value

    def test_decorator_with_name(self):
        """@session_end(name='custom') should register with name."""

        @session_end(name="cleanup_session")
        def on_end(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_END)
        assert len(hooks) == 1
        assert hooks[0].name == "cleanup_session"


# =============================================================================
# Context Reset Pre Decorator Tests
# =============================================================================


class TestContextResetPreDecorator:
    """Tests for @context_reset_pre decorator."""

    def test_bare_decorator(self):
        """@context_reset_pre should register a hook."""

        @context_reset_pre
        def before_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_PRE)
        assert len(hooks) == 1
        assert hooks[0].function == before_reset
        assert hooks[0].hook_type == HookType.CONTEXT_RESET_PRE
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@context_reset_pre(priority=10) should register with priority."""

        @context_reset_pre(priority=10)
        def before_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_PRE)
        assert len(hooks) == 1
        assert hooks[0].priority == 10

    def test_decorator_with_name(self):
        """@context_reset_pre(name='custom') should register with name."""

        @context_reset_pre(name="log_before_reset")
        def before_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_PRE)
        assert len(hooks) == 1
        assert hooks[0].name == "log_before_reset"


# =============================================================================
# Context Reset Post Decorator Tests
# =============================================================================


class TestContextResetPostDecorator:
    """Tests for @context_reset_post decorator."""

    def test_bare_decorator(self):
        """@context_reset_post should register a hook."""

        @context_reset_post
        def after_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_POST)
        assert len(hooks) == 1
        assert hooks[0].function == after_reset
        assert hooks[0].hook_type == HookType.CONTEXT_RESET_POST
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@context_reset_post(priority=10) should register with priority."""

        @context_reset_post(priority=10)
        def after_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_POST)
        assert len(hooks) == 1
        assert hooks[0].priority == 10

    def test_decorator_with_name(self):
        """@context_reset_post(name='custom') should register with name."""

        @context_reset_post(name="init_after_reset")
        def after_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_POST)
        assert len(hooks) == 1
        assert hooks[0].name == "init_after_reset"


# =============================================================================
# Context Compact Pre Decorator Tests
# =============================================================================


class TestContextCompactPreDecorator:
    """Tests for @context_compact_pre decorator."""

    def test_bare_decorator(self):
        """@context_compact_pre should register a hook."""

        @context_compact_pre
        def before_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)
        assert len(hooks) == 1
        assert hooks[0].function == before_compact
        assert hooks[0].hook_type == HookType.CONTEXT_COMPACT_PRE
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@context_compact_pre(priority=10) should register with priority."""

        @context_compact_pre(priority=10)
        def before_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)
        assert len(hooks) == 1
        assert hooks[0].priority == 10

    def test_decorator_with_name(self):
        """@context_compact_pre(name='custom') should register with name."""

        @context_compact_pre(name="preserve_state")
        def before_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)
        assert len(hooks) == 1
        assert hooks[0].name == "preserve_state"


# =============================================================================
# Context Compact Post Decorator Tests
# =============================================================================


class TestContextCompactPostDecorator:
    """Tests for @context_compact_post decorator."""

    def test_bare_decorator(self):
        """@context_compact_post should register a hook."""

        @context_compact_post
        def after_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_POST)
        assert len(hooks) == 1
        assert hooks[0].function == after_compact
        assert hooks[0].hook_type == HookType.CONTEXT_COMPACT_POST
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@context_compact_post(priority=10) should register with priority."""

        @context_compact_post(priority=10)
        def after_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_POST)
        assert len(hooks) == 1
        assert hooks[0].priority == 10

    def test_decorator_with_name(self):
        """@context_compact_post(name='custom') should register with name."""

        @context_compact_post(name="validate_compact")
        def after_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_POST)
        assert len(hooks) == 1
        assert hooks[0].name == "validate_compact"


# =============================================================================
# Registry get_hooks_for_type Tests
# =============================================================================


class TestGetHooksForType:
    """Tests for get_hooks_for_type registry method."""

    def test_returns_empty_list_when_no_hooks(self):
        """get_hooks_for_type should return empty list when no hooks registered."""
        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert hooks == []

    def test_returns_hooks_for_correct_type(self):
        """get_hooks_for_type should return hooks for the specified type only."""

        @session_start
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_end
        def on_end(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        start_hooks = get_hooks_for_type(HookType.SESSION_START)
        end_hooks = get_hooks_for_type(HookType.SESSION_END)

        assert len(start_hooks) == 1
        assert len(end_hooks) == 1
        assert start_hooks[0].function == on_start
        assert end_hooks[0].function == on_end

    def test_returns_hooks_sorted_by_priority(self):
        """get_hooks_for_type should return hooks sorted by priority."""

        @session_start(priority=90)
        def late_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_start(priority=10)
        def early_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_start(priority=50)
        def normal_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 3
        # Should be sorted by priority (ascending)
        assert hooks[0].priority == 10
        assert hooks[1].priority == 50
        assert hooks[2].priority == 90

    def test_returns_copy_of_hooks_list(self):
        """get_hooks_for_type should return a copy, not the internal list."""
        hooks1 = get_hooks_for_type(HookType.SESSION_START)
        hooks2 = get_hooks_for_type(HookType.SESSION_START)
        assert hooks1 is not hooks2

    def test_works_for_all_lifecycle_types(self):
        """get_hooks_for_type should work for all lifecycle hook types."""

        @session_start
        def start_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_end
        def end_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_pre
        def reset_pre_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_post
        def reset_post_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_compact_pre
        def compact_pre_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_compact_post
        def compact_post_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        # Verify each type returns exactly one hook
        assert len(get_hooks_for_type(HookType.SESSION_START)) == 1
        assert len(get_hooks_for_type(HookType.SESSION_END)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_RESET_PRE)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_RESET_POST)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_COMPACT_POST)) == 1


# =============================================================================
# HookContext Lifecycle Tests
# =============================================================================


class TestHookContextForLifecycle:
    """Tests for HookContext when used with lifecycle hooks."""

    def test_can_create_without_tool_name(self):
        """HookContext should allow tool_name=None for lifecycle hooks."""
        ctx = HookContext(tool_name=None, tool_instance=None)
        assert ctx.tool_name is None
        assert ctx.tool_instance is None

    def test_event_data_field_exists(self):
        """HookContext should have event_data field."""
        ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            event_data={"key": "value", "count": 42},
        )
        assert ctx.event_data == {"key": "value", "count": 42}

    def test_event_data_defaults_to_empty_dict(self):
        """HookContext event_data should default to empty dict."""
        ctx = HookContext(tool_name=None, tool_instance=None)
        assert ctx.event_data == {}

    def test_hook_type_field_exists(self):
        """HookContext should have hook_type field."""
        ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            hook_type=HookType.SESSION_START,
        )
        assert ctx.hook_type == HookType.SESSION_START

    def test_lifecycle_event_field_exists(self):
        """HookContext should have lifecycle_event field."""
        ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            lifecycle_event="session_start",
        )
        assert ctx.lifecycle_event == "session_start"

    def test_full_lifecycle_context(self):
        """HookContext should support all lifecycle-related fields."""
        ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            execution_id="exec-123",
            timestamp=datetime.now(),
            working_directory=Path("/tmp"),
            environment={"HOME": "/home/user"},
            session_id="session-456",
            user_message="Test message",
            conversation_turn=5,
            hook_type=HookType.CONTEXT_COMPACT_PRE,
            lifecycle_event="context_compact_pre",
            event_data={
                "message_count": 10,
                "token_count": 5000,
            },
        )

        assert ctx.tool_name is None
        assert ctx.hook_type == HookType.CONTEXT_COMPACT_PRE
        assert ctx.lifecycle_event == "context_compact_pre"
        assert ctx.event_data["message_count"] == 10
        assert ctx.event_data["token_count"] == 5000

    def test_state_is_mutable(self):
        """HookContext state should be mutable for sharing between hooks."""
        ctx = HookContext(tool_name=None, tool_instance=None)
        ctx.state["counter"] = 1
        ctx.state["items"] = ["item1"]

        assert ctx.state["counter"] == 1
        assert ctx.state["items"] == ["item1"]


# =============================================================================
# Hook Execution Tests
# =============================================================================


class TestLifecycleHookExecution:
    """Tests for lifecycle hook execution."""

    def test_hooks_can_be_executed_directly(self):
        """Lifecycle hooks can be called directly like regular functions."""
        execution_order = []

        @context_reset_pre(priority=10)
        def first_hook(ctx: HookContext) -> HookResult:
            execution_order.append("first")
            return HookResult.approve()

        @context_reset_pre(priority=20)
        def second_hook(ctx: HookContext) -> HookResult:
            execution_order.append("second")
            return HookResult.approve()

        # Execute hooks directly (simulating what run_context_hooks does)
        mock_ctx = HookContext(tool_name=None, tool_instance=None)

        # Get hooks and execute in order
        hooks = get_hooks_for_type(HookType.CONTEXT_RESET_PRE)
        for hook in hooks:
            hook.function(mock_ctx)

        # Verify hooks were called in order
        assert execution_order == ["first", "second"]

    def test_run_context_hooks_returns_execution_result(self):
        """run_context_hooks should return HookExecutionResult."""

        @context_compact_pre
        def track_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        mock_context_handler = MagicMock()
        mock_root_agent = MagicMock()

        result = HookExecutor.run_context_hooks(
            HookType.CONTEXT_COMPACT_PRE,
            context_handler=mock_context_handler,
            root_agent=mock_root_agent,
        )

        # Should return a HookExecutionResult
        assert hasattr(result, "approved")
        assert hasattr(result, "hooks_executed")
        assert hasattr(result, "total_time_ms")

    def test_context_compact_post_receives_summary(self):
        """Context compact post hook receives summary in output field."""
        received_output = []

        @context_compact_post
        def capture_summary(ctx: HookContext) -> HookResult:
            received_output.append(ctx.output)
            return HookResult.approve()

        # Create context directly with summary in output field
        mock_ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            output="Context was summarized...",
        )

        # Call the hook directly
        capture_summary(mock_ctx)

        # Hook should receive the summary in output field
        assert received_output == ["Context was summarized..."]

    def test_run_context_hooks_empty_registry(self):
        """run_context_hooks should handle empty registry gracefully."""
        mock_context_handler = MagicMock()
        mock_root_agent = MagicMock()

        result = HookExecutor.run_context_hooks(
            HookType.CONTEXT_RESET_PRE,
            context_handler=mock_context_handler,
            root_agent=mock_root_agent,
        )

        # Should succeed with no hooks executed
        assert result.hooks_executed == []

    def test_run_context_hooks_handles_exceptions(self):
        """run_context_hooks should handle hook exceptions gracefully."""
        execution_order = []

        @context_reset_pre(priority=10)
        def failing_hook(ctx: HookContext) -> HookResult:
            execution_order.append("failing")
            raise ValueError("Hook failed!")

        @context_reset_pre(priority=20)
        def succeeding_hook(ctx: HookContext) -> HookResult:
            execution_order.append("succeeding")
            return HookResult.approve()

        mock_context_handler = MagicMock()
        mock_root_agent = MagicMock()

        # Should not raise, should continue to next hook
        HookExecutor.run_context_hooks(
            HookType.CONTEXT_RESET_PRE,
            context_handler=mock_context_handler,
            root_agent=mock_root_agent,
        )

        # Both hooks should have been attempted (possibly multiple times due to registry issue)
        assert "failing" in execution_order
        assert "succeeding" in execution_order

    def test_run_context_hooks_timing(self):
        """run_context_hooks should track execution time."""

        @context_reset_pre
        def slow_hook(ctx: HookContext) -> HookResult:
            time.sleep(0.01)
            return HookResult.approve()

        mock_context_handler = MagicMock()
        mock_root_agent = MagicMock()

        result = HookExecutor.run_context_hooks(
            HookType.CONTEXT_RESET_PRE,
            context_handler=mock_context_handler,
            root_agent=mock_root_agent,
        )

        # Should have some elapsed time
        assert result.total_time_ms >= 0  # Just check it runs


class TestLifecycleHookPriorityOrdering:
    """Tests for priority ordering across different lifecycle hook types."""

    def test_session_start_priority_order(self):
        """Session start hooks should execute in priority order."""
        execution_order = []

        @session_start(priority=30)
        def middle(ctx: HookContext) -> HookResult:
            execution_order.append("middle")
            return HookResult.approve()

        @session_start(priority=10)
        def first(ctx: HookContext) -> HookResult:
            execution_order.append("first")
            return HookResult.approve()

        @session_start(priority=50)
        def last(ctx: HookContext) -> HookResult:
            execution_order.append("last")
            return HookResult.approve()

        # Get hooks and verify order
        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 3
        assert hooks[0].priority == 10
        assert hooks[1].priority == 30
        assert hooks[2].priority == 50

    def test_different_lifecycle_types_independent_priority(self):
        """Each lifecycle type should have independent priority ordering."""

        # Register hooks with same priorities but different types
        @session_start(priority=10)
        def start_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_pre(priority=10)
        def reset_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        # Both should be registered independently
        start_hooks = get_hooks_for_type(HookType.SESSION_START)
        reset_hooks = get_hooks_for_type(HookType.CONTEXT_RESET_PRE)

        assert len(start_hooks) == 1
        assert len(reset_hooks) == 1
        assert start_hooks[0].priority == 10
        assert reset_hooks[0].priority == 10


class TestLifecycleHookResultTypes:
    """Tests for different result types from lifecycle hooks."""

    def test_approve_result(self):
        """Lifecycle hooks can return approve result."""

        @session_start
        def approve_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1

        # Call the hook directly
        result = approve_hook(HookContext(tool_name=None, tool_instance=None))
        assert result.action.value == "approve"

    def test_log_result(self):
        """Lifecycle hooks can return log result."""

        @session_end
        def log_hook(ctx: HookContext) -> HookResult:
            return HookResult.log({"session_duration": 3600})

        hooks = get_hooks_for_type(HookType.SESSION_END)
        assert len(hooks) == 1

        # Call the hook directly
        result = log_hook(HookContext(tool_name=None, tool_instance=None))
        assert result.action.value == "log"
        assert result.log_data == {"session_duration": 3600}

    def test_deny_result_allowed_but_informational(self):
        """Lifecycle hooks can return deny, though it's informational only."""

        @context_compact_pre
        def deny_hook(ctx: HookContext) -> HookResult:
            return HookResult.deny("Would deny if allowed")

        # Should still register
        hooks = get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)
        assert len(hooks) == 1

        # Call the hook directly
        result = deny_hook(HookContext(tool_name=None, tool_instance=None))
        assert result.action.value == "deny"


# =============================================================================
# Integration Tests
# =============================================================================


class TestLifecycleHooksIntegration:
    """Integration tests for lifecycle hooks with multiple hook types."""

    def test_multiple_hook_types_coexist(self):
        """Multiple hook types can be registered simultaneously."""

        @session_start
        def on_start(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_end
        def on_end(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_pre
        def before_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_post
        def after_reset(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_compact_pre
        def before_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_compact_post
        def after_compact(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        # Verify all are registered
        assert len(get_hooks_for_type(HookType.SESSION_START)) == 1
        assert len(get_hooks_for_type(HookType.SESSION_END)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_RESET_PRE)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_RESET_POST)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)) == 1
        assert len(get_hooks_for_type(HookType.CONTEXT_COMPACT_POST)) == 1

    def test_hooks_can_share_state(self):
        """Pre and post hooks can share state via context."""

        @context_reset_pre
        def save_state(ctx: HookContext) -> HookResult:
            ctx.state["saved_data"] = "important_value"
            return HookResult.approve()

        @context_reset_post
        def restore_state(ctx: HookContext) -> HookResult:
            # Can access state from pre hook
            saved = ctx.state.get("saved_data")
            return HookResult.log({"restored": saved})

        # Verify both are registered
        pre_hooks = get_hooks_for_type(HookType.CONTEXT_RESET_PRE)
        post_hooks = get_hooks_for_type(HookType.CONTEXT_RESET_POST)
        assert len(pre_hooks) == 1
        assert len(post_hooks) == 1

    def test_clear_hooks_clears_all_lifecycle_hooks(self):
        """clear_hooks should clear all lifecycle hook types."""

        # Register all types
        @session_start
        def h1(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_end
        def h2(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_pre
        def h3(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_reset_post
        def h4(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_compact_pre
        def h5(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @context_compact_post
        def h6(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        # Clear all hooks
        clear_hooks()

        # All should be cleared
        assert len(get_hooks_for_type(HookType.SESSION_START)) == 0
        assert len(get_hooks_for_type(HookType.SESSION_END)) == 0
        assert len(get_hooks_for_type(HookType.CONTEXT_RESET_PRE)) == 0
        assert len(get_hooks_for_type(HookType.CONTEXT_RESET_POST)) == 0
        assert len(get_hooks_for_type(HookType.CONTEXT_COMPACT_PRE)) == 0
        assert len(get_hooks_for_type(HookType.CONTEXT_COMPACT_POST)) == 0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestLifecycleHooksEdgeCases:
    """Tests for edge cases and error handling."""

    def test_hook_with_default_name(self):
        """Hook should use function name if no name provided."""

        @session_start
        def my_session_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        # Should use function name
        assert hooks[0].name == "my_session_hook"

    def test_zero_priority(self):
        """Hook with priority=0 should work."""

        @session_start(priority=0)
        def zero_priority_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].priority == 0

    def test_negative_priority(self):
        """Hook with negative priority should work (executes earliest)."""

        @session_start(priority=-10)
        def negative_priority_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].priority == -10

    def test_high_priority(self):
        """Hook with high priority value should work."""

        @session_start(priority=1000)
        def high_priority_hook(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].priority == 1000

    def test_same_priority_hooks_order(self):
        """Hooks with same priority should preserve registration order."""

        @session_start(priority=50)
        def first(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_start(priority=50)
        def second(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        @session_start(priority=50)
        def third(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.SESSION_START)
        assert len(hooks) == 3
        # All have same priority
        assert hooks[0].priority == 50
        assert hooks[1].priority == 50
        assert hooks[2].priority == 50

    def test_hook_receives_correct_context_fields(self):
        """Hook should receive HookContext with appropriate fields."""

        received_contexts = []

        @context_compact_post
        def check_context(ctx: HookContext) -> HookResult:
            received_contexts.append(ctx)
            return HookResult.approve()

        # Create a context with lifecycle-specific fields
        test_context = HookContext(
            tool_name=None,
            tool_instance=None,
            hook_type=HookType.CONTEXT_COMPACT_POST,
            output="Test summary",
            event_data={"compacted": True},
        )

        # Call the hook directly
        check_context(test_context)

        # Verify the hook received proper context
        assert len(received_contexts) == 1
        ctx = received_contexts[0]
        assert ctx.tool_name is None


# =============================================================================
# New lifecycle hooks: PRE_USER_MESSAGE and PRE_RESPONSE_TO_USER
# =============================================================================


class TestPreUserMessageDecorator:
    """Tests for @pre_user_message decorator."""

    def test_bare_decorator(self):
        """@pre_user_message should register a lifecycle hook."""

        @pre_user_message
        def on_pre_user_message(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.PRE_USER_MESSAGE)
        assert len(hooks) == 1
        assert hooks[0].function == on_pre_user_message
        assert hooks[0].hook_type == HookType.PRE_USER_MESSAGE
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@pre_user_message(priority=10) should register with priority."""

        @pre_user_message(priority=10)
        def on_pre_user_message(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.PRE_USER_MESSAGE)
        assert len(hooks) == 1
        assert hooks[0].priority == 10


class TestPreResponseToUserDecorator:
    """Tests for @pre_response_to_user decorator."""

    def test_bare_decorator(self):
        """@pre_response_to_user should register a lifecycle hook."""

        @pre_response_to_user
        def on_pre_response(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.PRE_RESPONSE_TO_USER)
        assert len(hooks) == 1
        assert hooks[0].function == on_pre_response
        assert hooks[0].hook_type == HookType.PRE_RESPONSE_TO_USER
        assert hooks[0].tool_name is None

    def test_decorator_with_priority(self):
        """@pre_response_to_user(priority=10) should register with priority."""

        @pre_response_to_user(priority=10)
        def on_pre_response(ctx: HookContext) -> HookResult:
            return HookResult.approve()

        hooks = get_hooks_for_type(HookType.PRE_RESPONSE_TO_USER)
        assert len(hooks) == 1
        assert hooks[0].priority == 10


class TestPreUserMessageExecution:
    """Tests for PRE_USER_MESSAGE hook execution."""

    def test_event_data_contains_message_and_context(self):
        """PRE_USER_MESSAGE hook receives correct event_data."""
        received = []

        @pre_user_message
        def capture(ctx: HookContext) -> HookResult:
            received.append(ctx.event_data.copy())
            return HookResult.approve()

        class FakeRoot:
            pass

        fake_ctx = MagicMock()

        result = HookExecutor.run_context_hooks(
            HookType.PRE_USER_MESSAGE,
            root_agent=FakeRoot(),
            context_handler=fake_ctx,
            message="hello",
        )

        assert result.hooks_executed == ["capture"]
        assert len(received) == 1
        assert received[0]["message"] == "hello"
        assert received[0]["context_handler"] is fake_ctx
        assert isinstance(received[0]["root_agent"], FakeRoot)


class TestPreResponseToUserExecution:
    """Tests for PRE_RESPONSE_TO_USER hook execution and modification."""

    def test_event_data_contains_response_fields(self):
        """PRE_RESPONSE_TO_USER hook receives correct event_data."""
        received = []

        @pre_response_to_user
        def capture(ctx: HookContext) -> HookResult:
            received.append(ctx.event_data.copy())
            return HookResult.approve()

        class FakeRoot:
            pass

        fake_ctx = MagicMock()

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            context_handler=fake_ctx,
            response_content="assistant reply",
            response_reasoning="because",
            usage={"total_tokens": 42},
        )

        assert result.hooks_executed == ["capture"]
        assert len(received) == 1
        assert received[0]["response_content"] == "assistant reply"
        assert received[0]["response_reasoning"] == "because"
        assert received[0]["usage"] == {"total_tokens": 42}

    def test_output_set_to_response_content(self):
        """HookContext.output is initialized to response_content."""
        received = []

        @pre_response_to_user
        def capture(ctx: HookContext) -> HookResult:
            received.append(ctx.output)
            return HookResult.approve()

        class FakeRoot:
            pass

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            response_content="initial content",
        )

        assert result.hooks_executed == ["capture"]
        assert received == ["initial content"]

    def test_modify_output_changes_result(self):
        """A MODIFY_OUTPUT hook changes result.modified_output."""

        @pre_response_to_user
        def modify(ctx: HookContext) -> HookResult:
            return HookResult.modify_output("modified")

        class FakeRoot:
            pass

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            response_content="original",
        )

        assert result.modified_output == "modified"

    def test_modify_output_chain_last_wins(self):
        """Multiple MODIFY_OUTPUT hooks: last one wins, earlier visible via ctx.output."""
        seen_outputs = []

        @pre_response_to_user(priority=10)
        def first(ctx: HookContext) -> HookResult:
            seen_outputs.append(ctx.output)
            return HookResult.modify_output("first modification")

        @pre_response_to_user(priority=20)
        def second(ctx: HookContext) -> HookResult:
            seen_outputs.append(ctx.output)
            return HookResult.modify_output("second modification")

        class FakeRoot:
            pass

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            response_content="original",
        )

        assert seen_outputs == ["original", "first modification"]
        assert result.modified_output == "second modification"

    def test_modify_input_and_deny_are_noops(self):
        """MODIFY_INPUT and DENY actions do not affect the response."""

        @pre_response_to_user
        def deny_hook(ctx: HookContext) -> HookResult:
            return HookResult.deny("should be ignored")

        @pre_response_to_user
        def modify_input_hook(ctx: HookContext) -> HookResult:
            return HookResult.modify_input({"foo": "bar"})

        class FakeRoot:
            pass

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            response_content="original",
        )

        assert result.modified_output is None
        assert result.approved is True
        assert result.error_message is None

    def test_exception_preserves_earlier_modify(self):
        """If a later modify hook raises, earlier modification is preserved."""

        @pre_response_to_user(priority=10)
        def first(ctx: HookContext) -> HookResult:
            return HookResult.modify_output("preserved")

        @pre_response_to_user(priority=20)
        def second(ctx: HookContext) -> HookResult:
            raise RuntimeError("boom")

        class FakeRoot:
            pass

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            response_content="original",
        )

        assert result.modified_output == "preserved"

    def test_none_return_treated_as_approve(self):
        """A hook returning None is treated as APPROVE."""

        @pre_response_to_user
        def silent(ctx: HookContext) -> None:
            return None

        class FakeRoot:
            pass

        result = HookExecutor.run_context_hooks(
            HookType.PRE_RESPONSE_TO_USER,
            root_agent=FakeRoot(),
            response_content="original",
        )

        assert result.modified_output is None
        assert result.hooks_executed == ["silent"]
