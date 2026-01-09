import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from .artifact import Artifact

T = TypeVar("T", bound=BaseModel)

ARTIFACT_STORE_DIR = ".wichy/artifacts/"
ARTIFACT_STORE_DB_NAME = "artifacts.db"


class StoreBackendSQLite:
    """SQLite store for managing artifacts with CRUD operations."""

    def __init__(self):
        """
        Initialize the artifacts store.
        """
        self.artifacts_dir = ARTIFACT_STORE_DIR
        self.db_name = ARTIFACT_STORE_DB_NAME
        self.connection_string = self.artifacts_dir + self.db_name
        self.conn = None
        self._ensure_artifacts_dir()
        self._initialize_database()

    def _ensure_artifacts_dir(self):
        """
        Ensure the .wichy/artifacts directory exists.

        This method creates the artifacts directory if it doesn't exist. If a subdirectory
        is specified, it will create that as well.
        """
        os.makedirs(self.artifacts_dir, exist_ok=True)

    def _initialize_database(self):
        """Create database connection and initialize schema."""
        self.conn = sqlite3.connect(self.connection_string)
        self.conn.row_factory = sqlite3.Row

        # Enable WAL mode
        self.conn.execute("PRAGMA journal_mode=WAL")

        # Create artifacts table
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                replaced_by TEXT,
                data TEXT NOT NULL
            )
        """
        )

        # Create indexes for common queries
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_id
            ON artifacts(session_id)
        """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_replaced_by
            ON artifacts(replaced_by)
        """
        )

        self.conn.commit()

    def create(self, artifact: Artifact, session_id: str) -> bool:
        """
        Create a new artifact.

        Args:
            artifact: the artifact to create
            session_id: Session identifier

        Returns:
            True if created successfully, False otherwise
        """
        replaced_by = artifact.replaced_by
        data = artifact.model_dump_json()
        try:
            self.conn.execute(
                """
                INSERT INTO artifacts (id, session_id, replaced_by, data)
                VALUES (?, ?, ?, ?)
                """,
                (artifact.id, session_id, replaced_by, data),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_by_id(self, id: str) -> Optional[Artifact]:
        """
        Retrieve an artifact by ID.

        Args:
            id: Artifact identifier
            model: Optional Pydantic model class to validate and parse data into

        Returns:
            Dictionary containing artifact data, or Pydantic model instance if model provided.
            Returns None if not found.
        """
        cursor = self.conn.execute("SELECT * FROM artifacts WHERE id = ?", (id,))
        row = cursor.fetchone()

        if row:

            artifact = Artifact.model_validate_json(row["data"])

            return artifact
        return None

    def update_by_id(
        self,
        artifact: Artifact,
    ) -> bool:
        """
        Update an artifact by ID.

        Args:
            artifact: Artifact object
            session_id: New session ID (optional)

        Returns:
            True if updated successfully, False if artifact not found
        """
        # Build dynamic update query
        updates = []
        params = []

        data = artifact.model_dump_json()
        replaced_by = artifact.replaced_by

        if data is not None:
            updates.append("data = ?")
            params.append(data)

        if replaced_by is not None:
            updates.append("replaced_by = ?")
            params.append(replaced_by)

        if len(updates) < 1:
            return False

        params.append(artifact.id)
        query = f"UPDATE artifacts SET {', '.join(updates)} WHERE id = ?"

        cursor = self.conn.execute(query, params)
        self.conn.commit()

        return cursor.rowcount > 0

    def delete_by_id(self, id: str) -> bool:
        """
        Delete an artifact by ID.

        Args:
            id: Artifact identifier

        Returns:
            True if deleted successfully, False if artifact not found
        """
        cursor = self.conn.execute("DELETE FROM artifacts WHERE id = ?", (id,))
        self.conn.commit()

        return cursor.rowcount > 0

    def find_where_replaced_by_is_null(
        self, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Find all artifacts where replaced_by is NULL.

        Args:
            session_id: Optional session ID to filter by

        Returns:
            List of artifact dictionaries (or with Pydantic model instances if model provided)
        """
        if session_id:
            cursor = self.conn.execute(
                """
                SELECT * FROM artifacts
                WHERE replaced_by IS NULL AND session_id = ?
                """,
                (session_id,),
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM artifacts WHERE replaced_by IS NULL"
            )

        results = []
        for row in cursor.fetchall():
            result = {
                "session_id": row["session_id"],
                "data": row["data"],
            }

            result["data"] = Artifact.model_validate_json(row["data"])

            results.append(result)

        return results

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
