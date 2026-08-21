"""
Centralized configuration for wichy.

Uses pydantic-settings to load from environment variables with sane defaults.
Environment variables can be prefixed with WICHY_ (e.g., WICHY_OLLAMA_BASE_URL).

Also loads optional YAML settings files from ``~/.wichy/settings.yaml`` and
``./.wichy/settings.yaml``. Project-local values override user-home values.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from wichy.config._yaml_settings import SettingsNamespace, load_yaml_settings


class Settings(BaseSettings):
    """Centralized configuration for wichy."""

    if TYPE_CHECKING:
        # Type-only declaration so mypy knows the attribute type without creating a Pydantic field.
        _yaml_namespaces: dict[str, SettingsNamespace]

    model_config = SettingsConfigDict(
        env_prefix="WICHY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Allow reading env vars without prefix for API keys
        env_nested_delimiter="__",
    )

    # Paths - Home directory
    wichy_home: Path = Path.home() / ".wichy"

    # Paths - Project-local
    contexts_dir: Path = Path(".wichy/contexts")
    graphs_dir_name: str = "graphs"
    notes_dir_name: str = "notes"
    logs_dir_name: str = "logs"
    charts_dir_name: str = "charts"
    root_agent_defs_dir: str = "root_agent_defs"
    sub_agent_defs_dir: str = "sub_agents"

    # History file
    history_file: Path = Path.home() / ".wichy_history"

    # LLM Backend URLs
    ollama_base_url: str = "http://localhost:11434/v1"
    llama_cpp_base_url: str = "http://localhost:8080"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Show indication for content being reasoning extract
    show_reasoning_extract_prefix_and_suffix: bool = False

    # API Keys (loaded from environment with legacy names)
    # pydantic-settings will look for OPENAI_API_KEY and OPEN_ROUTER_API_KEY
    openai_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None

    # Browser settings
    browser_headless: bool = True
    browser_viewport_width: int = 1920
    browser_viewport_height: int = 1080
    browser_user_agents: list[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    ]
    browser_locale: str = "en-US"

    # Server settings
    server_host: str = "127.0.0.1"
    server_port: int = 7891

    # Pipeline / REPL settings
    wake_up_message: str = (
        "You just woke up. Perform any tasks you deem necessary before interacting further with the user."
    )

    # Skills settings

    # User attention script
    needs_user_attention_script: Optional[str] = None

    # Environment
    container: bool = False  # Set to true when running inside the Docker container

    # Tool execution
    parallel_exec: bool = True
    skip_human_verification: bool = False
    max_backend_connections: Optional[int] = None  # None = no limit

    ## query result tool
    query_result_tool_model_str: Optional[str] = (
        None  # None = use same model_str as root agent
    )

    ## task tool
    task_tool_model_str: Optional[str] = (
        None  # None = use same model_str as root agent or per task agent definition
    )

    skills_dir_name: str = "skills"

    @property
    def skills_dir_local(self) -> Path:
        """Project-local skills directory."""
        return Path(".wichy") / self.skills_dir_name

    @property
    def skills_dir_home(self) -> Path:
        """User-home skills directory."""
        return self.wichy_home / self.skills_dir_name

    # -------------------------------------------------------------------------
    # Result Offload Configuration
    # -------------------------------------------------------------------------

    # Character threshold above which results may be offloaded
    # Default: 8000 chars
    result_offload_threshold: int = 8000

    # Tolerance percentage for pass-through
    # If result size <= threshold * (1 + tolerance), pass through normally
    # Default: 0.10 (10%) - so 8000 * 1.10 = 8800 chars
    result_offload_tolerance: float = 0.10

    # Preview character count for offloaded results
    # Included in the reference response so agents can see beginning
    # Default: 500 chars (max 1000)
    result_offload_preview_chars: int = 500

    # Time-to-live for stored results (hours)
    # Default: 24 hours
    result_offload_ttl_hours: int = 24

    # Maximum validation retries for summarizer
    # Default: 2
    result_offload_max_validation_retries: int = 2

    # -------------------------------------------------------------------------
    # Loop Detection Configuration
    # -------------------------------------------------------------------------

    # Enable/disable loop detection for agent tool calls
    loop_detection_enabled: bool = True

    # Number of recent tool-call signatures to keep in the rolling window
    loop_detection_window: int = 10

    # Max number of times a signature may repeat before triggering
    # (count > threshold triggers, so 5 means the 6th repeat triggers)
    loop_detection_threshold: int = 5

    # -------------------------------------------------------------------------
    # MCP Configuration
    # -------------------------------------------------------------------------

    mcp_enabled: bool = True
    mcp_connection_timeout: int = 10  # seconds
    mcp_tool_timeout: int = 60  # seconds

    @property
    def root_agent_defs_home_dir(self) -> Path:
        """Full path to home root agent defs directory."""
        return self.wichy_home / self.root_agent_defs_dir

    @property
    def root_agent_defs_local_dir(self) -> Path:
        """Path to local project root agent defs directory."""
        return Path(".wichy") / self.root_agent_defs_dir

    @property
    def sub_agent_defs_home_dir(self) -> Path:
        """Full path to home sub agent defs directory."""
        return self.wichy_home / self.sub_agent_defs_dir

    @property
    def sub_agent_defs_local_dir(self) -> Path:
        """Path to local project sub agent defs directory."""
        return Path(".wichy") / self.sub_agent_defs_dir

    @property
    def graphs_dir(self) -> Path:
        """Full path to graphs directory (relative to workspace)."""
        return Path(".wichy") / self.graphs_dir_name

    @property
    def logs_dir(self) -> Path:
        """Full path to logs directory (relative to workspace)."""
        return Path(".wichy") / self.logs_dir_name

    @property
    def charts_dir(self) -> Path:
        """Full path to charts directory (relative to workspace)."""
        return Path(".wichy") / self.charts_dir_name

    @property
    def notes_dir(self) -> Path:
        """Full path to notes directory (relative to workspace)."""
        return Path(".wichy") / self.notes_dir_name

    @property
    def scratchpad_marker_path(self) -> Path:
        """Full path to scratchpad marker file."""
        return self.notes_dir / ".scratchpad"

    @property
    def browser_viewport(self) -> dict:
        """Viewport configuration for browser."""
        return {
            "width": self.browser_viewport_width,
            "height": self.browser_viewport_height,
        }

    def __init__(self, **kwargs):
        """Initialize settings, checking for legacy env var names."""
        # Check for legacy env var names (without prefix)
        import os

        if "openai_api_key" not in kwargs and os.environ.get("OPENAI_API_KEY"):
            kwargs["openai_api_key"] = os.environ.get("OPENAI_API_KEY")
        if "openrouter_api_key" not in kwargs and os.environ.get("OPEN_ROUTER_API_KEY"):
            kwargs["openrouter_api_key"] = os.environ.get("OPEN_ROUTER_API_KEY")
        super().__init__(**kwargs)
        # Load optional YAML settings after pydantic-settings has resolved env/defaults.
        self._yaml_namespaces = load_yaml_settings(
            self.wichy_home / "settings.yaml",
            Path(".wichy/settings.yaml"),
        )

    def __getattr__(self, name: str) -> Any:
        """Resolve unknown attribute names as YAML namespaces."""
        try:
            namespaces = self._yaml_namespaces
        except AttributeError:
            # _yaml_namespaces may not be set yet during pydantic init
            raise AttributeError(name) from None
        try:
            return namespaces[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get_namespace(self, name: str) -> SettingsNamespace:
        """Return a YAML settings namespace by name.

        Raises ``AttributeError`` if the namespace is not defined.
        """
        if name not in self._yaml_namespaces:
            raise AttributeError(name)
        return self._yaml_namespaces[name]


# Global singleton instance
settings = Settings()
