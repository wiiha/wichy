"""
SQLite-backed Result Store for offloaded tool results.

This module provides thread-safe storage for large tool results that need
to be offloaded from the context window. Uses SQLite with WAL mode for
concurrent read access and serialized writes.

Usage:
    from wichy.result_offload.store import get_result_store

    store = get_result_store()
    ref_id = store.save(content="...", tool_name="read_file", input_args={...})
    result = store.load(ref_id)
"""

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class StoredResult:
    """A stored tool result with metadata."""

    ref_id: str
    content: str
    tool_name: str
    input_args: Dict[str, Any]
    char_count: int
    created_at: datetime
    expires_at: datetime
    model_str: Optional[str] = None  # Which model triggered this (for debugging)


# Module-level singleton
_instance: Optional["ResultStore"] = None
_lock = threading.Lock()


def get_result_store() -> "ResultStore":
    """Get the singleton ResultStore instance."""
    global _instance
    with _lock:
        if _instance is None:
            _instance = ResultStore()
    return _instance


class ResultStore:
    """
    Thread-safe SQLite-backed store for offloaded tool results.

    All agents share the same store instance, allowing ref IDs to be
    passed between agents (e.g., from task agent to root agent).

    Storage location: .wichy/results.db

    Thread safety: Uses connection-per-operation pattern with WAL mode
    for concurrent reads. A write lock ensures serialized writes.
    """

    # Class-level lock for write operations
    _write_lock = threading.Lock()

    def __init__(self, wichy_dir: Optional[Path] = None):
        """Initialize the SQLite store."""
        if wichy_dir is None:
            wichy_dir = Path.cwd() / ".wichy"

        # Ensure directory exists before creating database
        wichy_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = wichy_dir / "results.db"
        self._init_db()

    @contextmanager
    def _get_conn(self, for_write: bool = False):
        """
        Create a new database connection with WAL mode.

        Uses connection-per-operation pattern (not connection pooling).
        Each call creates a fresh connection that's closed after use.
        WAL mode allows concurrent reads while writes are serialized.

        Args:
            for_write: If True, acquire the write lock before connecting.
                       This ensures only one writer at a time.

        Yields:
            sqlite3.Connection: A fresh database connection.
        """
        if for_write:
            # Acquire write lock to serialize writes
            self._write_lock.acquire()

        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            if conn is not None:
                conn.close()
            if for_write:
                self._write_lock.release()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn: sqlite3.Connection
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS results (
                    ref_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    tool_name TEXT,
                    input_args TEXT,
                    char_count INTEGER,
                    created_at TEXT,
                    expires_at TEXT,
                    model_str TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires_at ON results(expires_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_name ON results(tool_name)"
            )
            conn.commit()

    def _generate_ref_id(self) -> str:
        """Generate a unique reference ID."""
        return f"res_{uuid4().hex[:12]}"

    def _get_ttl_hours(self) -> int:
        """Get TTL from settings."""
        from wichy.config import settings

        return settings.result_offload_ttl_hours

    # -------------------------------------------------------------------------
    # Core Operations
    # -------------------------------------------------------------------------

    def save(
        self,
        content: str,
        tool_name: str,
        input_args: Dict[str, Any],
        model_str: Optional[str] = None,
    ) -> str:
        """
        Store a result and return its reference ID.

        Args:
            content: The tool result content
            tool_name: Name of the tool that produced this result
            input_args: The input arguments to the tool
            model_str: Optional model string (for debugging)

        Returns:
            ref_id: Unique reference ID for later retrieval
        """
        import json

        ref_id = self._generate_ref_id()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=self._get_ttl_hours())

        conn: sqlite3.Connection
        with self._get_conn(for_write=True) as conn:
            conn.execute(
                """
                INSERT INTO results (ref_id, content, tool_name, input_args,
                                     char_count, created_at, expires_at, model_str)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ref_id,
                    content,
                    tool_name,
                    json.dumps(input_args),
                    len(content),
                    now.isoformat(),
                    expires_at.isoformat(),
                    model_str,
                ),
            )
            conn.commit()

        return ref_id

    def load(self, ref_id: str) -> Optional[StoredResult]:
        """
        Load a stored result by reference ID.

        Args:
            ref_id: The reference ID

        Returns:
            StoredResult if found and not expired, None otherwise
        """
        import json

        conn: sqlite3.Connection
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM results WHERE ref_id = ?",
                (ref_id,),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Check expiration
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) > expires_at:
                self.delete(ref_id)
                return None

            return StoredResult(
                ref_id=row["ref_id"],
                content=row["content"],
                tool_name=row["tool_name"],
                input_args=json.loads(row["input_args"]),
                char_count=row["char_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=expires_at,
                model_str=row["model_str"],
            )

    def delete(self, ref_id: str) -> bool:
        """
        Delete a stored result.

        Args:
            ref_id: The reference ID

        Returns:
            True if deleted, False if not found
        """
        conn: sqlite3.Connection
        with self._get_conn(for_write=True) as conn:
            cursor = conn.execute(
                "DELETE FROM results WHERE ref_id = ?",
                (ref_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_expired(self) -> int:
        """
        Remove all expired results.

        Returns:
            Number of expired results removed
        """
        conn: sqlite3.Connection
        with self._get_conn(for_write=True) as conn:
            cursor = conn.execute(
                "DELETE FROM results WHERE expires_at < ?",
                (datetime.now(timezone.utc).isoformat(),),
            )
            conn.commit()
            return cursor.rowcount

    def list_refs(self) -> List[Dict[str, Any]]:
        """
        List all stored result references (without content).

        Returns:
            List of dicts with ref_id, tool_name, char_count, created_at, expires_at
        """
        conn: sqlite3.Connection
        with self._get_conn() as conn:
            cursor = conn.execute("""
                SELECT ref_id, tool_name, char_count, created_at, expires_at
                FROM results
                ORDER BY created_at DESC
                """)
            rows = cursor.fetchall()

            return [
                {
                    "ref_id": row["ref_id"],
                    "tool_name": row["tool_name"],
                    "char_count": row["char_count"],
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                }
                for row in rows
            ]
