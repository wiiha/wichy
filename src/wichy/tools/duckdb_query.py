"""DuckDB SQL query execution tool for data analysis."""

from typing import Any, Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.duckdb_manager import DuckDBManager


class DuckDBQueryParameters(ParametersModel):
    query: str = Field(
        ...,
        description="SQL query to execute (SELECT, SHOW, DESCRIBE, etc.)",
    )
    limit: Optional[int] = Field(
        100,
        description="Maximum number of rows to return. Use to avoid context overflow. Default 100.",
    )
    sample: Optional[bool] = Field(
        False,
        description="If True and result exceeds limit, return random sample instead of first rows.",
    )

    def info(self) -> str:
        query_preview = self.query[:50] + "..." if len(self.query) > 50 else self.query
        return f'query="{query_preview}" limit={self.limit}'


class DuckDBQueryTool(BaseTool):
    name = "duckdb_query"
    description = "Execute SQL queries on loaded data tables for analysis"
    description_long = """
- Execute SQL queries on data tables previously loaded with duckdb_load tool
- DuckDB provides fast, efficient columnar query processing
- Returns structured results suitable for LLM context (limited rows by default)
- Use for data analysis, filtering, aggregation, joins, etc.
- Supports standard SQL syntax (SELECT, WHERE, GROUP BY, JOIN, etc.)
- Automatically handles large datasets by returning limited results
- **Can also create derived tables/views**: CREATE TABLE new AS SELECT..., CREATE VIEW summary AS SELECT...
- Tables persist within the session for subsequent queries
- Example: SELECT * FROM my_table WHERE column > 100 LIMIT 10
- Use duckdb_schema tool to see available tables and columns
- Use duckdb_status tool to see current session state
"""
    enable_result_offload = True
    parameters_model = DuckDBQueryParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute SQL query and return results."""
        query: str = kwargs["query"]
        limit: int = kwargs.get("limit", 100)
        sample: bool = kwargs.get("sample", False)
        manager = DuckDBManager.get_instance()
        return manager.execute_query(query, limit=limit, sample=sample)
