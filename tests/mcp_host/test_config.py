"""Tests for MCP configuration loading."""

import json
import os


from wichy.mcp_host.config import (
    MCPServerConfigStdio,
    MCPServerConfigHttp,
    MCPConfig,
    load_mcp_config,
)


class TestMCPServerConfigStdio:
    """Test MCPServerConfigStdio model."""

    def test_interpolate_env_vars(self):
        """Test environment variable interpolation in env dict."""
        os.environ["TEST_MCP_KEY"] = "secret123"
        try:
            config = MCPServerConfigStdio(
                transport="stdio",
                command="python",
                args=["server.py"],
                env={"API_KEY": "${TEST_MCP_KEY}"},
            )
            interpolated = config.get_interpolated_env()
            assert interpolated["API_KEY"] == "secret123"
        finally:
            del os.environ["TEST_MCP_KEY"]

    def test_interpolate_missing_env_var(self):
        """Test interpolation with missing env var — os.path.expandvars behavior."""
        config = MCPServerConfigStdio(
            transport="stdio",
            command="python",
            args=["server.py"],
            env={"API_KEY": "${NONEXISTENT_VAR_12345}"},
        )
        interpolated = config.get_interpolated_env()
        # On Linux, undefined vars are kept as-is by os.path.expandvars
        # (on some platforms they become empty strings)
        assert interpolated["API_KEY"] in ("${NONEXISTENT_VAR_12345}", "")

    def test_extra_fields_ignored(self):
        """Test that extra fields in config are ignored (forward compat)."""
        config = MCPServerConfigStdio(
            transport="stdio",
            command="python",
            future_field="some_value",
        )
        assert config.command == "python"
        assert not hasattr(config, "future_field")


class TestMCPServerConfigHttp:
    """Test MCPServerConfigHttp model."""

    def test_interpolate_headers(self):
        """Test header environment variable interpolation."""
        os.environ["TEST_MCP_TOKEN"] = "tok_abc123"
        try:
            config = MCPServerConfigHttp(
                transport="http",
                url="http://localhost:3000/mcp",
                headers={"Authorization": "Bearer ${TEST_MCP_TOKEN}"},
            )
            interpolated = config.get_interpolated_headers()
            assert interpolated["Authorization"] == "Bearer tok_abc123"
        finally:
            del os.environ["TEST_MCP_TOKEN"]


class TestMCPConfig:
    """Test the top-level MCPConfig model."""

    def test_config_from_json(self):
        """Test parsing config from JSON string."""
        json_str = json.dumps(
            {
                "mcpServers": {
                    "test": {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["server.py"],
                    }
                }
            }
        )
        config = MCPConfig.model_validate_json(json_str)
        assert "test" in config.mcpServers
        assert config.mcpServers["test"].command == "python"

    def test_discriminated_union_resolves_stdio(self):
        """Test that the Stdio|Http union resolves correctly for stdio."""
        json_str = json.dumps(
            {
                "mcpServers": {
                    "s1": {
                        "transport": "stdio",
                        "command": "python",
                    }
                }
            }
        )
        config = MCPConfig.model_validate_json(json_str)
        assert isinstance(config.mcpServers["s1"], MCPServerConfigStdio)

    def test_discriminated_union_resolves_http(self):
        """Test that the Stdio|Http union resolves correctly for http."""
        json_str = json.dumps(
            {
                "mcpServers": {
                    "h1": {
                        "transport": "http",
                        "url": "http://localhost:3000",
                    }
                }
            }
        )
        config = MCPConfig.model_validate_json(json_str)
        assert isinstance(config.mcpServers["h1"], MCPServerConfigHttp)


class TestLoadMcpConfig:
    """Test config loading from files and env vars."""

    def test_load_from_file(self, monkeypatch, tmp_path):
        """Test loading config from a JSON file."""
        config_data = {
            "mcpServers": {
                "myserver": {
                    "transport": "stdio",
                    "command": "python",
                }
            }
        }
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        config = load_mcp_config()
        assert "myserver" in config.mcpServers

    def test_load_from_env_var(self, monkeypatch, tmp_path):
        """Test loading config from environment variable."""
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)

        env_data = json.dumps(
            {
                "mcpServers": {
                    "envserver": {
                        "transport": "http",
                        "url": "http://example.com/mcp",
                    }
                }
            }
        )
        monkeypatch.setenv("WICHY_MCP_SERVERS", env_data)

        config = load_mcp_config()
        assert "envserver" in config.mcpServers

    def test_load_empty_when_no_config(self, monkeypatch, tmp_path):
        """Test that empty config is returned when nothing is configured."""
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert config.mcpServers == {}
        finally:
            os.chdir(orig_cwd)

    def test_load_invalid_json_file(self, monkeypatch, tmp_path):
        """Test graceful handling of invalid JSON in config file."""
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text("{invalid json!!!}")

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert config.mcpServers == {}
        finally:
            os.chdir(orig_cwd)

    def test_load_invalid_env_var(self, monkeypatch, tmp_path):
        """Test graceful handling of invalid JSON in env var."""
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.setenv("WICHY_MCP_SERVERS", "{invalid}")

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert config.mcpServers == {}
        finally:
            os.chdir(orig_cwd)

    def test_file_takes_priority_over_env(self, monkeypatch, tmp_path):
        """Test that the config file takes priority over env var."""
        config_data = {
            "mcpServers": {
                "from_file": {
                    "transport": "stdio",
                    "command": "file_server",
                }
            }
        }
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text(json.dumps(config_data))

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.setenv(
            "WICHY_MCP_SERVERS",
            json.dumps(
                {
                    "mcpServers": {
                        "from_env": {
                            "transport": "http",
                            "url": "http://env.com",
                        }
                    }
                }
            ),
        )

        config = load_mcp_config()
        assert "from_file" in config.mcpServers
        assert "from_env" not in config.mcpServers

    def test_valid_json_invalid_schema(self, monkeypatch, tmp_path):
        """Test graceful handling of valid JSON with invalid server schema."""
        config_file = tmp_path / "mcp_servers.json"
        config_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "bad_transport": {
                            "transport": "ftp",
                            "command": "python",
                        }
                    }
                }
            )
        )

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert config.mcpServers == {}
        finally:
            os.chdir(orig_cwd)

    def test_load_local_only(self, monkeypatch, tmp_path):
        """Test loading from local file when no global config exists."""
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        local_dir = tmp_path / ".wichy"
        local_dir.mkdir()
        local_file = local_dir / "mcp_servers.json"
        local_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "local_server": {
                            "transport": "stdio",
                            "command": "local_cmd",
                        }
                    }
                }
            )
        )

        import os

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert "local_server" in config.mcpServers
            assert config.mcpServers["local_server"].command == "local_cmd"
        finally:
            os.chdir(orig_cwd)

    def test_load_local_overrides_global(self, monkeypatch, tmp_path):
        """Local server config replaces global server with same key entirely."""
        global_file = tmp_path / "mcp_servers.json"
        global_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared_server": {
                            "transport": "stdio",
                            "command": "global_cmd",
                            "args": ["global_arg"],
                        }
                    }
                }
            )
        )
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        local_dir = tmp_path / ".wichy"
        local_dir.mkdir()
        local_file = local_dir / "mcp_servers.json"
        local_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "shared_server": {
                            "transport": "http",
                            "url": "http://local.local",
                        }
                    }
                }
            )
        )

        import os

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert "shared_server" in config.mcpServers
            # Local replaced the global entry entirely
            assert config.mcpServers["shared_server"].transport == "http"
            assert config.mcpServers["shared_server"].url == "http://local.local"
            # Old global fields must NOT survive
            assert not hasattr(config.mcpServers["shared_server"], "command")
        finally:
            os.chdir(orig_cwd)

    def test_load_global_and_local_merge(self, monkeypatch, tmp_path):
        """Different keys in global and local coexist."""
        global_file = tmp_path / "mcp_servers.json"
        global_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "global_server": {
                            "transport": "stdio",
                            "command": "global_cmd",
                        }
                    }
                }
            )
        )
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        local_dir = tmp_path / ".wichy"
        local_dir.mkdir()
        local_file = local_dir / "mcp_servers.json"
        local_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "local_server": {
                            "transport": "http",
                            "url": "http://local.local",
                        }
                    }
                }
            )
        )

        import os

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert "global_server" in config.mcpServers
            assert "local_server" in config.mcpServers
        finally:
            os.chdir(orig_cwd)

    def test_invalid_global_valid_local(self, monkeypatch, tmp_path):
        """INV-001: invalid global → skip global, still load local."""
        global_file = tmp_path / "mcp_servers.json"
        global_file.write_text("{bad json")

        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        local_dir = tmp_path / ".wichy"
        local_dir.mkdir()
        local_file = local_dir / "mcp_servers.json"
        local_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "local_server": {
                            "transport": "stdio",
                            "command": "local_cmd",
                        }
                    }
                }
            )
        )

        import os

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert "local_server" in config.mcpServers
            assert config.mcpServers["local_server"].command == "local_cmd"
        finally:
            os.chdir(orig_cwd)

    def test_valid_global_invalid_local(self, monkeypatch, tmp_path):
        """INV-002: valid global + invalid local → fall back to global only."""
        global_file = tmp_path / "mcp_servers.json"
        global_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "global_server": {
                            "transport": "stdio",
                            "command": "global_cmd",
                        }
                    }
                }
            )
        )
        monkeypatch.setattr("wichy.config.settings.wichy_home", tmp_path)
        monkeypatch.delenv("WICHY_MCP_SERVERS", raising=False)

        local_dir = tmp_path / ".wichy"
        local_dir.mkdir()
        local_file = local_dir / "mcp_servers.json"
        local_file.write_text("{bad json")

        import os

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            config = load_mcp_config()
            assert "global_server" in config.mcpServers
            assert "local_server" not in config.mcpServers
        finally:
            os.chdir(orig_cwd)
