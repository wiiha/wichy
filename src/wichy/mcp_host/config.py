"""MCP server configuration loading and models.

Provides Pydantic models for MCP server configuration and a loader that
reads from a JSON file or environment variable with graceful degradation.

A project-local ``.wichy/mcp_servers.json`` overlays the global config.
The merge is shallow at the ``mcpServers`` level: local server entries
replace global entries with the same key entirely (wholesale replacement).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
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


def _try_read_file(path: Path, source_desc: str) -> str | None:
    """Read a file as UTF-8 text. Logs and returns *None* on *OSError*."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        user_console.print(
            f"[red]MCP config error: could not read {source_desc}: {exc}[/red]"
        )
        return None


def _try_parse(raw: str, source_desc: str) -> MCPConfig | None:
    """Parse raw JSON string into *MCPConfig*. Logs and returns *None* on failure."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        user_console.print(
            f"[red]MCP config error: invalid JSON in {source_desc}: {exc}[/red]"
        )
        return None

    try:
        return MCPConfig.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        user_console.print(
            f"[red]MCP config error: validation failed for {source_desc}: {exc}[/red]"
        )
        return None


def load_mcp_config() -> MCPConfig:
    """Load MCP server configuration with graceful degradation.

    Resolution order:
    1. Global: ``settings.wichy_home / "mcp_servers.json"``
    2. Global fallback: ``WICHY_MCP_SERVERS`` environment variable (raw JSON)
    3. Local overlay: ``./.wichy/mcp_servers.json`` (resolved from *cwd* at call time)

    If both global and local define the same server key, the **local entry wins
    entirely** (wholesale replacement — no field-level merging).

    Parse errors are logged via the user console and the offending source is
    skipped — the function **never** crashes.
    """
    # --- Resolve global base ------------------------------------------------
    global_cfg = MCPConfig()
    raw_global: str | None = None
    global_source_desc: str = ""

    # Try global file first
    config_path = settings.wichy_home / _MCP_CONFIG_FILE
    if config_path.is_file():
        raw_global = _try_read_file(config_path, f"file {config_path}")
        if raw_global is not None:
            global_source_desc = f"file {config_path}"

    # Fallback to env var only when global file was absent or unreadable
    if raw_global is None:
        env_val = os.environ.get(_MCP_ENV_VAR)
        if env_val is not None:
            raw_global = env_val
            global_source_desc = f"env var {_MCP_ENV_VAR}"

    if raw_global is not None:
        parsed = _try_parse(raw_global, global_source_desc)
        if parsed is not None:
            global_cfg = parsed

    # --- Resolve local overlay ----------------------------------------------
    local_cfg = MCPConfig()
    local_path = Path(".wichy") / _MCP_CONFIG_FILE
    if local_path.is_file():
        raw_local = _try_read_file(local_path, f"file {local_path}")
        if raw_local is not None:
            parsed = _try_parse(raw_local, f"file {local_path}")
            if parsed is not None:
                local_cfg = parsed

    # --- Merge (shallow at mcpServers level) --------------------------------
    return MCPConfig(mcpServers={**global_cfg.mcpServers, **local_cfg.mcpServers})
