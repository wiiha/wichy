"""DuckDB data loading tool."""

from typing import Any, Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.duckdb_manager import DuckDBManager


class DuckDBLoadParameters(ParametersModel):
    data_path: str = Field(
        ...,
        description="Path to data file (CSV, Parquet, JSON, or JSONL)",
    )
    table_name: Optional[str] = Field(
        None,
        description="Name for the table (defaults to filename without extension)",
    )
    overwrite: Optional[bool] = Field(
        False,
        description="Whether to overwrite existing table with same name",
    )

    def info(self) -> str:
        parts = [f'path="{self.data_path}"']
        if self.table_name:
            parts.append(f'table="{self.table_name}"')
        if self.overwrite:
            parts.append("overwrite=True")
        return " ".join(parts)


class DuckDBLoadTool(BaseTool):
    needs_verification_in_api: bool = False
    name = "duckdb_load"
    description = "Load data files (CSV, Parquet, JSON) into DuckDB tables for analysis"
    description_long = """
- Load CSV, Parquet, or JSON files into named DuckDB tables
- Files are loaded efficiently using DuckDB's native readers
- Memory-efficient: only reads needed columns during queries
- Table names default to filename (without extension)
- Use overwrite=True to replace existing tables
- Loaded tables persist within the session for multiple queries
- Use duckdb_schema to inspect loaded data structure
- Use duckdb_query to run SQL queries on loaded tables
- Example: Load "sales_data.csv" creates table "sales_data"
"""
    parameters_model = DuckDBLoadParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Load data file into DuckDB table."""
        data_path: str = kwargs["data_path"]
        table_name: Optional[str] = kwargs.get("table_name")
        overwrite: bool = kwargs.get("overwrite", False)
        manager = DuckDBManager.get_instance()
        return manager.load_data(data_path, table_name=table_name, overwrite=overwrite)
