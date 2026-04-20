"""
MCP (Model Context Protocol) integration for wichy.

Allows wichy to act as an MCP Host, connecting to MCP servers
and using their tools as if they were native wichy tools.
"""

from .manager import get_mcp_manager, MCPManager
from .tool_proxy import MCPToolProxy
from .async_bridge import mcp_async_bridge
from .errors import (
    MCPError,
    MCPConfigError,
    MCPConnectionError,
    MCPToolExecutionError,
    MCPTimeoutError,
)

__all__ = [
    "get_mcp_manager",
    "MCPManager",
    "MCPToolProxy",
    "mcp_async_bridge",
    "discover_mcp_tools",
    "shutdown_mcp",
    "MCPError",
    "MCPConfigError",
    "MCPConnectionError",
    "MCPToolExecutionError",
    "MCPTimeoutError",
]


def discover_mcp_tools(existing_tool_names: set[str]) -> list:
    """
    Main entry point for MCP tool discovery.

    Called from __main__.py during startup.

    Args:
        existing_tool_names: Set of native tool names (for collision detection)

    Returns:
        List of MCPToolProxy instances
    """
    manager = get_mcp_manager()

    if not manager.has_servers_configured():
        return []

    try:
        return manager.connect_and_discover(existing_tool_names)
    except Exception as e:
        from wichy.console import user_console

        user_console.print(f"[red]MCP discovery failed: {e}[/red]")
        return []


def shutdown_mcp() -> None:
    """Cleanup MCP connections. Called on exit."""
    manager = get_mcp_manager()
    manager.disconnect_all()
    mcp_async_bridge.shutdown()
