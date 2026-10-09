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

    def test_each_error_type_is_a_catchable_distinct_class(self):
        """Each error type is a real, catchable exception class."""
        for error_cls in (
            MCPConfigError,
            MCPConnectionError,
            MCPToolExecutionError,
            MCPTimeoutError,
        ):
            with pytest.raises(error_cls):
                raise error_cls("boom")
