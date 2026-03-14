"""
Test cases for the block_on decorator.
"""

import pytest
from typing import Optional
from wichy.tools.human_verification import block_on

# Test fixtures - simple callable classes and functions


class MockTool:
    """A mock tool class to test block_on with self parameter."""

    def __init__(self, blocked: bool = False, reason: str = "Blocked by test"):
        self.blocked = blocked
        self.reason = reason
        self.executed = False

    def should_block(
        self, command: str, timeout: int = 30
    ) -> tuple[bool, Optional[str]]:
        """Decision function that uses self to introspect."""
        return self.blocked, self.reason

    @block_on(should_block)
    def execute(self, command: str, timeout: int = 30) -> str:
        """Mock execute method."""
        self.executed = True
        return f"Executed: {command}"


def test_block_on_blocks_execution_when_decision_true():
    """Test that execution is blocked when decision returns (True, message)."""
    tool = MockTool(blocked=True, reason="Dangerous command")

    with pytest.raises(PermissionError) as exc_info:
        tool.execute("rm -rf /", timeout=30)

    assert "Dangerous command" in str(exc_info.value)
    assert tool.executed is False  # Ensure execute was not called


def test_block_on_blocks_with_default_message_when_no_reason():
    """Test that a default message is used when decision returns (True, None)."""
    tool = MockTool(blocked=True, reason=None)

    with pytest.raises(PermissionError) as exc_info:
        tool.execute("some command")

    assert "blocked by should_block" in str(exc_info.value).lower()
    assert tool.executed is False


def test_block_on_allows_execution_when_decision_false():
    """Test that execution proceeds normally when decision returns (False, _)."""
    tool = MockTool(blocked=False, reason="Not used")

    result = tool.execute("ls -la", timeout=10)

    assert result == "Executed: ls -la"
    assert tool.executed is True


def test_block_on_preserves_function_metadata():
    """Test that block_on preserves the wrapped function's name and docstring."""
    tool = MockTool(blocked=False)

    assert tool.execute.__name__ == "execute"
    assert (
        "Mock execute method" in tool.execute.__doc__
        or "execute" in tool.execute.__doc__
    )


def test_block_on_decision_receives_correct_arguments():
    """Test that the decision function receives exactly the same arguments as execute."""
    received_args = []
    received_kwargs = {}

    def custom_decision(
        self, command: str, timeout: int = 30
    ) -> tuple[bool, Optional[str]]:
        received_args.append(command)
        received_kwargs.update({"timeout": timeout})
        return False, None

    class TestTool:
        @block_on(custom_decision)
        def execute(self, command: str, timeout: int = 30) -> str:
            return "done"

    tool = TestTool()
    tool.execute("test_cmd", timeout=45)

    assert received_args == ["test_cmd"]
    assert received_kwargs == {"timeout": 45}


def test_block_on_with_positional_and_keyword_args():
    """Test block_on works with mixed positional and keyword arguments."""
    decisions = []

    def tracking_decision(
        self, command: str, timeout: int = 30
    ) -> tuple[bool, Optional[str]]:
        decisions.append((command, timeout))
        return False, None

    class TestTool:
        @block_on(tracking_decision)
        def execute(self, command: str, timeout: int = 30) -> str:
            return "executed"

    tool = TestTool()
    # Call with positional arg
    tool.execute("cmd1")
    # Call with keyword arg
    tool.execute("cmd2", timeout=60)
    # Call with both
    tool.execute("cmd3", timeout=90)

    assert decisions[0] == ("cmd1", 30)  # default timeout
    assert decisions[1] == ("cmd2", 60)
    assert decisions[2] == ("cmd3", 90)


def test_block_on_with_multiple_parameters():
    """Test block_on with a decision function that inspects multiple parameters."""

    class MultiParamTool:
        def __init__(self):
            self.sensitive_mode = True

        def check_security(
            self, operation: str, path: str, force: bool = False
        ) -> tuple[bool, Optional[str]]:
            if self.sensitive_mode and operation == "delete" and not force:
                return True, "Cannot delete in sensitive mode without force=True"
            return False, None

        @block_on(check_security)
        def execute(self, operation: str, path: str, force: bool = False) -> str:
            return f"Performed {operation} on {path}"

    tool = MultiParamTool()

    # Should block
    with pytest.raises(PermissionError) as exc:
        tool.execute("delete", "/data/file.txt")
    assert "sensitive mode" in str(exc.value).lower()

    # Should allow with force
    result = tool.execute("delete", "/data/file.txt", force=True)
    assert "Performed delete" in result
