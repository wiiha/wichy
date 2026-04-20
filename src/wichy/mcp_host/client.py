import json
from typing import Any

from .async_bridge import mcp_async_bridge
from .errors import MCPConnectionError, MCPToolExecutionError
from .config import MCPServerConfigStdio, MCPServerConfigHttp


class MCPClient:
    """Manages connection to a single MCP server."""

    def __init__(
        self,
        name: str,
        config: MCPServerConfigStdio | MCPServerConfigHttp,
        bridge=None,
    ):
        self.name = name
        self.config = config
        self._bridge = bridge or mcp_async_bridge
        self._client: Any = None  # fastmcp.Client
        self._tools: list[dict] | None = None

    def connect(self) -> None:
        """Establish connection to MCP server."""
        from fastmcp import Client

        if self._client is not None:
            return

        try:
            if self.config.transport == "stdio":
                from fastmcp.client.transports import StdioTransport

                transport = StdioTransport(
                    command=self.config.command,
                    args=self.config.args,
                    env=self.config.get_interpolated_env(),
                )
            else:  # http
                from fastmcp.client.transports import StreamableHttpTransport

                transport = StreamableHttpTransport(
                    url=self.config.url,
                    headers=self.config.get_interpolated_headers(),
                )

            self._client = Client(transport)
            # Enter async context via bridge
            self._bridge.run_sync(self._client.__aenter__())

        except Exception as e:
            self._client = None
            raise MCPConnectionError(
                f"Failed to connect to MCP server '{self.name}': {e}"
            )

    def disconnect(self) -> None:
        """Close connection to MCP server."""
        if self._client is not None:
            try:
                self._bridge.run_sync(self._client.__aexit__(None, None, None))
            except Exception:
                pass  # Best effort cleanup
            finally:
                self._client = None

    def list_tools(self) -> list[dict]:
        """Discover available tools from this server.

        Results are cached after first call. In V1, there is no
        dynamic re-discovery — reconnect to see new tools.
        """
        if self._tools is None:
            if self._client is None:
                raise MCPConnectionError(f"Not connected to '{self.name}'")

            try:
                result = self._bridge.run_sync(self._client.list_tools())
                self._tools = [
                    {
                        "name": t.name,
                        "description": t.description or "",
                        "inputSchema": t.model_dump().get("inputSchema", {}),
                    }
                    for t in result
                ]
            except Exception as e:
                raise MCPToolExecutionError(
                    f"Failed to list tools from '{self.name}': {e}"
                )

        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Execute a tool on this server. Returns result as string.

        Always returns a string — errors are returned as error strings,
        never raised. This matches native wichy tool behavior.
        """
        if self._client is None:
            return (
                f"[MCP Error] {self.name}/{tool_name}: Not connected to '{self.name}'"
            )

        try:
            result = self._bridge.run_sync(
                self._client.call_tool(tool_name, arguments),
                timeout=60.0,
            )
            return self._format_result(result)
        except Exception as e:
            # Return error string (matches native tool behavior — never raises)
            return f"[MCP Error] {self.name}/{tool_name}: {e}"

    def _format_result(self, result: Any) -> str:
        """Format MCP tool result to string for wichy context."""
        # Handle MCP CallToolResult with content list
        if hasattr(result, "content"):
            parts = []
            for block in result.content:
                block_type = getattr(block, "type", None)

                if block_type == "text":
                    parts.append(block.text if hasattr(block, "text") else str(block))
                elif block_type == "image":
                    mime = getattr(block, "mimeType", "image/png")
                    parts.append(f"[Image: {mime}]")
                elif block_type == "audio":
                    mime = getattr(block, "mimeType", "audio/wav")
                    parts.append(f"[Audio: {mime}]")
                elif block_type == "resource":
                    uri = getattr(block, "uri", "unknown")
                    parts.append(f"[Resource: {uri}]")
                elif isinstance(block, dict):
                    parts.append(json.dumps(block, indent=2))
                elif hasattr(block, "text"):
                    # Fallback for blocks with .text but no recognized type
                    parts.append(block.text)
                else:
                    parts.append(str(block))

            return "\n".join(parts)

        # Handle direct string
        if isinstance(result, str):
            return result

        # Handle dict/object
        if isinstance(result, dict):
            return json.dumps(result, indent=2)

        return str(result)
