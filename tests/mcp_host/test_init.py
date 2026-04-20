"""Tests for the MCP public API (wichy.mcp_host.__init__).

These tests exercise discover_mcp_tools() and shutdown_mcp() by using
real manager instances with controlled configs, NOT by mocking the
manager itself. This avoids the tautological mock-chain problem where
mocks provide both the input and the asserted output.
"""

import json
from unittest.mock import MagicMock

from wichy.mcp_host import discover_mcp_tools, shutdown_mcp
from wichy.mcp_host.config import MCPConfig
from wichy.mcp_host.manager import _reset_manager, MCPManager


class TestDiscoverMcpTools:
    """Test the discover_mcp_tools entry point with real manager logic."""

    def test_returns_empty_when_no_servers_configured(self, monkeypatch, tmp_path):
        """Should return empty list when no MCP servers are configured."""
        # No config file, no env var → empty config
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)
        _reset_manager()

        result = discover_mcp_tools(set())
        assert result == []

    def test_returns_empty_on_invalid_config(self, monkeypatch, tmp_path):
        """Should return empty list when config is invalid (graceful degradation)."""
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text("{bad json!!")

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)
        _reset_manager()

        result = discover_mcp_tools(set())
        assert result == []

    def test_returns_empty_for_disabled_servers(self, monkeypatch, tmp_path):
        """Should return empty list when all servers are disabled."""
        config_data = {
            "mcpServers": {
                "disabled_srv": {
                    "transport": "stdio",
                    "command": "python",
                    "disabled": True,
                }
            }
        }
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)
        _reset_manager()

        result = discover_mcp_tools(set())
        assert result == []

    def test_returns_empty_when_server_unreachable(self, monkeypatch, tmp_path):
        """Should return empty list when server exists but can't connect (graceful degradation)."""
        config_data = {
            "mcpServers": {
                "bad_srv": {
                    "transport": "stdio",
                    "command": "nonexistent_command_xyz_12345",
                }
            }
        }
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)
        _reset_manager()

        # The real connect_all() will fail and log an error,
        # then discover_all_tools() returns [] because no clients connected
        result = discover_mcp_tools(set())
        assert result == []

    def test_collision_detection_via_manager(self):
        """discover_mcp_tools should skip tools that collide with native names."""
        _reset_manager()
        config = MCPConfig(
            mcpServers={
                "test": {"transport": "stdio", "command": "python"},
            }
        )
        manager = MCPManager(config=config)

        # Inject a mock client with tools that will collide
        mock_client = MagicMock()
        mock_client.list_tools.return_value = [
            {
                "name": "read_file",
                "description": "Reads files",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
            {
                "name": "unique_mcp_tool",
                "description": "Unique tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {"arg": {"type": "string"}},
                },
            },
        ]
        manager._clients = {"test": mock_client}

        # Use the manager's discover_all_tools directly (same logic discover_mcp_tools uses)
        # native_names must contain the NAMESPACED name since that's what the proxy produces
        native_names = {"test_read_file"}  # collision with the MCP-proxied name
        tools = manager.discover_all_tools(native_names)

        # test_read_file should be skipped (collision), test_unique_mcp_tool should be included
        tool_names = [t.name for t in tools]
        assert "test_read_file" not in tool_names
        assert "test_unique_mcp_tool" in tool_names


class TestShutdownMcp:
    """Test shutdown_mcp by exercising real shutdown logic."""

    def test_shutdown_calls_disconnect_on_real_manager(self):
        """shutdown_mcp should disconnect clients from a real manager instance."""
        from wichy.mcp_host.manager import get_mcp_manager

        _reset_manager()
        manager = get_mcp_manager()

        # Inject a mock client to track disconnect calls
        mock_client = MagicMock()
        manager._clients = {"test": mock_client}

        shutdown_mcp()

        mock_client.disconnect.assert_called_once()

    def test_shutdown_handles_empty_manager(self):
        """shutdown_mcp should not raise when no clients are connected."""
        _reset_manager()
        # Fresh manager with no clients
        shutdown_mcp()  # Should not raise
