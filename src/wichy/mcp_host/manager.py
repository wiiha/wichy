from typing import Optional

from wichy.console import user_console
from .config import load_mcp_config, MCPConfig
from .client import MCPClient
from .tool_proxy import MCPToolProxy


class MCPManager:
    """Manages connections to multiple MCP servers."""

    def __init__(self, config: Optional[MCPConfig] = None):
        self._config = config  # None means "load on demand"
        self._clients: dict[str, MCPClient] = {}

    def has_servers_configured(self) -> bool:
        """Check if any MCP servers are configured (doesn't check enabled)."""
        if self._config is None:
            self._config = load_mcp_config()
        return len(self._config.mcpServers) > 0

    def connect_all(self) -> None:
        """Connect to all configured servers. Logs errors but doesn't fail."""
        if self._config is None:
            self._config = load_mcp_config()

        for name, server_config in self._config.mcpServers.items():
            if server_config.disabled:
                continue

            try:
                client = MCPClient(name, server_config)
                client.connect()
                self._clients[name] = client
            except Exception as e:
                # Log but continue - graceful degradation
                user_console.print(
                    f"[red]Failed to connect to MCP server '{name}': {e}[/red]"
                )

    def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        for client in self._clients.values():
            try:
                client.disconnect()
            except Exception:
                pass  # Best effort
        self._clients.clear()

    def discover_all_tools(self, existing_tool_names: set[str]) -> list[MCPToolProxy]:
        """
        Discover tools from all connected servers.

        Args:
            existing_tool_names: Names of native tools (for collision detection)

        Returns:
            List of MCPToolProxy instances (excludes colliding tools)
        """
        tools = []

        for server_name, client in self._clients.items():
            try:
                server_tools = client.list_tools()

                for tool_def in server_tools:
                    proxy = MCPToolProxy(
                        server_name=server_name,
                        client=client,
                        tool_definition=tool_def,
                    )

                    # Check for collision with native tools
                    if proxy.name in existing_tool_names:
                        user_console.print(
                            f"[yellow]MCP tool '{proxy.name}' collides with native tool, skipping[/yellow]"
                        )
                        continue

                    tools.append(proxy)

            except Exception as e:
                user_console.print(
                    f"[red]Failed to discover tools from '{server_name}': {e}[/red]"
                )

        return tools

    def connect_and_discover(self, existing_tool_names: set[str]) -> list[MCPToolProxy]:
        """Convenience: connect all servers and discover tools."""
        self.connect_all()
        return self.discover_all_tools(existing_tool_names)


# Singleton
_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Get the global MCP manager instance."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager


def _reset_manager():
    """Reset the singleton manager. For testing only."""
    global _manager
    if _manager is not None:
        try:
            _manager.disconnect_all()
        except Exception:
            pass
    _manager = None
