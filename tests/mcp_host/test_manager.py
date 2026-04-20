"""Tests for MCP manager."""

from unittest.mock import MagicMock

import pytest

from wichy.mcp_host.manager import MCPManager, get_mcp_manager, _reset_manager
from wichy.mcp_host.config import MCPConfig, MCPServerConfigStdio


@pytest.fixture(autouse=True)
def reset_global_manager():
    """Reset the global manager singleton between tests."""
    _reset_manager()
    yield
    _reset_manager()


class MockClient:
    """A mock MCP client for testing."""

    def __init__(self, tools=None, connect_raises=None, list_tools_raises=None):
        self._tools = tools or []
        self._connect_raises = connect_raises
        self._list_tools_raises = list_tools_raises
        self.connected = False

    def connect(self):
        if self._connect_raises:
            raise self._connect_raises
        self.connected = True

    def disconnect(self):
        self.connected = False

    def list_tools(self):
        if self._list_tools_raises:
            raise self._list_tools_raises
        return self._tools


class TestMCPManagerInit:
    """Test MCPManager initialization."""

    def test_default_init(self):
        """Manager initializes with empty state."""
        manager = MCPManager()
        assert manager._config is None
        assert manager._clients == {}

    def test_init_with_config(self):
        """Manager can be initialized with a pre-built config."""
        config = MCPConfig(
            mcpServers={
                "test": MCPServerConfigStdio(transport="stdio", command="python"),
            }
        )
        manager = MCPManager(config=config)
        assert manager._config is config


class TestMCPManagerHasServersConfigured:
    """Test has_servers_configured method."""

    def test_no_servers_configured(self):
        """Returns False when no servers are in config."""
        manager = MCPManager(config=MCPConfig())
        assert manager.has_servers_configured() is False

    def test_servers_configured(self):
        """Returns True when servers exist in config."""
        config = MCPConfig(
            mcpServers={
                "test": MCPServerConfigStdio(transport="stdio", command="python"),
            }
        )
        manager = MCPManager(config=config)
        assert manager.has_servers_configured() is True

    def test_all_servers_disabled_still_configured(self):
        """Returns True even if all servers are disabled (checks configuration, not enabled state)."""
        config = MCPConfig(
            mcpServers={
                "test": MCPServerConfigStdio(
                    transport="stdio", command="python", disabled=True
                ),
            }
        )
        manager = MCPManager(config=config)
        assert manager.has_servers_configured() is True

    def test_lazy_config_loading(self):
        """When config is None, should load config lazily on first call."""
        manager = MCPManager()
        # Should not raise — should call load_mcp_config() which returns empty config
        result = manager.has_servers_configured()
        assert isinstance(result, bool)

    def test_no_reload_when_config_set(self):
        """When config is already set, should not re-load it."""
        config = MCPConfig(
            mcpServers={
                "test": MCPServerConfigStdio(transport="stdio", command="python"),
            }
        )
        manager = MCPManager(config=config)
        # Call twice — config should be the same object
        manager.has_servers_configured()
        assert manager._config is config


class TestMCPManagerConnectAll:
    """Test connect_all method."""

    def test_skip_disabled_servers(self):
        """Disabled servers should be skipped."""
        config = MCPConfig(
            mcpServers={
                "disabled_srv": MCPServerConfigStdio(
                    transport="stdio", command="python", disabled=True
                ),
            }
        )
        manager = MCPManager(config=config)
        manager.connect_all()
        assert "disabled_srv" not in manager._clients

    def test_graceful_degradation_on_connect_failure(self):
        """Connect failures should be logged but not crash."""
        config = MCPConfig(
            mcpServers={
                "bad_server": MCPServerConfigStdio(
                    transport="stdio", command="nonexistent_command"
                ),
            }
        )
        manager = MCPManager(config=config)
        manager.connect_all()
        assert "bad_server" not in manager._clients


class TestMCPManagerDiscoverAllTools:
    """Test discover_all_tools method."""

    def test_discover_tools_with_collision(self):
        """Tools colliding with native tools should be skipped."""
        config = MCPConfig()
        manager = MCPManager(config=config)

        mock_client = MockClient(
            tools=[
                {
                    "name": "echo",
                    "description": "Echo tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"msg": {"type": "string"}},
                    },
                },
                {
                    "name": "unique_tool",
                    "description": "Unique tool",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"arg": {"type": "string"}},
                    },
                },
            ]
        )
        manager._clients = {"test": mock_client}

        native_names = {"read_file", "test_echo", "write_file"}
        tools = manager.discover_all_tools(native_names)

        tool_names = [t.name for t in tools]
        assert "test_echo" not in tool_names
        assert "test_unique_tool" in tool_names

    def test_discover_tools_from_multiple_servers(self):
        """Should collect tools from all connected servers."""
        config = MCPConfig()
        manager = MCPManager(config=config)

        client1 = MockClient(
            tools=[
                {"name": "search", "description": "Search", "inputSchema": {}},
            ]
        )
        client2 = MockClient(
            tools=[
                {"name": "weather", "description": "Weather", "inputSchema": {}},
            ]
        )
        manager._clients = {"srv1": client1, "srv2": client2}

        tools = manager.discover_all_tools(set())
        tool_names = [t.name for t in tools]
        assert "srv1_search" in tool_names
        assert "srv2_weather" in tool_names

    def test_discover_tools_continues_on_list_tools_error(self):
        """If one server's list_tools fails, other servers should still be discovered."""
        config = MCPConfig()
        manager = MCPManager(config=config)

        failing_client = MockClient(list_tools_raises=RuntimeError("server down"))
        working_client = MockClient(
            tools=[
                {"name": "ok_tool", "description": "OK tool", "inputSchema": {}},
            ]
        )
        manager._clients = {"bad": failing_client, "good": working_client}

        tools = manager.discover_all_tools(set())
        tool_names = [t.name for t in tools]
        assert "bad" not in str(tool_names)  # bad server contributed nothing
        assert "good_ok_tool" in tool_names  # good server still discovered


class TestMCPManagerConnectAndDiscover:
    """Test the connect_and_discover convenience method."""

    def test_connect_and_discover_calls_both(self):
        """connect_and_discover should call connect_all then discover_all_tools."""
        config = MCPConfig(
            mcpServers={
                "test": MCPServerConfigStdio(
                    transport="stdio", command="python", disabled=True
                ),
            }
        )
        manager = MCPManager(config=config)
        # Disabled server → connect_all adds no clients → discover returns []
        tools = manager.connect_and_discover(set())
        assert tools == []


class TestMCPManagerDisconnectAll:
    """Test disconnect_all method."""

    def test_disconnect_all_calls_disconnect_on_each_client(self):
        """disconnect_all should call disconnect() on each client before clearing."""
        config = MCPConfig()
        manager = MCPManager(config=config)
        mock_client = MagicMock()
        manager._clients = {"test": mock_client}
        manager.disconnect_all()
        mock_client.disconnect.assert_called_once()
        assert len(manager._clients) == 0

    def test_disconnect_all_best_effort(self):
        """disconnect_all should not raise even if disconnect fails."""
        config = MCPConfig()
        manager = MCPManager(config=config)
        failing_client = MagicMock()
        failing_client.disconnect.side_effect = RuntimeError("disconnection failed")
        manager._clients = {"bad": failing_client}
        manager.disconnect_all()
        assert len(manager._clients) == 0


class TestMCPManagerSingleton:
    """Test the global singleton pattern."""

    def test_get_mcp_manager_returns_instance(self):
        """get_mcp_manager should return an MCPManager instance."""
        manager = get_mcp_manager()
        assert isinstance(manager, MCPManager)

    def test_get_mcp_manager_returns_same_instance(self):
        """get_mcp_manager should return the same instance on repeated calls."""
        m1 = get_mcp_manager()
        m2 = get_mcp_manager()
        assert m1 is m2

    def test_reset_manager_creates_new_instance(self):
        """_reset_manager should allow a fresh instance."""
        m1 = get_mcp_manager()
        _reset_manager()
        m2 = get_mcp_manager()
        assert m1 is not m2
