"""DuckDB session reset tool."""

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.duckdb_manager import DuckDBManager


class DuckDBResetParameters(ParametersModel):
    """No parameters needed for reset."""

    pass


class DuckDBResetTool(BaseTool):
    name = "duckdb_reset"
    description = "Reset DuckDB session - clear all tables and close connection"
    description_long = """
- Clears all loaded tables from memory
- Closes the current database connection
- Resets to a fresh in-memory session
- Use when starting a new analysis from scratch
- All unsaved data will be lost (use duckdb_persist first if needed)
"""
    parameters_model = DuckDBResetParameters

    def execute(self) -> str:
        """Reset the DuckDB session."""
        DuckDBManager.reset()
        return "DuckDB session reset. All tables cleared."
