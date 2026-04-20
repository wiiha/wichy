"""Tests for MCP errors module."""

import pytest

from wichy.mcp_host.errors import (
    MCPError,
    MCPConfigError,
    MCPConnectionError,
    MCPToolExecutionError,
    MCPTimeoutError,
)


class TestMCPErrorHierarchy:
    """Test the exception class hierarchy is usable for catch-all patterns."""

    def test_all_subclasses_catchable_via_base(self):
        """All specific errors should be catchable via MCPError base class."""
        for exc_class in [
            MCPConfigError,
            MCPConnectionError,
            MCPToolExecutionError,
            MCPTimeoutError,
        ]:
            with pytest.raises(MCPError):
                raise exc_class("test message")

    def test_error_message_preserved(self):
        """Error message should be accessible via str() and args."""
        msg = "connection refused on port 3000"
        err = MCPConnectionError(msg)
        assert str(err) == msg
        assert err.args == (msg,)

    def test_specific_errors_are_distinct(self):
        """Each error type should be distinguishable (not aliases)."""
        assert MCPConfigError is not MCPConnectionError
        assert MCPConnectionError is not MCPToolExecutionError
        assert MCPToolExecutionError is not MCPTimeoutError
