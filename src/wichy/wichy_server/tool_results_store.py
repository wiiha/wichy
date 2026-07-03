"""SQLite-backed store for manually executed tool results.

The external server API can execute tools on behalf of a caller.
Execution records are stored here so that:

- ``POST /server/api/tools/execute`` can return a stable reference instead of
  only the raw result.
- ``POST /server/api/tools/inject`` can look up a previous execution by that
  reference and inject the *stored* arguments and result into the root agent
  context. This prevents callers from spoofing tool calls or results.
- ``GET /server/api/tools/results`` can list previous manual executions.

The database lives in ``.wichy/tool_results.db`` under the current project.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from wichy.config import settings


@dataclass
class ToolResultRecord:
    """A single stored manual tool execution."""

    id: str
    tool_name: str
    arguments: dict[str, Any]
    result: str
    verified: bool
    created_at: str


class ToolResultsStore:
    """Store and retrieve manual tool execution results."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = settings.contexts_dir.parent / "tool_results.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_results (
                    id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def add(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: str,
        verified: bool = False,
    ) -> str:
        """Store a manual tool execution and return its reference id."""
        record_id = uuid.uuid4().hex
        created_at = datetime.now().isoformat()
        with self._lock, self._connection() as conn:
            conn.execute(
                """
                INSERT INTO tool_results (id, tool_name, arguments, result, verified, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    tool_name,
                    json.dumps(arguments),
                    result,
                    1 if verified else 0,
                    created_at,
                ),
            )
            conn.commit()
        return record_id

    def get(self, record_id: str) -> Optional[ToolResultRecord]:
        """Return a stored execution record by id, or None if not found."""
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM tool_results WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            return None
        return ToolResultRecord(
            id=row["id"],
            tool_name=row["tool_name"],
            arguments=json.loads(row["arguments"]),
            result=row["result"],
            verified=bool(row["verified"]),
            created_at=row["created_at"],
        )

    def list_all(self) -> list[ToolResultRecord]:
        """Return all stored execution records, newest first."""
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_results ORDER BY created_at DESC"
            ).fetchall()
        return [
            ToolResultRecord(
                id=row["id"],
                tool_name=row["tool_name"],
                arguments=json.loads(row["arguments"]),
                result=row["result"],
                verified=bool(row["verified"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete(self, record_id: str) -> bool:
        """Delete a record. Returns True if a row was removed."""
        with self._lock, self._connection() as conn:
            cursor = conn.execute("DELETE FROM tool_results WHERE id = ?", (record_id,))
            conn.commit()
        return cursor.rowcount > 0


# Module-level singleton. Reused across requests in the same process.
_store: Optional[ToolResultsStore] = None
_store_lock = threading.Lock()


def get_tool_results_store() -> ToolResultsStore:
    """Return the process-wide ToolResultsStore, creating it if necessary."""
    global _store
    with _store_lock:
        if _store is None:
            _store = ToolResultsStore()
        return _store
