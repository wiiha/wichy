"""DuckDB persistence tools for saving/loading databases."""

from typing import Any

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.duckdb_manager import DuckDBManager


class DuckDBPersistParameters(ParametersModel):
    db_path: str = Field(
        ...,
        description="Path to save the DuckDB database file",
    )

    def info(self) -> str:
        return f'path="{self.db_path}"'


class DuckDBPersistTool(BaseTool):
    needs_verification_in_api: bool = False
    name = "duckdb_persist"
    description = "Save current DuckDB session to a database file for persistence"
    description_long = """
- Saves the in-memory database to disk
- All loaded tables and queries are persisted
- Useful for saving analysis state between sessions
- Database can be reloaded later with duckdb_load_db
- File extension should be .duckdb or .db
- Does not close the current session
"""
    parameters_model = DuckDBPersistParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Persist database to disk."""
        db_path: str = kwargs["db_path"]
        manager = DuckDBManager.get_instance()
        return manager.persist(db_path)


class DuckDBLoadDBParameters(ParametersModel):
    db_path: str = Field(
        ...,
        description="Path to the DuckDB database file to load",
    )

    def info(self) -> str:
        return f'path="{self.db_path}"'


class DuckDBLoadDBTool(BaseTool):
    needs_verification_in_api: bool = False
    name = "duckdb_load_db"
    description = "Load a persisted DuckDB database file to restore a previous session"
    description_long = """
- Loads a previously saved DuckDB database from disk
- Replaces current in-memory session
- All tables from the saved session become available
- Use after duckdb_persist to restore analysis state
- File extension should be .duckdb or .db
"""
    parameters_model = DuckDBLoadDBParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Load database from disk."""
        db_path: str = kwargs["db_path"]
        manager = DuckDBManager.get_instance()
        return manager.load_database(db_path)
