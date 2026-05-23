"""DuckDB session status tool."""

from typing import Any

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.duckdb_manager import DuckDBManager


class DuckDBStatusParameters(ParametersModel):
    """No parameters needed for status check."""

    pass


class DuckDBStatusTool(BaseTool):
    name = "duckdb_status"
    description = (
        "Check current DuckDB session status (loaded tables, database location)"
    )
    description_long = """
- Shows current state of DuckDB session
- Lists all loaded tables and their source files
- Shows whether using in-memory or persisted database
- Useful for debugging and understanding session state
- Run before complex analysis to verify data is loaded
"""
    parameters_model = DuckDBStatusParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Get DuckDB session status."""
        manager = DuckDBManager.get_instance()
        return manager.get_status()
