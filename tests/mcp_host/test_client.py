"""Tests for MCP client."""

from unittest.mock import MagicMock

import pytest

from wichy.mcp_host.client import MCPClient
from wichy.mcp_host.config import MCPServerConfigHttp, MCPServerConfigStdio
from wichy.mcp_host.errors import MCPConnectionError, MCPToolExecutionError

# Mocking fastmcp.Client's async context manager produces unawaited coroutines.
# This is inherent to mocking async protocols in sync test code — the coroutines
# are passed to MagicMock.run_sync() which records them but never awaits them.
pytestmark = pytest.mark.filterwarnings(
    "ignore:coroutine.*was never awaited:RuntimeWarning"
)


class FakeBridge:
    """A fake async bridge that tracks calls and closes coroutines.

    Unlike MagicMock, this properly closes coroutine objects to prevent
    RuntimeWarning about unawaited coroutines in tests.
    """

    def __init__(self):
        self.calls = []
        self.run_sync_returns = None
        self.run_sync_side_effect = None

    def run_sync(self, coro, timeout=60.0):
        """Track the call, close the coroutine, return mock result."""
        self.calls.append(("run_sync", coro, timeout))

        # Close the coroutine to prevent RuntimeWarning
        if hasattr(coro, "close"):
            coro.close()

        if self.run_sync_side_effect is not None:
            raise self.run_sync_side_effect

        if self.run_sync_returns is not None:
            return self.run_sync_returns

        return MagicMock()


def _mock_client(**methods):
    """Create a mock for fastmcp.Client without async context manager methods.

    Using MagicMock() directly for _client generates __aenter__/__aexit__
    coroutines that are never awaited, causing RuntimeWarnings in tests.
    This helper creates a mock with only the explicitly requested methods.
    """
    mock = MagicMock(spec=["list_tools", "call_tool"])
    for name, impl in methods.items():
        getattr(mock, name).side_effect = impl
    return mock


class TestMCPClientInit:
    """Test MCPClient initialization."""

    def test_basic_stdio_init(self):
        """Test creating a client with stdio config."""
        config = MCPServerConfigStdio(
            transport="stdio",
            command="python",
            args=["server.py"],
        )
        client = MCPClient("test", config, bridge=FakeBridge())
        assert client.name == "test"
        assert client.config is config
        assert client._client is None
        assert client._tools is None

    def test_bridge_injection(self):
        """Test that bridge can be injected for testing."""
        bridge = FakeBridge()
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=bridge)
        assert client._bridge is bridge


class TestMCPClientConnect:
    """Test connect() method."""

    def test_connect_creates_client(self):
        """connect() should set _client to a fastmcp Client."""
        bridge = FakeBridge()

        config = MCPServerConfigStdio(
            transport="stdio", command="python", args=["server.py"]
        )
        client = MCPClient("test", config, bridge=bridge)

        client.connect()
        assert client._client is not None
        # Bridge should have been called for __aenter__
        assert len(bridge.calls) == 1

    def test_connect_idempotent(self):
        """connect() should be a no-op if already connected."""
        bridge = FakeBridge()

        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=bridge)
        client.connect()
        first_client = client._client

        client.connect()
        assert client._client is first_client  # Same object
        assert len(bridge.calls) == 1  # Not called again

    def test_connect_failure_raises_connection_error(self):
        """connect() should raise MCPConnectionError on failure."""
        bridge = FakeBridge()
        bridge.run_sync_side_effect = RuntimeError("transport failed")

        config = MCPServerConfigStdio(transport="stdio", command="nonexistent_cmd")
        client = MCPClient("test", config, bridge=bridge)

        with pytest.raises(
            MCPConnectionError, match="Failed to connect to MCP server 'test'"
        ):
            client.connect()
        assert client._client is None  # Cleaned up after failure

    def test_connect_http_transport(self):
        """connect() should create HTTP transport for MCPServerConfigHttp."""
        bridge = FakeBridge()

        config = MCPServerConfigHttp(
            transport="http",
            url="http://localhost:3000/mcp",
        )
        client = MCPClient("api", config, bridge=bridge)
        client.connect()
        assert client._client is not None


class TestMCPClientDisconnect:
    """Test disconnect() method."""

    def test_disconnect_resets_client(self):
        """disconnect() should set _client to None."""
        bridge = FakeBridge()

        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=bridge)
        client.connect()
        assert client._client is not None

        client.disconnect()
        assert client._client is None

    def test_disconnect_best_effort(self):
        """disconnect() should not raise even if __aexit__ fails."""
        bridge = FakeBridge()
        first_call = True
        original_run_sync = bridge.run_sync

        def run_sync_side_effect(coro, timeout=60.0):
            nonlocal first_call
            if first_call:
                first_call = False
                return original_run_sync(coro, timeout)
            # Close the coroutine before raising to prevent RuntimeWarning
            if hasattr(coro, "close"):
                coro.close()
            raise RuntimeError("aexit failed")

        bridge.run_sync = run_sync_side_effect

        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=bridge)
        client.connect()

        # Should not raise despite __aexit__ failure
        client.disconnect()
        assert client._client is None

    def test_disconnect_when_not_connected(self):
        """disconnect() on unconnected client is a no-op."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=FakeBridge())
        # Should not raise
        client.disconnect()
        assert client._client is None


class TestMCPClientCallTool:
    """Test call_tool with various scenarios."""

    def test_call_tool_with_real_format_result(self):
        """call_tool should return formatted result from _format_result."""
        bridge = MagicMock()
        # Simulate a CallToolResult with content
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Hello from MCP"

        mock_result = MagicMock()
        mock_result.content = [text_block]
        bridge.run_sync.return_value = mock_result

        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=bridge)
        client._client = _mock_client()

        result = client.call_tool("echo", {"message": "hello"})
        assert result == "Hello from MCP"

    def test_call_tool_returns_error_string_on_exception(self):
        """call_tool should return error string, never raise."""
        bridge = MagicMock()
        bridge.run_sync.side_effect = RuntimeError("connection lost")

        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config, bridge=bridge)
        client._client = _mock_client()

        result = client.call_tool("echo", {"message": "hello"})
        assert "[MCP Error]" in result
        assert "connection lost" in result

    def test_call_tool_returns_error_when_not_connected(self):
        """call_tool should return error string when client is not connected."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)
        client._client = None

        result = client.call_tool("echo", {"message": "hello"})
        assert "[MCP Error]" in result
        assert "Not connected" in result


class TestMCPClientFormatResult:
    """Test _format_result with various result types.

    Uses simple namespace objects instead of MagicMock for the CallToolResult
    to avoid the auto-attribute problem where hasattr(mock, 'content') always
    returns True by accident.
    """

    @staticmethod
    def _make_result(content):
        """Create a lightweight result object with a .content attribute."""

        class CallToolResult:
            def __init__(self, content):
                self.content = content

        return CallToolResult(content)

    def test_format_string_result(self):
        """Test formatting a plain string result."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)
        assert client._format_result("hello world") == "hello world"

    def test_format_dict_result(self):
        """Test formatting a dict result."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)
        result = client._format_result({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_format_unknown_object_result(self):
        """Test formatting an unknown object type falls back to str()."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)
        result = client._format_result(42)
        assert result == "42"

    def test_format_content_result_text(self):
        """Test formatting a CallToolResult-like object with text content."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        text_block = MagicMock(spec=["type", "text"])
        text_block.type = "text"
        text_block.text = "Hello from MCP"

        result = client._format_result(self._make_result([text_block]))
        assert result == "Hello from MCP"

    def test_format_content_result_image(self):
        """Test formatting a result with an image content block."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        image_block = MagicMock(spec=["type", "mimeType"])
        image_block.type = "image"
        image_block.mimeType = "image/png"

        result = client._format_result(self._make_result([image_block]))
        assert "[Image: image/png]" in result

    def test_format_content_result_audio(self):
        """Test formatting a result with an audio content block."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        audio_block = MagicMock(spec=["type", "mimeType"])
        audio_block.type = "audio"
        audio_block.mimeType = "audio/wav"

        result = client._format_result(self._make_result([audio_block]))
        assert "[Audio: audio/wav]" in result

    def test_format_content_result_resource(self):
        """Test formatting a result with a resource content block."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        resource_block = MagicMock(spec=["type", "uri"])
        resource_block.type = "resource"
        resource_block.uri = "file:///data.csv"

        result = client._format_result(self._make_result([resource_block]))
        assert "[Resource: file:///data.csv]" in result

    def test_format_content_result_dict_block(self):
        """Test formatting a dict-type content block inside a content list."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        dict_block = {"type": "embedded", "data": "value"}

        result = client._format_result(self._make_result([dict_block]))
        assert "embedded" in result
        assert "value" in result

    def test_format_text_fallback_for_untyped_block(self):
        """Test that blocks with .text but no recognized .type use the text fallback."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        # A block with .text but type=None (unknown type)
        block = MagicMock(spec=["text"])
        block.text = "fallback text content"

        result = client._format_result(self._make_result([block]))
        assert result == "fallback text content"

    def test_format_mixed_content(self):
        """Test formatting a result with multiple content blocks."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        text_block = MagicMock(spec=["type", "text"])
        text_block.type = "text"
        text_block.text = "Here is the data:"

        resource_block = MagicMock(spec=["type", "uri"])
        resource_block.type = "resource"
        resource_block.uri = "file:///data.csv"

        result = client._format_result(self._make_result([text_block, resource_block]))
        assert "Here is the data:" in result
        assert "[Resource: file:///data.csv]" in result
        assert "\n" in result

    def test_format_str_fallback_for_unknown_block(self):
        """Test that unrecognized content blocks fall back to str()."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        class CustomObj:
            def __str__(self):
                return "custom_str"

        result = client._format_result(self._make_result([CustomObj()]))
        assert result == "custom_str"

    def test_format_result_without_content_attr(self):
        """Test that result without .content falls through to str/dict checks."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)

        # An object without .content — should hit the str() fallback
        class NoContent:
            def __str__(self):
                return "no_content_str"

        result = client._format_result(NoContent())
        assert result == "no_content_str"


class TestMCPClientListTools:
    """Test list_tools with mock bridge and client."""

    def test_list_tools_raises_when_not_connected(self):
        """list_tools should raise MCPConnectionError when not connected."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        client = MCPClient("test", config)
        client._client = None

        with pytest.raises(MCPConnectionError):
            client.list_tools()

    def test_list_tools_caches_result(self):
        """list_tools should cache the result after first call."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        bridge = MagicMock()

        tool1 = MagicMock()
        tool1.name = "echo"
        tool1.description = "Echo tool"
        tool1.model_dump.return_value = {"inputSchema": {}}

        bridge.run_sync.return_value = [tool1]

        client = MCPClient("test", config, bridge=bridge)
        client._client = _mock_client()

        tools1 = client.list_tools()
        tools2 = client.list_tools()

        assert tools1 is tools2
        assert bridge.run_sync.call_count == 1

    def test_list_tools_raises_on_bridge_error(self):
        """list_tools should raise MCPToolExecutionError when bridge fails."""
        config = MCPServerConfigStdio(transport="stdio", command="python")
        bridge = MagicMock()
        bridge.run_sync.side_effect = RuntimeError("server disconnected")

        client = MCPClient("test", config, bridge=bridge)
        client._client = _mock_client()

        with pytest.raises(MCPToolExecutionError, match="Failed to list tools"):
            client.list_tools()
