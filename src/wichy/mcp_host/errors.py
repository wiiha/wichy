class MCPError(Exception):
    """Base exception for MCP errors."""

    pass


class MCPConfigError(MCPError):
    """Configuration error."""

    pass


class MCPConnectionError(MCPError):
    """Failed to connect to MCP server."""

    pass


class MCPToolExecutionError(MCPError):
    """Tool execution failed."""

    pass


class MCPTimeoutError(MCPError):
    """Operation timed out."""

    pass
