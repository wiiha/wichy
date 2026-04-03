"""DuckDB schema inspection tool."""

from typing import Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.duckdb_manager import DuckDBManager


class DuckDBSchemaParameters(ParametersModel):
    table_name: Optional[str] = Field(
        None,
        description="Specific table to inspect, or None to show all tables",
    )

    def info(self) -> str:
        if self.table_name:
            return f'table="{self.table_name}"'
        return "all tables"


class DuckDBSchemaTool(BaseTool):
    name = "duckdb_schema"
    description = "Inspect schema of loaded DuckDB tables (columns, types, row counts)"
    description_long = """
- Shows table structure: column names, data types, and row counts
- Use without table_name to see all loaded tables
- Use with table_name to get detailed schema for specific table
- Shows source file path for each loaded table
- Essential first step before writing queries
- Helps understand data structure and plan analysis
- **Note**: Tables created via CREATE TABLE/CREATE VIEW (from duckdb_query) will also appear here
- Run duckdb_status to see overall session state
"""
    enable_result_offload = True
    parameters_model = DuckDBSchemaParameters

    def execute(self, table_name: Optional[str] = None) -> str:
        """Get schema information for tables."""
        manager = DuckDBManager.get_instance()
        return manager.get_schema(table_name=table_name)
