"""SQLite-backed session map storage."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .models import SessionMap, Node, Edge, NodeType, EdgeType, generate_node_id
from wichy.config import settings


class SessionMapStore:
    """SQLite-backed session map storage.

    Thread Safety:
        - Thread-safe within a single process via per-instance connection locks
        - Singleton pattern ensures one instance per database path per process
        - For multi-worker deployments (e.g., gunicorn), each worker has its own
          instance - SQLite handles concurrent writes safely with WAL mode
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS session_maps (
        context_id TEXT PRIMARY KEY,
        map_json TEXT NOT NULL,
        last_extracted_turn INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    
    CREATE INDEX IF NOT EXISTS idx_updated_at ON session_maps(updated_at);
    """

    _db_path: Path
    _lock = threading.Lock()
    _instances: dict[Path, "SessionMapStore"] = {}

    def __new__(cls, db_path: Path | None = None):
        """Singleton per database path."""
        if db_path is None:
            db_path = settings.session_map_db_path

        db_path = Path(db_path)

        with cls._lock:
            if db_path not in cls._instances:
                instance = super().__new__(cls)
                instance._db_path = db_path
                instance._initialize_db()
                cls._instances[db_path] = instance
            return cls._instances[db_path]

    def _initialize_db(self):
        """Create database and tables if they don't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn(for_write=True) as conn:
            conn.executescript(self.SCHEMA)

    @contextmanager
    def _get_conn(self, for_write: bool = False):
        """Get a database connection."""
        conn = sqlite3.connect(
            self._db_path,
            isolation_level="IMMEDIATE" if for_write else None,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            if for_write:
                conn.commit()
        finally:
            conn.close()

    def get(self, context_id: str) -> SessionMap | None:
        """Get session map for a context, or None if not found."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT map_json, last_extracted_turn FROM session_maps WHERE context_id = ?",
                (context_id,),
            ).fetchone()

            if row is None:
                return None

            session_map = SessionMap.from_json(row["map_json"])
            session_map.last_extracted_turn = row["last_extracted_turn"]
            return session_map

    def save(self, session_map: SessionMap):
        """Save or update a session map."""
        with self._get_conn(for_write=True) as conn:
            conn.execute(
                """
                INSERT INTO session_maps (context_id, map_json, last_extracted_turn, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(context_id) DO UPDATE SET
                    map_json = excluded.map_json,
                    last_extracted_turn = excluded.last_extracted_turn,
                    updated_at = excluded.updated_at
            """,
                (
                    session_map.context_id,
                    session_map.to_json(),
                    session_map.last_extracted_turn,
                    datetime.now().isoformat(),
                ),
            )

    def get_last_turn(self, context_id: str) -> int:
        """Get the last extracted turn for a context."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_extracted_turn FROM session_maps WHERE context_id = ?",
                (context_id,),
            ).fetchone()
            return row["last_extracted_turn"] if row else 0

    def set_last_turn(self, context_id: str, turn: int):
        """Update the last extracted turn."""
        with self._get_conn(for_write=True) as conn:
            # Create empty map if doesn't exist
            empty_map_json = json.dumps(
                {
                    "context_id": context_id,
                    "nodes": [],
                    "edges": [],
                    "last_extracted_turn": turn,
                    "updated_at": datetime.now().isoformat(),
                }
            )
            conn.execute(
                """
                INSERT INTO session_maps (context_id, map_json, last_extracted_turn)
                VALUES (?, ?, ?)
                ON CONFLICT(context_id) DO UPDATE SET last_extracted_turn = ?
            """,
                (context_id, empty_map_json, turn, turn),
            )

    def merge_nodes(
        self,
        context_id: str,
        new_nodes: list[Node],
        new_edges: list[Edge],
        turn: int,
    ):
        """Merge new nodes and edges into existing session map atomically."""
        with self._get_conn(for_write=True) as conn:
            # Read existing map within transaction
            row = conn.execute(
                "SELECT map_json FROM session_maps WHERE context_id = ?",
                (context_id,),
            ).fetchone()

            if row:
                session_map = SessionMap.from_json(row["map_json"])
            else:
                session_map = SessionMap(context_id=context_id)

            # Merge new nodes
            existing_ids = {n.id for n in session_map.nodes}
            for node in new_nodes:
                if node.id not in existing_ids:
                    session_map.nodes.append(node)

            # Merge new edges
            existing_edges = {(e.from_id, e.to_id) for e in session_map.edges}
            for edge in new_edges:
                if (edge.from_id, edge.to_id) not in existing_edges:
                    session_map.edges.append(edge)

            session_map.last_extracted_turn = turn
            session_map.updated_at = datetime.now()

            # Write within same transaction
            conn.execute(
                """INSERT INTO session_maps 
                   (context_id, map_json, last_extracted_turn, updated_at) 
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(context_id) DO UPDATE SET
                    map_json = excluded.map_json,
                    last_extracted_turn = excluded.last_extracted_turn,
                    updated_at = excluded.updated_at""",
                (
                    context_id,
                    session_map.to_json(),
                    turn,
                    session_map.updated_at.isoformat(),
                ),
            )

    def add_manual_node(
        self,
        context_id: str,
        node_type: NodeType,
        content: str,
        turn: int,
        parent_ids: list[str] | None = None,
    ) -> Node:
        """Add a manual user-created node."""
        session_map = self.get(context_id)

        if session_map is None:
            session_map = SessionMap(context_id=context_id)

        node = Node(
            id=generate_node_id(),
            type=node_type,
            content=content,
            created_at=datetime.now(),
            turn=turn,
            source_msg_idx=None,  # Manual nodes have no source
        )

        session_map.nodes.append(node)

        # Create edges to parent nodes if provided
        if parent_ids:
            existing_node_ids = {n.id for n in session_map.nodes}
            for parent_id in parent_ids:
                if parent_id in existing_node_ids:
                    edge = Edge(
                        from_id=parent_id,
                        to_id=node.id,
                        type=EdgeType.RELATED,
                    )
                    session_map.edges.append(edge)

        session_map.updated_at = datetime.now()
        self.save(session_map)

        return node

    def delete_node(self, context_id: str, node_id: str) -> bool:
        """Delete a node and its associated edges."""
        session_map = self.get(context_id)

        if session_map is None:
            return False

        # Remove node
        original_count = len(session_map.nodes)
        session_map.nodes = [n for n in session_map.nodes if n.id != node_id]

        if len(session_map.nodes) == original_count:
            return False  # Node not found

        # Remove edges involving this node
        session_map.edges = [
            e for e in session_map.edges if e.from_id != node_id and e.to_id != node_id
        ]

        session_map.updated_at = datetime.now()
        self.save(session_map)

        return True

    def clear(self, context_id: str):
        """Clear the session map for a context."""
        with self._get_conn(for_write=True) as conn:
            conn.execute("DELETE FROM session_maps WHERE context_id = ?", (context_id,))
