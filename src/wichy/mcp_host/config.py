"""MCP server configuration loading and models.

Provides Pydantic models for MCP server configuration and a loader that
reads from a JSON file or environment variable with graceful degradation.
"""

from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel

from wichy.config import settings
from wichy.console import user_console

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MCPServerConfigStdio(BaseModel):
    """Configuration for an MCP server that communicates over stdio."""

    model_config = {"extra": "ignore"}

    transport: Literal["stdio"]
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    disabled: bool = False

    def get_interpolated_env(self) -> dict[str, str]:
        """Return the env dict with ``${VAR}`` values interpolated via os.path.expandvars."""
        return {k: os.path.expandvars(v) for k, v in self.env.items()}


class MCPServerConfigHttp(BaseModel):
    """Configuration for an MCP server that communicates over HTTP."""

    model_config = {"extra": "ignore"}

    transport: Literal["http"]
    url: str
    headers: dict[str, str] = {}
    disabled: bool = False

    def get_interpolated_headers(self) -> dict[str, str]:
        """Return the headers dict with ``${VAR}`` values interpolated via os.path.expandvars."""
        return {k: os.path.expandvars(v) for k, v in self.headers.items()}


# Union type — any supported server configuration
MCPServerConfig = MCPServerConfigStdio | MCPServerConfigHttp


class MCPConfig(BaseModel):
    """Top-level MCP configuration container."""

    model_config = {"extra": "ignore"}

    mcpServers: dict[str, MCPServerConfig] = {}


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_MCP_CONFIG_FILE = "mcp_servers.json"
_MCP_ENV_VAR = "WICHY_MCP_SERVERS"


def load_mcp_config() -> MCPConfig:
    """Load MCP server configuration with graceful degradation.

    Resolution order:
    1. ``settings.wichy_home / "mcp_servers.json"``
    2. ``WICHY_MCP_SERVERS`` environment variable (raw JSON string)
    3. Empty :class:`MCPConfig` if neither source exists

    Parse errors are logged via the user console and an empty
    :class:`MCPConfig` is returned — the function **never** crashes.
    """
    config_path = settings.wichy_home / _MCP_CONFIG_FILE
    raw: str | None = None
    source_desc: str = ""

    # --- Try JSON file first ---
    if config_path.is_file():
        try:
            raw = config_path.read_text(encoding="utf-8")
            source_desc = f"file {config_path}"
        except OSError as exc:
            user_console.print(
                f"[red]MCP config error: could not read {config_path}: {exc}[/red]"
            )
            return MCPConfig()

    # --- Fall back to env var ---
    if raw is None:
        env_val = os.environ.get(_MCP_ENV_VAR)
        if env_val is not None:
            raw = env_val
            source_desc = f"env var {_MCP_ENV_VAR}"

    # --- Neither source available ---
    if raw is None:
        return MCPConfig()

    # --- Parse JSON ---
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        user_console.print(
            f"[red]MCP config error: invalid JSON in {source_desc}: {exc}[/red]"
        )
        return MCPConfig()

    # --- Validate into MCPConfig ---
    try:
        return MCPConfig.model_validate(data)
    except Exception as exc:
        user_console.print(
            f"[red]MCP config error: validation failed for {source_desc}: {exc}[/red]"
        )
        return MCPConfig()
