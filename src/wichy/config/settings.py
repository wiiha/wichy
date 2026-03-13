"""
Centralized configuration for wichy.

Uses pydantic-settings to load from environment variables with sane defaults.
Environment variables can be prefixed with WICHY_ (e.g., WICHY_OLLAMA_BASE_URL).
"""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized configuration for wichy."""

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
    logs_dir_name: str = "logs"
    root_agent_defs_dir: str = "root_agent_defs"

    # History file
    history_file: Path = Path.home() / ".wichy_history"

    # LLM Backend URLs
    ollama_base_url: str = "http://localhost:11434/v1"
    llama_cpp_base_url: str = "http://localhost:8080"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

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
    server_port: int = 7891

    # Skills settings
    skills_dir_name: str = "skills"

    @property
    def skills_dir(self) -> Path:
        """Full path to skills directory."""
        return self.wichy_home / self.skills_dir_name

    @property
    def root_agent_defs_home_dir(self) -> Path:
        """Full path to home root agent defs directory."""
        return self.wichy_home / self.root_agent_defs_dir

    @property
    def root_agent_defs_local_dir(self) -> Path:
        """Path to local project root agent defs directory."""
        return Path(".wichy") / self.root_agent_defs_dir

    @property
    def graphs_dir(self) -> Path:
        """Full path to graphs directory (relative to workspace)."""
        return Path(".wichy") / self.graphs_dir_name

    @property
    def logs_dir(self) -> Path:
        """Full path to logs directory (relative to workspace)."""
        return Path(".wichy") / self.logs_dir_name

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


# Global singleton instance
settings = Settings()