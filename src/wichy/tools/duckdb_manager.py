"""DuckDB connection manager - singleton for session persistence."""

import os
from pathlib import Path
from typing import Dict, List, Optional

import duckdb

from wichy.tools.errors import format_error


class DuckDBManager:
    """Singleton manager for DuckDB connections within a session."""

    _instance: Optional["DuckDBManager"] = None
    _connection: Optional[duckdb.DuckDBPyConnection] = None
    _loaded_tables: Dict[str, str] = {}  # table_name -> source_path
    _db_path: Optional[str] = None  # Path if persisted to disk

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "DuckDBManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset the singleton - close connection and clear state."""
        if cls._connection is not None:
            cls._connection.close()
            cls._connection = None
        cls._loaded_tables = {}
        cls._db_path = None
        cls._instance = None

    def get_connection(self) -> duckdb.DuckDBPyConnection:
        """Get or create the DuckDB connection."""
        if self._connection is None:
            if self._db_path:
                # Use persisted database
                self._connection = duckdb.connect(self._db_path)
            else:
                # In-memory database
                self._connection = duckdb.connect(":memory:")
        return self._connection

    def load_data(
        self,
        data_path: str,
        table_name: Optional[str] = None,
        overwrite: bool = False,
    ) -> str:
        """
        Load data file into a named table.

        Args:
            data_path: Path to CSV, Parquet, or JSON file
            table_name: Name for the table (defaults to filename without extension)
            overwrite: Whether to overwrite existing table

        Returns:
            Success message or error
        """
        try:
            # Validate file exists
            if not os.path.exists(data_path):
                return format_error(f"File not found: {data_path}")

            # Generate table name if not provided
            if table_name is None:
                table_name = Path(data_path).stem

            # Sanitize table name (replace spaces, special chars)
            table_name = "".join(
                c if c.isalnum() or c == "_" else "_" for c in table_name
            )

            conn = self.get_connection()

            # Check if table exists
            existing_tables = self.list_tables()
            if table_name in existing_tables and not overwrite:
                return format_error(
                    f"Table '{table_name}' already exists. Use overwrite=True to replace it."
                )

            # Determine file type and load
            if data_path.endswith(".csv"):
                conn.execute(
                    f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto('{data_path}')"
                )
            elif data_path.endswith(".parquet"):
                conn.execute(
                    f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet('{data_path}')"
                )
            elif data_path.endswith(".json") or data_path.endswith(".jsonl"):
                conn.execute(
                    f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_json_auto('{data_path}')"
                )
            else:
                return format_error(
                    "Unsupported file format. Use .csv, .parquet, or .json"
                )

            self._loaded_tables[table_name] = data_path
            return f"Loaded {data_path} into table '{table_name}'"

        except Exception as e:
            return format_error(f"Failed to load data: {e}")

    def list_tables(self) -> List[str]:
        """List all loaded tables."""
        conn = self.get_connection()
        result = conn.execute("SHOW TABLES").fetchall()
        return [row[0] for row in result]

    def get_schema(self, table_name: Optional[str] = None) -> str:
        """
        Get schema information for tables.

        Args:
            table_name: Specific table, or None for all tables

        Returns:
            Schema description
        """
        try:
            conn = self.get_connection()
            tables = self.list_tables()

            if not tables:
                return "No tables loaded. Use duckdb_load to load data first."

            if table_name:
                if table_name not in tables:
                    return format_error(
                        f"Table '{table_name}' not found. Available tables: {', '.join(tables)}"
                    )
                tables = [table_name]

            schema_info = []
            for tbl in tables:
                result = conn.execute(f"DESCRIBE {tbl}").fetchall()
                schema_info.append(f"\n## Table: {tbl}")
                schema_info.append("| Column | Type |")
                schema_info.append("|--------|------|")
                for row in result:
                    schema_info.append(f"| {row[0]} | {row[1]} |")

                # Add row count
                count = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                schema_info.append(f"\nRows: {count}")

                # Add source path if known
                if tbl in self._loaded_tables:
                    schema_info.append(f"Source: {self._loaded_tables[tbl]}")

            return "\n".join(schema_info)

        except Exception as e:
            return format_error(f"Failed to get schema: {e}")

    def execute_query(self, query: str, limit: int = 100, sample: bool = False) -> str:
        """
        Execute a SQL query and return formatted results.

        Args:
            query: SQL query string
            limit: Maximum rows to return
            sample: If True, sample random rows instead of first rows

        Returns:
            Formatted query results
        """
        try:
            conn = self.get_connection()
            result = conn.execute(query)

            # Get column names
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()

            # Check if we need to limit results
            total_rows = len(rows)
            if total_rows == 0:
                return "Query returned 0 rows."

            # Apply limit
            if total_rows > limit:
                if sample:
                    import random

                    rows = random.sample(rows, limit)
                else:
                    rows = rows[:limit]

                result_str = f"Query returned {total_rows} rows, showing {limit} (limit reached):\n\n"
            else:
                result_str = f"Query returned {total_rows} rows:\n\n"

            # Format results as markdown table
            result_str += "| " + " | ".join(columns) + " |\n"
            result_str += "| " + " | ".join(["---"] * len(columns)) + " |\n"

            for row in rows:
                formatted_row = []
                for val in row:
                    if val is None:
                        formatted_row.append("")
                    elif isinstance(val, (int, float)):
                        formatted_row.append(str(val))
                    else:
                        formatted_row.append(str(val)[:100])  # Truncate long strings
                result_str += "| " + " | ".join(formatted_row) + " |\n"

            return result_str.strip()

        except Exception as e:
            return format_error(f"Query failed: {e}")

    def persist(self, db_path: str) -> str:
        """
        Persist the in-memory database to disk.

        Args:
            db_path: Path to save the database file

        Returns:
            Success message or error
        """
        try:
            conn = self.get_connection()

            # Get all tables
            tables = self.list_tables()
            if not tables:
                return format_error("No tables to persist")

            # Use DuckDB's ATTACH and COPY to persist

            # Create the directory if needed
            import os

            os.makedirs(
                os.path.dirname(db_path) if os.path.dirname(db_path) else ".",
                exist_ok=True,
            )

            # Attach target database and copy tables
            conn.execute(f"ATTACH '{db_path}' AS target_db")
            for table_name in tables:
                conn.execute(
                    f"CREATE OR REPLACE TABLE target_db.{table_name} AS SELECT * FROM {table_name}"
                )
            conn.execute("DETACH target_db")

            self._db_path = db_path
            return f"Database persisted to {db_path} with {len(tables)} table(s)"
        except Exception as e:
            return format_error(f"Failed to persist database: {e}")

    def load_database(self, db_path: str) -> str:
        """
        Load a persisted database from disk.

        Args:
            db_path: Path to the database file

        Returns:
            Success message or error
        """
        try:
            if not os.path.exists(db_path):
                return format_error(f"Database file not found: {db_path}")

            # Close current connection
            if self._connection is not None:
                self._connection.close()
                self._connection = None

            self._db_path = db_path
            self.get_connection()

            # Update loaded_tables with existing tables
            tables = self.list_tables()
            for tbl in tables:
                self._loaded_tables[tbl] = f"<loaded from {db_path}>"

            return f"Loaded database from {db_path}. Tables: {', '.join(tables)}"

        except Exception as e:
            return format_error(f"Failed to load database: {e}")

    def get_status(self) -> str:
        """Get current status of the DuckDB session."""
        tables = self.list_tables()
        status_parts = [
            f"Database: {self._db_path if self._db_path else 'in-memory'}",
            f"Tables loaded: {len(tables)}",
        ]

        if tables:
            status_parts.append("\n### Loaded Tables:")
            for tbl in tables:
                source = self._loaded_tables.get(tbl, "<unknown>")
                status_parts.append(f"  - {tbl} (source: {source})")

        return "\n".join(status_parts)
