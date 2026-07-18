"""DuckDB connection manager - singleton for session persistence."""

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Dict, List, Optional

import duckdb

from wichy.tools.errors import format_error


class PoolExhaustedError(Exception):
    """Raised when connection pool has no available connections."""

    pass


class ConnectionPool:
    """
    Thread-safe DuckDB connection pool.

    For in-memory databases: Uses cursors derived from a primary connection.
    For file-based databases: Creates independent connections.
    """

    def __init__(
        self, db_path: Optional[str] = None, pool_size: int = 4, read_only: bool = False
    ):
        self._db_path = db_path  # None = in-memory
        self._pool_size = pool_size
        self._read_only = read_only

        # For in-memory: single primary connection
        self._primary_connection: Optional[duckdb.DuckDBPyConnection] = None

        # Pool of available connections/cursors
        self._pool: Queue[duckdb.DuckDBPyConnection] = Queue(maxsize=pool_size)

        # Track active connections (for cleanup)
        self._active: Dict[int, duckdb.DuckDBPyConnection] = {}
        self._active_lock = threading.Lock()

        # Initialize pool
        self._initialize_pool()

    def _initialize_pool(self):
        """Create initial connections for the pool."""
        if self._db_path is None:
            # In-memory: create primary connection, then cursors
            self._primary_connection = duckdb.connect(":memory:")
            for _ in range(self._pool_size):
                cursor = self._primary_connection.cursor()
                self._pool.put(cursor)
        else:
            # File-based: each thread gets own connection
            for _ in range(self._pool_size):
                conn = duckdb.connect(self._db_path, read_only=self._read_only)
                self._pool.put(conn)

    @contextmanager
    def get_connection(self, timeout: float = 5.0):
        """
        Get a connection from the pool (context manager).

        Usage:
            with pool.get_connection() as conn:
                result = conn.execute("SELECT 1").fetchall()
        """
        conn = self._acquire(timeout)
        try:
            yield conn
        finally:
            self._release(conn)

    def _acquire(self, timeout: float) -> duckdb.DuckDBPyConnection:
        """Acquire a connection from the pool."""
        try:
            conn = self._pool.get(timeout=timeout)
            with self._active_lock:
                self._active[id(conn)] = conn
            return conn
        except Empty:
            raise PoolExhaustedError(
                f"No connections available after {timeout}s. Pool size: {self._pool_size}"
            )

    def _release(self, conn: duckdb.DuckDBPyConnection):
        """Return connection to pool."""
        with self._active_lock:
            self._active.pop(id(conn), None)
        try:
            self._pool.put_nowait(conn)
        except Full:
            conn.close()

    def close(self):
        """Close all connections."""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Empty:
                break

        with self._active_lock:
            for conn in self._active.values():
                conn.close()
            self._active.clear()

        if self._primary_connection:
            self._primary_connection.close()
            self._primary_connection = None


class DuckDBManager:
    """Singleton manager for DuckDB connections within a session."""

    _instance: Optional["DuckDBManager"] = None
    _loaded_tables: Dict[str, str] = {}  # table_name -> source_path
    _db_path: Optional[str] = None  # Path if persisted to disk
    _pool: Optional[ConnectionPool] = None
    _metadata_lock = threading.RLock()  # For _loaded_tables
    DEFAULT_POOL_SIZE = 4

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "DuckDBManager":
        """Get the singleton instance."""
        if cls._instance is None:
            with cls._metadata_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls):
        """Reset the singleton - close pool and clear state."""
        with cls._metadata_lock:
            if cls._pool is not None:
                cls._pool.close()
                cls._pool = None
            cls._loaded_tables = {}
            cls._db_path = None
            cls._instance = None

    @classmethod
    def _ensure_pool(cls) -> ConnectionPool:
        """Ensure pool is initialized."""
        if cls._pool is None:
            with cls._metadata_lock:
                if cls._pool is None:
                    cls._pool = ConnectionPool(
                        db_path=cls._db_path,
                        pool_size=cls.DEFAULT_POOL_SIZE,
                        read_only=False,
                    )
        return cls._pool

    @classmethod
    @contextmanager
    def get_connection(cls):
        """Get a connection from the pool."""
        pool = cls._ensure_pool()
        with pool.get_connection() as conn:
            yield conn

    @classmethod
    def load_data(
        cls,
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

            # Hold metadata lock for the entire operation to prevent race conditions
            # when multiple threads try to load/create the same table concurrently.
            # Using RLock allows re-entry for get_connection() which may need
            # to initialize the pool (also uses this lock).
            with cls._metadata_lock:
                # Check if table exists
                existing_tables = cls.list_tables()
                if table_name in existing_tables and not overwrite:
                    return format_error(
                        f"Table '{table_name}' already exists. Use overwrite=True to replace it."
                    )

                # Determine file type and load
                with cls.get_connection() as conn:
                    if data_path.endswith(".csv"):
                        conn.execute(
                            f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto(?)",
                            [data_path],
                        )
                    elif data_path.endswith(".parquet"):
                        conn.execute(
                            f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet(?)",
                            [data_path],
                        )
                    elif data_path.endswith(".json") or data_path.endswith(".jsonl"):
                        conn.execute(
                            f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_json_auto(?)",
                            [data_path],
                        )
                    else:
                        return format_error(
                            "Unsupported file format. Use .csv, .parquet, or .json"
                        )

                cls._loaded_tables[table_name] = data_path

            return f"Loaded {data_path} into table '{table_name}'"

        except Exception as e:
            return format_error(f"Failed to load data: {e}")

    @classmethod
    def list_tables(cls) -> List[str]:
        """List all loaded tables."""
        with cls.get_connection() as conn:
            result = conn.execute("SHOW TABLES").fetchall()
        return [row[0] for row in result]

    @classmethod
    def get_schema(cls, table_name: Optional[str] = None) -> str:
        """
        Get schema information for tables.

        Args:
            table_name: Specific table, or None for all tables

        Returns:
            Schema description
        """
        try:
            tables = cls.list_tables()

            if not tables:
                return "No tables loaded. Use duckdb_load to load data first."

            if table_name:
                if table_name not in tables:
                    return format_error(
                        f"Table '{table_name}' not found. Available tables: {', '.join(tables)}"
                    )
                tables = [table_name]

            schema_info = []
            with cls.get_connection() as conn:
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
                    with cls._metadata_lock:
                        source_path = cls._loaded_tables.get(tbl)
                    if source_path:
                        schema_info.append(f"Source: {source_path}")

            return "\n".join(schema_info)

        except Exception as e:
            return format_error(f"Failed to get schema: {e}")

    @classmethod
    def execute_query(cls, query: str, limit: int = 100, sample: bool = False) -> str:
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
            with cls.get_connection() as conn:
                result = conn.execute(query)

                # DDL/DML statements (CREATE, INSERT, DROP, etc.) have no
                # result set — description is None. Report success instead of
                # crashing on NoneType iteration.
                if result.description is None:
                    return "Query executed successfully."

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
                        formatted_row.append(str(val))
                result_str += "| " + " | ".join(formatted_row) + " |\n"

            return result_str.strip()

        except Exception as e:
            return format_error(f"Query failed: {e}")

    @classmethod
    def persist(cls, db_path: str) -> str:
        """
        Persist the in-memory database to disk.

        Args:
            db_path: Path to save the database file

        Returns:
            Success message or error
        """
        try:
            # Get all tables
            tables = cls.list_tables()
            if not tables:
                return format_error("No tables to persist")

            # Create the directory if needed
            os.makedirs(
                os.path.dirname(db_path) if os.path.dirname(db_path) else ".",
                exist_ok=True,
            )

            # Attach target database and copy tables
            with cls.get_connection() as conn:
                conn.execute(f"ATTACH '{db_path}' AS target_db")
                try:
                    for table_name in tables:
                        conn.execute(
                            f"CREATE OR REPLACE TABLE target_db.{table_name} AS SELECT * FROM {table_name}"
                        )
                finally:
                    conn.execute("DETACH target_db")

            # Close existing pool and update path
            with cls._metadata_lock:
                if cls._pool is not None:
                    cls._pool.close()
                    cls._pool = None
                cls._db_path = db_path

            return f"Database persisted to {db_path} with {len(tables)} table(s)"
        except Exception as e:
            return format_error(f"Failed to persist database: {e}")

    @classmethod
    def load_database(cls, db_path: str) -> str:
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

            # Save old state for rollback on failure
            with cls._metadata_lock:
                old_db_path = cls._db_path
                old_pool = cls._pool
                cls._pool = None
                cls._db_path = db_path
                cls._loaded_tables = {}

            try:
                # Initialize new pool
                cls._ensure_pool()

                # Update loaded_tables with existing tables
                tables = cls.list_tables()
                with cls._metadata_lock:
                    for tbl in tables:
                        cls._loaded_tables[tbl] = f"<loaded from {db_path}>"

                return f"Loaded database from {db_path}. Tables: {', '.join(tables)}"
            except Exception as e:
                # Rollback on failure
                with cls._metadata_lock:
                    cls._db_path = old_db_path
                    cls._pool = old_pool
                return format_error(f"Failed to load database: {e}")

        except Exception as e:
            return format_error(f"Failed to load database: {e}")

    @classmethod
    def get_status(cls) -> str:
        """Get current status of the DuckDB session."""
        tables = cls.list_tables()
        status_parts = [
            f"Database: {cls._db_path if cls._db_path else 'in-memory'}",
            f"Tables loaded: {len(tables)}",
        ]

        if tables:
            status_parts.append("\n### Loaded Tables:")
            with cls._metadata_lock:
                for tbl in tables:
                    source = cls._loaded_tables.get(tbl, "<unknown>")
                    status_parts.append(f"  - {tbl} (source: {source})")

        return "\n".join(status_parts)
