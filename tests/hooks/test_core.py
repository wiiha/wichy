"""Tests for core hooks infrastructure.

This module tests the fundamental components of the hooks system:
- HookAction enum and HookResult dataclass (result.py)
- HookContext dataclass (context.py)
- HookType and HookPriority enums, RegisteredHook dataclass (types.py)
"""

from datetime import datetime
from pathlib import Path

from wichy.hooks.result import HookAction, HookResult
from wichy.hooks.context import HookContext
from wichy.hooks.types import HookType, HookPriority, RegisteredHook


class TestHookAction:
    """Test suite for HookAction enum."""

    def test_approve_value_exists(self):
        """Test that APPROVE action exists with correct value."""
        assert hasattr(HookAction, "APPROVE")
        assert HookAction.APPROVE.value == "approve"

    def test_deny_value_exists(self):
        """Test that DENY action exists with correct value."""
        assert hasattr(HookAction, "DENY")
        assert HookAction.DENY.value == "deny"

    def test_modify_input_value_exists(self):
        """Test that MODIFY_INPUT action exists with correct value."""
        assert hasattr(HookAction, "MODIFY_INPUT")
        assert HookAction.MODIFY_INPUT.value == "modify_input"

    def test_modify_output_value_exists(self):
        """Test that MODIFY_OUTPUT action exists with correct value."""
        assert hasattr(HookAction, "MODIFY_OUTPUT")
        assert HookAction.MODIFY_OUTPUT.value == "modify_output"

    def test_log_value_exists(self):
        """Test that LOG action exists with correct value."""
        assert hasattr(HookAction, "LOG")
        assert HookAction.LOG.value == "log"

    def test_all_actions_count(self):
        """Test that all expected actions are present."""
        actions = list(HookAction)
        assert len(actions) == 5


class TestHookResult:
    """Test suite for HookResult dataclass."""

    def test_default_action_is_approve(self):
        """Test that default action is APPROVE."""
        result = HookResult()
        assert result.action == HookAction.APPROVE

    def test_default_modified_input_is_none(self):
        """Test that default modified_input is None."""
        result = HookResult()
        assert result.modified_input is None

    def test_default_modified_output_is_none(self):
        """Test that default modified_output is None."""
        result = HookResult()
        assert result.modified_output is None

    def test_default_error_message_is_none(self):
        """Test that default error_message is None."""
        result = HookResult()
        assert result.error_message is None

    def test_default_log_data_is_none(self):
        """Test that default log_data is None."""
        result = HookResult()
        assert result.log_data is None

    def test_default_hook_name_is_empty_string(self):
        """Test that default hook_name is empty string."""
        result = HookResult()
        assert result.hook_name == ""

    def test_default_execution_time_is_none(self):
        """Test that default execution_time_ms is None."""
        result = HookResult()
        assert result.execution_time_ms is None


class TestHookResultApprove:
    """Test suite for HookResult.approve() factory method."""

    def test_approve_creates_approve_action(self):
        """Test that approve() creates result with APPROVE action."""
        # Arrange & Act
        result = HookResult.approve()

        # Assert
        assert result.action == HookAction.APPROVE

    def test_approve_has_no_error_message(self):
        """Test that approve() result has no error message."""
        # Arrange & Act
        result = HookResult.approve()

        # Assert
        assert result.error_message is None

    def test_approve_has_no_modified_input(self):
        """Test that approve() result has no modified input."""
        # Arrange & Act
        result = HookResult.approve()

        # Assert
        assert result.modified_input is None


class TestHookResultDeny:
    """Test suite for HookResult.deny() factory method."""

    def test_deny_creates_deny_action(self):
        """Test that deny() creates result with DENY action."""
        # Arrange & Act
        result = HookResult.deny("Access denied")

        # Assert
        assert result.action == HookAction.DENY

    def test_deny_sets_error_message(self):
        """Test that deny() sets the error message."""
        # Arrange
        message = "Permission denied for this resource"

        # Act
        result = HookResult.deny(message)

        # Assert
        assert result.error_message == message

    def test_deny_with_empty_message(self):
        """Test that deny() works with empty message."""
        # Arrange & Act
        result = HookResult.deny("")

        # Assert
        assert result.action == HookAction.DENY
        assert result.error_message == ""


class TestHookResultModifyInput:
    """Test suite for HookResult.modify_input() factory method."""

    def test_modify_input_creates_correct_action(self):
        """Test that modify_input() creates MODIFY_INPUT action."""
        # Arrange
        new_args = {"path": "/new/path"}

        # Act
        result = HookResult.modify_input(new_args)

        # Assert
        assert result.action == HookAction.MODIFY_INPUT

    def test_modify_input_sets_new_arguments(self):
        """Test that modify_input() sets the new arguments."""
        # Arrange
        new_args = {"path": "/safe/path", "mode": "read"}

        # Act
        result = HookResult.modify_input(new_args)

        # Assert
        assert result.modified_input == new_args

    def test_modify_input_with_nested_dict(self):
        """Test modify_input() with nested dictionary."""
        # Arrange
        new_args = {"config": {"nested": {"key": "value"}}}

        # Act
        result = HookResult.modify_input(new_args)

        # Assert
        assert result.modified_input == new_args


class TestHookResultModifyOutput:
    """Test suite for HookResult.modify_output() factory method."""

    def test_modify_output_creates_correct_action(self):
        """Test that modify_output() creates MODIFY_OUTPUT action."""
        # Arrange & Act
        result = HookResult.modify_output("New output")

        # Assert
        assert result.action == HookAction.MODIFY_OUTPUT

    def test_modify_output_sets_new_output(self):
        """Test that modify_output() sets the new output."""
        # Arrange
        new_output = "Sanitized output content"

        # Act
        result = HookResult.modify_output(new_output)

        # Assert
        assert result.modified_output == new_output

    def test_modify_output_with_empty_string(self):
        """Test modify_output() with empty string."""
        # Arrange & Act
        result = HookResult.modify_output("")

        # Assert
        assert result.action == HookAction.MODIFY_OUTPUT
        assert result.modified_output == ""


class TestHookResultLog:
    """Test suite for HookResult.log() factory method."""

    def test_log_creates_log_action(self):
        """Test that log() creates LOG action."""
        # Arrange & Act
        result = HookResult.log()

        # Assert
        assert result.action == HookAction.LOG

    def test_log_sets_log_data(self):
        """Test that log() sets the log data."""
        # Arrange
        data = {"key": "value", "count": 42}

        # Act
        result = HookResult.log(data)

        # Assert
        assert result.log_data == data

    def test_log_with_none_data(self):
        """Test that log() works with None data."""
        # Arrange & Act
        result = HookResult.log(None)

        # Assert
        assert result.action == HookAction.LOG
        assert result.log_data is None

    def test_log_with_string_data(self):
        """Test that log() works with string data."""
        # Arrange
        message = "Simple log message"

        # Act
        result = HookResult.log(message)

        # Assert
        assert result.log_data == message


class TestHookContext:
    """Test suite for HookContext dataclass."""

    def test_hook_context_creation_all_fields_set(self):
        """Test that HookContext sets all fields correctly."""
        # Arrange
        tool_instance = object()

        # Act
        context = HookContext(
            tool_name="bash",
            tool_instance=tool_instance,
            input_args={"command": "ls"},
            raw_input_args={"command": "ls"},
            execution_id="exec-123",
            timestamp=datetime(2024, 1, 1, 12, 0, 0),
            working_directory=Path("/home/user"),
            environment={"HOME": "/home/user"},
        )

        # Assert
        assert context.tool_name == "bash"
        assert context.tool_instance is tool_instance
        assert context.input_args == {"command": "ls"}
        assert context.raw_input_args == {"command": "ls"}
        assert context.execution_id == "exec-123"
        assert context.timestamp == datetime(2024, 1, 1, 12, 0, 0)
        assert context.working_directory == Path("/home/user")
        assert context.environment == {"HOME": "/home/user"}

    def test_hook_context_state_is_mutable_dict(self):
        """Test that HookContext.state is a mutable dict."""
        # Arrange
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Act
        context.state["key"] = "value"
        context.state["count"] = 42

        # Assert
        assert context.state["key"] == "value"
        assert context.state["count"] == 42

    def test_hook_context_state_default_empty_dict(self):
        """Test that state defaults to empty dict."""
        # Arrange & Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Assert
        assert context.state == {}

    def test_hook_context_optional_output_can_be_none(self):
        """Test that output field can be None."""
        # Arrange & Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Assert
        assert context.output is None

    def test_hook_context_optional_error_can_be_none(self):
        """Test that error field can be None."""
        # Arrange & Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Assert
        assert context.error is None

    def test_hook_context_optional_session_id_can_be_none(self):
        """Test that session_id can be None."""
        # Arrange & Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Assert
        assert context.session_id is None

    def test_hook_context_optional_user_message_can_be_none(self):
        """Test that user_message can be None."""
        # Arrange & Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Assert
        assert context.user_message is None

    def test_hook_context_optional_conversation_turn_can_be_none(self):
        """Test that conversation_turn can be None."""
        # Arrange & Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
        )

        # Assert
        assert context.conversation_turn is None

    def test_hook_context_can_set_optional_fields(self):
        """Test that optional fields can be set."""
        # Arrange
        error = ValueError("test error")

        # Act
        context = HookContext(
            tool_name="test",
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id="test-id",
            timestamp=datetime.now(),
            working_directory=Path.cwd(),
            environment={},
            output="tool output",
            error=error,
            session_id="session-123",
            user_message="Run the tool",
            conversation_turn=5,
        )

        # Assert
        assert context.output == "tool output"
        assert context.error is error
        assert context.session_id == "session-123"
        assert context.user_message == "Run the tool"
        assert context.conversation_turn == 5


class TestHookType:
    """Test suite for HookType enum."""

    def test_pre_tool_exists(self):
        """Test that PRE_TOOL type exists."""
        assert hasattr(HookType, "PRE_TOOL")
        assert HookType.PRE_TOOL.value == "pre_tool"

    def test_post_tool_exists(self):
        """Test that POST_TOOL type exists."""
        assert hasattr(HookType, "POST_TOOL")
        assert HookType.POST_TOOL.value == "post_tool"

    def test_all_hook_types_count(self):
        """Test that all expected hook types are present."""
        types = list(HookType)
        assert len(types) == 2


class TestHookPriority:
    """Test suite for HookPriority enum."""

    def test_early_value(self):
        """Test that EARLY priority has value 10."""
        assert hasattr(HookPriority, "EARLY")
        assert HookPriority.EARLY.value == 10

    def test_normal_value(self):
        """Test that NORMAL priority has value 50."""
        assert hasattr(HookPriority, "NORMAL")
        assert HookPriority.NORMAL.value == 50

    def test_late_value(self):
        """Test that LATE priority has value 90."""
        assert hasattr(HookPriority, "LATE")
        assert HookPriority.LATE.value == 90

    def test_all_priorities_count(self):
        """Test that all expected priorities are present."""
        priorities = list(HookPriority)
        assert len(priorities) == 3

    def test_priority_ordering(self):
        """Test that priority values are ordered correctly."""
        assert HookPriority.EARLY.value < HookPriority.NORMAL.value
        assert HookPriority.NORMAL.value < HookPriority.LATE.value


class TestRegisteredHook:
    """Test suite for RegisteredHook dataclass."""

    def test_registered_hook_defaults(self):
        """Test that RegisteredHook has correct default values."""

        # Arrange
        def dummy_hook(ctx):
            return HookResult.approve()

        # Act
        registered = RegisteredHook(
            hook_type=HookType.PRE_TOOL,
            tool_name="bash",
            function=dummy_hook,
        )

        # Assert
        assert registered.priority == 50
        assert registered.name == ""
        assert registered.source == "python"
        assert registered.enabled is True

    def test_registered_hook_all_fields_set(self):
        """Test that RegisteredHook sets all fields correctly."""

        # Arrange
        def my_hook(ctx):
            return HookResult.approve()

        # Act
        registered = RegisteredHook(
            hook_type=HookType.POST_TOOL,
            tool_name="write_file",
            function=my_hook,
            priority=25,
            name="my_custom_hook",
            source="yaml",
            enabled=False,
        )

        # Assert
        assert registered.hook_type == HookType.POST_TOOL
        assert registered.tool_name == "write_file"
        assert registered.function is my_hook
        assert registered.priority == 25
        assert registered.name == "my_custom_hook"
        assert registered.source == "yaml"
        assert registered.enabled is False

    def test_registered_hook_wildcard_tool_name(self):
        """Test that RegisteredHook can have None tool_name (wildcard)."""

        # Arrange
        def wildcard_hook(ctx):
            return HookResult.approve()

        # Act
        registered = RegisteredHook(
            hook_type=HookType.PRE_TOOL,
            tool_name=None,
            function=wildcard_hook,
        )

        # Assert
        assert registered.tool_name is None
