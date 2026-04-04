# Session Map Feature Specification

## Overview

### Problem Statement

During long conversation sessions with the LLM:

1. **Both user and LLM lose track** - Context bloats, earlier decisions and findings are forgotten
2. **User repeats themselves** - Re-explaining goals, re-correcting LLM, re-stating decisions
3. **No visibility** - User cannot see what has been explored, what decisions were made, what paths were abandoned

### Solution

**Session Map** - A visual, auto-extracted representation of the conversation that surfaces:

- Questions asked
- Files explored and what was found
- Findings discovered
- Decisions made
- Dead ends / wrong turns

The map is:
- **Auto-extracted** from conversation by LLM (with validation)
- **Visible** via web GUI (canvas/graph view)
- **Queryable** by the LLM (optional tool to read it)
- **Editable** by the user (manual pruning, notes)
- **Persistent** across sessions (linked to context)

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Main Conversation (REPL)                         │
│                                                                      │
│   User ↔ RootAgent ↔ Tools                                          │
│          │                                                           │
│          ▼                                                           │
│   ContextHandler (message history)                                  │
│          │                                                           │
│          │ after each assistant response                             │
│          ▼                                                           │
│   Check: user_turn_count - last_extracted_turn >= INTERVAL          │
│          │                                                           │
│          │ if true                                                   │
│          ▼                                                           │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│              Background Extraction (same thread, blocking)           │
│                                                                      │
│   1. Get messages since last extraction (ContextHandler)            │
│   2. Get existing map summary (SessionMapStore)                      │
│   3. Call LLM with EXTRACTION_PROMPT (includes existing map)        │
│   4. Parse JSON response into proposed nodes/edges                  │
│   5. Validate with validation LLM call                              │
│      ├─ VALID → proceed                                              │
│      └─ INVALID → retry with feedback (max retries: 2)             │
│   6. Store nodes/edges in SessionMapStore                           │
│   7. Update last_extracted_turn                                     │
│                                                                      │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   SessionMapStore (SQLite)                           │
│                                                                      │
│   .wichy/session_maps.db                                            │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ context_id | map_json | last_extracted_turn | updated_at   │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│   - context_id: Links to conversation context file                  │
│   - map_json: Full SessionMap as JSON (nodes, edges)               │
│   - last_extracted_turn: Turn number of last extraction             │
│                                                                      │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Web GUI (/tools/session-map/)                   │
│                                                                      │
│   - Vis.js canvas for graph visualization                           │
│   - Polls /api/session-map every 5 seconds                          │
│   - Node types: question, finding, decision, file, dead_end, note  │
│   - Click node for full content in side panel                       │
│   - Filter by node type                                             │
│   - Manual add/delete nodes                                         │
│   - Trigger extraction on-demand                                    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Data Model

### File: `src/wichy/session_map/models.py`

```python
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime
import uuid
import json


class NodeType(Enum):
    """Type of node in the session map."""
    QUESTION = "question"      # Questions asked during investigation
    FINDING = "finding"         # Facts/discoveries found
    DECISION = "decision"       # Choices made
    FILE = "file"               # Files explored (with brief summary)
    DEAD_END = "dead_end"       # Abandoned paths / wrong turns
    NOTE = "note"               # User-added notes


class EdgeType(Enum):
    """Type of connection between nodes."""
    LED_TO = "led_to"           # Question led to finding
    ANSWERED_BY = "answered_by" # Question answered by finding
    EXPLORED = "explored"       # Finding came from file exploration
    RULED_OUT = "ruled_out"    # Decision ruled out a path
    RELATED = "related"         # General relationship
    FOLLOWS = "follows"         # Temporal sequence


@dataclass
class Node:
    """A node in the session map."""
    id: str
    type: NodeType
    content: str                        # The actual content (question, finding, etc.)
    created_at: datetime
    turn: int                           # Conversation turn when created
    source_msg_idx: int | None = None  # Index in context.messages
    connects_to: list[str] = field(default_factory=list)  # IDs of connected nodes
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "turn": self.turn,
            "source_msg_idx": self.source_msg_idx,
            "connects_to": self.connects_to,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Node":
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            content=data["content"],
            created_at=datetime.fromisoformat(data["created_at"]),
            turn=data["turn"],
            source_msg_idx=data.get("source_msg_idx"),
            connects_to=data.get("connects_to", []),
        )


@dataclass
class Edge:
    """A directed connection between two nodes."""
    from_id: str
    to_id: str
    type: EdgeType
    
    def to_dict(self) -> dict:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type.value,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Edge":
        return cls(
            from_id=data["from"],
            to_id=data["to"],
            type=EdgeType(data["type"]),
        )


@dataclass
class SessionMap:
    """Complete session map for a conversation."""
    context_id: str                     # Links to context file
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    last_extracted_turn: int = 0
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "last_extracted_turn": self.last_extracted_turn,
            "updated_at": self.updated_at.isoformat(),
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())
    
    @classmethod
    def from_dict(cls, data: dict) -> "SessionMap":
        return cls(
            context_id=data["context_id"],
            nodes=[Node.from_dict(n) for n in data.get("nodes", [])],
            edges=[Edge.from_dict(e) for e in data.get("edges", [])],
            last_extracted_turn=data.get("last_extracted_turn", 0),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "SessionMap":
        return cls.from_dict(json.loads(json_str))
    
    def get_summary(self, max_nodes: int = 20, max_content_len: int = 100) -> str:
        """Generate a text summary for the extraction LLM."""
        lines = [f"Session Map ({len(self.nodes)} nodes, {len(self.edges)} edges):\n"]
        
        # Group by type
        by_type: dict[NodeType, list[Node]] = {}
        for node in self.nodes:
            by_type.setdefault(node.type, []).append(node)
        
        for node_type in [NodeType.QUESTION, NodeType.FINDING, NodeType.DECISION, NodeType.FILE, NodeType.DEAD_END]:
            if node_type in by_type:
                lines.append(f"\n## {node_type.value.upper()}S")
                for node in by_type[node_type][:max_nodes]:
                    content = node.content[:max_content_len]
                    if len(node.content) > max_content_len:
                        content += "..."
                    lines.append(f"  [{node.id}] {content}")
        
        return "\n".join(lines)


def generate_node_id() -> str:
    """Generate a unique node ID."""
    return f"node_{uuid.uuid4().hex[:8]}"
```

---

## Persistence Layer

### File: `src/wichy/session_map/store.py`

```python
"""SQLite-backed session map storage."""

import sqlite3
import threading
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime

from .models import SessionMap, Node, Edge, generate_node_id


class SessionMapStore:
    """Thread-safe SQLite storage for session maps.
    
    Database location: .wichy/session_maps.db
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
    
    _lock = threading.Lock()
    _instances: dict[Path, "SessionMapStore"] = {}
    
    def __new__(cls, db_path: Path | None = None):
        """Singleton per database path."""
        from wichy.config import settings
        
        if db_path is None:
            db_path = settings.wichy_home / "session_maps.db"
        
        db_path = Path(db_path)
        
        with cls._lock:
            if db_path not in cls._instances:
                instance = super().__new__(cls)
                instance._db_path = db_path
                instance._conn_lock = threading.Lock()
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
            isolation_level="EXCLUSIVE" if for_write else None,
        )
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
                (context_id,)
            ).fetchone()
            
            if row is None:
                return None
            
            session_map = SessionMap.from_json(row["map_json"])
            session_map.last_extracted_turn = row["last_extracted_turn"]
            return session_map
    
    def save(self, session_map: SessionMap):
        """Save or update a session map."""
        with self._get_conn(for_write=True) as conn:
            conn.execute("""
                INSERT INTO session_maps (context_id, map_json, last_extracted_turn, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(context_id) DO UPDATE SET
                    map_json = excluded.map_json,
                    last_extracted_turn = excluded.last_extracted_turn,
                    updated_at = excluded.updated_at
            """, (
                session_map.context_id,
                session_map.to_json(),
                session_map.last_extracted_turn,
                datetime.now().isoformat(),
            ))
    
    def get_last_turn(self, context_id: str) -> int:
        """Get the last extracted turn for a context."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_extracted_turn FROM session_maps WHERE context_id = ?",
                (context_id,)
            ).fetchone()
            return row["last_extracted_turn"] if row else 0
    
    def set_last_turn(self, context_id: str, turn: int):
        """Update the last extracted turn."""
        with self._get_conn(for_write=True) as conn:
            # Create empty map if doesn't exist
            conn.execute("""
                INSERT INTO session_maps (context_id, map_json, last_extracted_turn)
                VALUES (?, '[]', ?)
                ON CONFLICT(context_id) DO UPDATE SET last_extracted_turn = ?
            """, (context_id, turn, turn))
    
    def merge_nodes(
        self,
        context_id: str,
        new_nodes: list[Node],
        new_edges: list[Edge],
        turn: int,
    ):
        """Merge new nodes and edges into existing session map."""
        session_map = self.get(context_id)
        
        if session_map is None:
            session_map = SessionMap(context_id=context_id)
        
        # Add new nodes
        existing_ids = {n.id for n in session_map.nodes}
        for node in new_nodes:
            if node.id not in existing_ids:
                session_map.nodes.append(node)
        
        # Add new edges
        existing_edges = {(e.from_id, e.to_id) for e in session_map.edges}
        for edge in new_edges:
            if (edge.from_id, edge.to_id) not in existing_edges:
                session_map.edges.append(edge)
        
        # Update turn
        session_map.last_extracted_turn = turn
        session_map.updated_at = datetime.now()
        
        # Save
        self.save(session_map)
    
    def add_manual_node(
        self,
        context_id: str,
        node_type: NodeType,
        content: str,
        turn: int,
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
            e for e in session_map.edges
            if e.from_id != node_id and e.to_id != node_id
        ]
        
        session_map.updated_at = datetime.now()
        self.save(session_map)
        
        return True
    
    def clear(self, context_id: str):
        """Clear the session map for a context."""
        with self._get_conn(for_write=True) as conn:
            conn.execute("DELETE FROM session_maps WHERE context_id = ?", (context_id,))
```

---

## Extraction Logic

### File: `src/wichy/session_map/extractor.py`

```python
"""LLM-based session map extraction."""

import json
from datetime import datetime

from wichy.llm_backend import call
from wichy.config import settings
from .models import Node, Edge, NodeType, EdgeType, SessionMap, generate_node_id
from .validation import validate_extraction


# =============================================================================
# Prompts
# =============================================================================

EXTRACTION_PROMPT = """You are analyzing a conversation to extract a structured session map.

## Existing Session Map
{existing_map_summary}

## New Conversation (since last extraction)
{new_messages}

## Your Task
Extract ONLY NEW and SIGNIFICANT items from the conversation. Do NOT re-extract items that already exist in the map.

### Node Types
- QUESTION: Questions asked by user or agent to investigate something
- FINDING: Facts, discoveries, or observations made during investigation
- DECISION: Choices or decisions made (what to do, what approach to take)
- FILE: Files explored with brief summary of what was found/relevant
- DEAD_END: Paths explored that were abandoned or didn't work out

### Edge Types (connections between nodes)
- LED_TO: A question led to a finding
- ANSWERED_BY: A question was answered by a finding
- EXPLORED: A finding came from exploring a file
- RULED_OUT: A decision ruled out a path
- RELATED: General relationship
- FOLLOWS: Temporal sequence (one thing followed another)

## Output Format
Output valid JSON with this structure:
{{
  "nodes": [
    {{
      "type": "question|finding|decision|file|dead_end",
      "content": "The actual content - be concise but complete",
      "turn": <conversation turn number>,
      "connects_to": ["existing_node_id_1", "existing_node_id_2"]
    }}
  ],
  "edges": [
    {{
      "from": "node content or new node index (0, 1, 2...)",
      "to": "node content or id",
      "type": "led_to|answered_by|explored|ruled_out|related|follows"
    }}
  ]
}}

## Guidelines
1. Be selective - only extract truly significant items
2. For FILE nodes, include filename AND brief summary
3. For DECISION nodes, include the rationale
4. For DEAD_END nodes, include why it was abandoned
5. Connect to existing nodes when there's a clear relationship
6. Use existing node IDs from the existing map when connecting
7. For new nodes, you can reference them by index (0, 1, 2...) in edges
8. Skip trivial exchanges, greetings, and small talk
"""

VALIDATION_PROMPT = """You are validating a session map extraction.

## Existing Session Map
{existing_map_summary}

## New Conversation Excerpt
{new_messages}

## Proposed Extraction
{proposed_extraction}

## Validation Criteria
1. RELEVANCE: Are all extracted nodes actually significant to the conversation?
2. TYPE CORRECTNESS: Are node types correct?
   - QUESTION should be actual questions
   - FINDING should be facts/discoveries
   - DECISION should be choices made
   - FILE should include filename AND summary
   - DEAD_END should explain why abandoned
3. EDGE SANITY: Do edges connect logically? Are relationships correct?
4. ID VALIDITY: Do references to existing node IDs actually exist in the map?
5. COMPLETENESS: Is anything significant missing?
6. NO DUPLICATES: Are new nodes truly new (not duplicates of existing nodes)?

## Response Format
- If valid: "VALID: <brief confirmation>"
- If invalid: "INVALID: <specific issues and corrections needed>"

Do NOT re-output the extraction. Only state VALID or INVALID with explanation.
"""


# =============================================================================
# Formatting Functions
# =============================================================================

def format_messages_for_extraction(messages: list[dict], start_turn: int = 0) -> str:
    """Format conversation messages for extraction prompt."""
    lines = []
    turn = start_turn
    
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        
        # Skip system messages
        if role == "system":
            continue
        
        # Track turns (user + assistant = one turn)
        if role == "user":
            turn += 1
        
        # Truncate very long messages
        if len(content) > 500:
            content = content[:500] + "...[truncated]"
        
        lines.append(f"[Turn {turn}] {role.upper()}: {content}")
    
    return "\n\n".join(lines)


def format_extraction_for_display(nodes: list[dict], edges: list[dict]) -> str:
    """Format proposed extraction for validation prompt."""
    lines = ["### Nodes"]
    for i, node in enumerate(nodes):
        lines.append(f"  [{i}] {node.get('type')}: {node.get('content', '')[:100]}")
    
    lines.append("\n### Edges")
    for edge in edges:
        lines.append(f"  {edge.get('from')} --[{edge.get('type')}]--> {edge.get('to')}")
    
    return "\n".join(lines)


# =============================================================================
# Parsing Functions
# =============================================================================

def parse_extraction_response(response_content: str) -> tuple[list[dict], list[dict]]:
    """Parse LLM JSON response into nodes and edges."""
    try:
        data = json.loads(response_content)
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        return nodes, edges
    except json.JSONDecodeError:
        # Try to extract JSON from response
        import re
        json_match = re.search(r'\{[\s\S]*\}', response_content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                nodes = data.get("nodes", [])
                edges = data.get("edges", [])
                return nodes, edges
            except json.JSONDecodeError:
                pass
        
        return [], []


def parse_validation_response(response_content: str) -> tuple[bool, str]:
    """Parse validation response into (is_valid, feedback)."""
    content = response_content.strip().upper()
    
    if content.startswith("VALID"):
        # Extract explanation
        feedback = response_content.strip()[5:].strip().strip(":")
        return True, feedback or "Extraction validated successfully"
    
    elif content.startswith("INVALID"):
        # Extract issues
        feedback = response_content.strip()[7:].strip().strip(":")
        return False, feedback or "Validation failed"
    
    else:
        # Ambiguous response - treat as valid with warning
        return True, f"Ambiguous validation response: {response_content[:100]}"


# =============================================================================
# Main Extraction Class
# =============================================================================

class SessionMapExtractor:
    """Extracts session map from conversation using LLM."""
    
    def __init__(self, model_str: str | None = None):
        self.model_str = model_str  # None = use default from settings
    
    def _get_model_str(self) -> str:
        """Get effective model string."""
        from wichy.config import settings
        return self.model_str or settings.session_map_model or settings.default_model
    
    def extract(
        self,
        messages: list[dict],
        existing_map: SessionMap | None,
        start_turn: int = 0,
    ) -> tuple[list[Node], list[Edge]]:
        """Extract nodes and edges from conversation messages."""
        
        # Format existing map summary
        existing_summary = "None (empty map)"
        if existing_map and existing_map.nodes:
            existing_summary = existing_map.get_summary()
        
        # Format messages
        formatted_messages = format_messages_for_extraction(messages, start_turn)
        
        # Build prompt
        prompt = EXTRACTION_PROMPT.format(
            existing_map_summary=existing_summary,
            new_messages=formatted_messages,
        )
        
        # Call LLM
        response = call(
            context=[{"role": "user", "content": prompt}],
            model_str=self._get_model_str(),
            extra_args={"response_format": {"type": "json_object"}},
        )
        
        # Parse response
        proposed_nodes, proposed_edges = parse_extraction_response(response.message.content)
        
        # Convert to Node and Edge objects
        nodes = []
        node_id_map = {}  # Map from index to generated ID
        
        for i, node_data in enumerate(proposed_nodes):
            node_type = NodeType(node_data.get("type", "finding"))
            content = node_data.get("content", "")
            
            if not content:
                continue
            
            node = Node(
                id=generate_node_id(),
                type=node_type,
                content=content,
                created_at=datetime.now(),
                turn=start_turn + node_data.get("turn", i),
                source_msg_idx=node_data.get("source_msg_idx"),
                connects_to=node_data.get("connects_to", []),
            )
            
            node_id_map[str(i)] = node.id
            nodes.append(node)
        
        # Convert edges
        edges = []
        for edge_data in proposed_edges:
            from_ref = edge_data.get("from", "")
            to_ref = edge_data.get("to", "")
            edge_type = EdgeType(edge_data.get("type", "related"))
            
            # Resolve references
            from_id = node_id_map.get(str(from_ref), from_ref)
            to_id = node_id_map.get(str(to_ref), to_ref)
            
            # Skip edges with unresolved references
            if not from_id or not to_id:
                continue
            
            edges.append(Edge(from_id=from_id, to_id=to_id, type=edge_type))
        
        return nodes, edges
    
    def extract_with_validation(
        self,
        messages: list[dict],
        existing_map: SessionMap | None,
        start_turn: int = 0,
        max_retries: int | None = None,
    ) -> tuple[bool, list[Node], list[Edge], str]:
        """Extract with validation retry loop.
        
        Returns:
            (is_valid, nodes, edges, feedback)
        """
        from wichy.config import settings
        
        max_retries = max_retries or settings.session_map_validation_retries
        existing_summary = "None (empty map)"
        if existing_map and existing_map.nodes:
            existing_summary = existing_map.get_summary()
        
        formatted_messages = format_messages_for_extraction(messages, start_turn)
        
        # Initial extraction
        prompt = EXTRACTION_PROMPT.format(
            existing_map_summary=existing_summary,
            new_messages=formatted_messages,
        )
        
        response = call(
            context=[{"role": "user", "content": prompt}],
            model_str=self._get_model_str(),
            extra_args={"response_format": {"type": "json_object"}},
        )
        
        proposed_nodes, proposed_edges = parse_extraction_response(response.message.content)
        
        # Validation loop
        for attempt in range(max_retries + 1):
            # Validate
            validation_prompt = VALIDATION_PROMPT.format(
                existing_map_summary=existing_summary,
                new_messages=formatted_messages,
                proposed_extraction=format_extraction_for_display(proposed_nodes, proposed_edges),
            )
            
            validation_response = call(
                context=[{"role": "user", "content": validation_prompt}],
                model_str=self._get_model_str(),
            )
            
            is_valid, feedback = parse_validation_response(validation_response.message.content)
            
            if is_valid:
                # Convert to objects and return
                nodes, edges = self._convert_to_objects(proposed_nodes, proposed_edges, start_turn)
                return True, nodes, edges, feedback
            
            # Retry with feedback
            if attempt < max_retries:
                retry_prompt = f"""Previous extraction was INVALID: {feedback}

Please extract again, addressing these issues.

{EXTRACTION_PROMPT.format(
    existing_map_summary=existing_summary,
    new_messages=formatted_messages,
)}"""
                
                response = call(
                    context=[{"role": "user", "content": retry_prompt}],
                    model_str=self._get_model_str(),
                    extra_args={"response_format": {"type": "json_object"}},
                )
                
                proposed_nodes, proposed_edges = parse_extraction_response(response.message.content)
        
        # Max retries reached
        return False, [], [], f"Validation failed after {max_retries} retries: {feedback}"
    
    def _convert_to_objects(
        self,
        proposed_nodes: list[dict],
        proposed_edges: list[dict],
        start_turn: int,
    ) -> tuple[list[Node], list[Edge]]:
        """Convert parsed dicts to Node and Edge objects."""
        nodes = []
        node_id_map = {}
        
        for i, node_data in enumerate(proposed_nodes):
            node_type = NodeType(node_data.get("type", "finding"))
            content = node_data.get("content", "")
            
            if not content:
                continue
            
            node = Node(
                id=generate_node_id(),
                type=node_type,
                content=content,
                created_at=datetime.now(),
                turn=start_turn + node_data.get("turn", i),
                source_msg_idx=node_data.get("source_msg_idx"),
                connects_to=node_data.get("connects_to", []),
            )
            
            node_id_map[str(i)] = node.id
            nodes.append(node)
        
        edges = []
        for edge_data in proposed_edges:
            from_ref = edge_data.get("from", "")
            to_ref = edge_data.get("to", "")
            edge_type = EdgeType(edge_data.get("type", "related"))
            
            from_id = node_id_map.get(str(from_ref), from_ref)
            to_id = node_id_map.get(str(to_ref), to_ref)
            
            if from_id and to_id:
                edges.append(Edge(from_id=from_id, to_id=to_id, type=edge_type))
        
        return nodes, edges
```

---

## Validation

### File: `src/wichy/session_map/validation.py`

```python
"""Validation for session map extractions."""

from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of validating an extraction."""
    is_valid: bool
    nodes: list[dict]
    edges: list[dict]
    feedback: str | None = None


# Validation is integrated into extractor.py via VALIDATION_PROMPT
# This file exists for future extensions (e.g., rule-based validation)

def validate_node_types(nodes: list[dict], valid_types: set[str]) -> list[str]:
    """Check that all node types are valid."""
    issues = []
    for i, node in enumerate(nodes):
        node_type = node.get("type", "")
        if node_type not in valid_types:
            issues.append(f"Node {i} has invalid type: {node_type}")
    return issues


def validate_edge_types(edges: list[dict], valid_types: set[str]) -> list[str]:
    """Check that all edge types are valid."""
    issues = []
    for i, edge in enumerate(edges):
        edge_type = edge.get("type", "")
        if edge_type not in valid_types:
            issues.append(f"Edge {i} has invalid type: {edge_type}")
    return issues


def validate_references(edges: list[dict], node_ids: set[str]) -> list[str]:
    """Check that all edge references point to valid node IDs."""
    issues = []
    for i, edge in enumerate(edges):
        from_id = edge.get("from", "")
        to_id = edge.get("to", "")
        
        if from_id not in node_ids:
            issues.append(f"Edge {i} has invalid 'from' reference: {from_id}")
        if to_id not in node_ids:
            issues.append(f"Edge {i} has invalid 'to' reference: {to_id}")
    return issues
```

---

## API Routes

### File: `src/wichy/session_map/api.py`

```python
"""Flask API routes for session map."""

from flask import Blueprint, jsonify, request

from .store import SessionMapStore
from .models import NodeType


# Global references (set by main initialization)
_session_map_store: SessionMapStore | None = None
_context_handler = None  # Will be ContextHandler


def set_session_map_store(store: SessionMapStore):
    """Set the session map store instance."""
    global _session_map_store
    _session_map_store = store


def set_context_handler(ctx):
    """Set the context handler instance."""
    global _context_handler
    _context_handler = ctx


bp = Blueprint("session_map", __name__, url_prefix="/tools/session-map")


def register_routes(bp: Blueprint):
    """Register all routes with the blueprint."""
    
    @bp.route("/", methods=["GET"])
    def index():
        """Render the session map web UI."""
        from flask import render_template
        return render_template("session_map.html")
    
    @bp.route("/api/map", methods=["GET"])
    def get_map():
        """Get the current session map."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500
        
        context_id = str(_context_handler.path)
        session_map = _session_map_store.get(context_id)
        
        if session_map is None:
            return jsonify({
                "nodes": [],
                "edges": [],
                "last_extracted_turn": 0,
                "updated_at": None,
            })
        
        return jsonify(session_map.to_dict())
    
    @bp.route("/api/status", methods=["GET"])
    def get_status():
        """Get extraction status."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500
        
        context_id = str(_context_handler.path)
        
        # Get current turn count
        user_turns = len([m for m in _context_handler.context if m.get("role") == "user"])
        last_extracted = _session_map_store.get_last_turn(context_id)
        
        return jsonify({
            "current_turn": user_turns,
            "last_extracted_turn": last_extracted,
            "next_extraction_in": 10 - ((user_turns - last_extracted) % 10),  # Assuming interval of 10
            "enabled": True,
        })
    
    @bp.route("/api/node", methods=["POST"])
    def add_node():
        """Add a manual node."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500
        
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        node_type_str = data.get("type", "note")
        content = data.get("content", "")
        
        try:
            node_type = NodeType(node_type_str)
        except ValueError:
            return jsonify({"error": f"Invalid node type: {node_type_str}"}), 400
        
        if not content:
            return jsonify({"error": "Content is required"}), 400
        
        context_id = str(_context_handler.path)
        current_turn = len([m for m in _context_handler.context if m.get("role") == "user"])
        
        node = _session_map_store.add_manual_node(
            context_id=context_id,
            node_type=node_type,
            content=content,
            turn=current_turn,
        )
        
        return jsonify(node.to_dict())
    
    @bp.route("/api/node/<node_id>", methods=["DELETE"])
    def delete_node(node_id: str):
        """Delete a node."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500
        
        context_id = str(_context_handler.path)
        success = _session_map_store.delete_node(context_id, node_id)
        
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Node not found"}), 404
    
    @bp.route("/api/extract", methods=["POST"])
    def trigger_extraction():
        """Manually trigger extraction."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500
        
        from .extractor import SessionMapExtractor
        
        context_id = str(_context_handler.path)
        last_turn = _session_map_store.get_last_turn(context_id)
        
        # Get messages since last extraction
        messages = _context_handler.context[last_turn:]
        
        # Get existing map
        existing_map = _session_map_store.get(context_id)
        
        # Extract
        extractor = SessionMapExtractor()
        current_turn = len([m for m in _context_handler.context if m.get("role") == "user"])
        
        is_valid, nodes, edges, feedback = extractor.extract_with_validation(
            messages=messages,
            existing_map=existing_map,
            start_turn=last_turn,
        )
        
        if is_valid and nodes:
            _session_map_store.merge_nodes(context_id, nodes, edges, current_turn)
        
        return jsonify({
            "success": is_valid,
            "nodes_added": len(nodes),
            "edges_added": len(edges),
            "feedback": feedback,
        })
    
    @bp.route("/api/clear", methods=["POST"])
    def clear_map():
        """Clear the session map."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500
        
        context_id = str(_context_handler.path)
        _session_map_store.clear(context_id)
        
        return jsonify({"success": True})
```

---

## Blueprint Registration

### File: `src/wichy/session_map/__init__.py`

```python
"""Session Map feature - auto-extracted conversation visualization."""

from flask import Flask

from .store import SessionMapStore
from .api import bp, register_routes, set_session_map_store, set_context_handler


__all__ = [
    "SessionMapStore",
    "bp",
    "register_routes",
    "set_session_map_store",
    "set_context_handler",
]


def register(app: Flask):
    """Register the session map blueprint with the Flask app."""
    from . import api
    api.register_routes(bp)
    app.register_blueprint(bp)
```

---

## Configuration

### File: `src/wichy/config/settings.py` (additions)

```python
# Add to the Settings class:

# =============================================================================
# Session Map Settings
# =============================================================================

# Enable/disable session map extraction
session_map_enabled: bool = True

# Extract every N user turns
session_map_interval: int = 10

# Model to use for extraction (None = use default model)
session_map_model: str | None = None

# Max validation retries before accepting extraction
session_map_validation_retries: int = 2

# Session map database path (computed property)
@property
def session_map_db_path(self) -> Path:
    """Path to session map SQLite database."""
    return self.wichy_home / ".wichy" / "session_maps.db"
```

Environment variables:
```bash
WICHY_SESSION_MAP_ENABLED=true
WICHY_SESSION_MAP_INTERVAL=10
WICHY_SESSION_MAP_MODEL=  # Empty = use default
WICHY_SESSION_MAP_VALIDATION_RETRIES=2
```

---

## Integration in RootAgent

### File: `src/wichy/root_agent/root_agent.py` (modifications)

```python
# Add imports near top
from wichy.config import settings
from wichy.session_map.store import SessionMapStore
from wichy.session_map.extractor import SessionMapExtractor

# Add to RootAgent.__init__
class RootAgent:
    def __init__(self, ...):
        # ... existing init ...
        
        # Session map components (lazy initialization)
        self._session_map_store: SessionMapStore | None = None
        self._session_map_extractor: SessionMapExtractor | None = None
    
    def _init_session_map(self):
        """Initialize session map components."""
        if not settings.session_map_enabled:
            return
        
        if self._session_map_store is None:
            self._session_map_store = SessionMapStore()
        
        if self._session_map_extractor is None:
            self._session_map_extractor = SessionMapExtractor()
    
    def _get_user_turn_count(self) -> int:
        """Count number of user turns in conversation."""
        return len([m for m in self.context.context if m.get("role") == "user"])
    
    def _maybe_extract_session_map(self):
        """Check if session map extraction is due and run it."""
        if not settings.session_map_enabled:
            return
        
        self._init_session_map()
        
        current_turn = self._get_user_turn_count()
        context_id = str(self.context.path)
        last_extracted = self._session_map_store.get_last_turn(context_id)
        
        if current_turn - last_extracted < settings.session_map_interval:
            return  # Not time yet
        
        # Get messages since last extraction
        # Note: We need to map turn to message index
        # For simplicity, we'll use a different approach:
        # Count user messages and track their indices
        
        messages_since_last = self._get_messages_since_turn(last_extracted)
        
        if not messages_since_last:
            return
        
        # Get existing map
        existing_map = self._session_map_store.get(context_id)
        
        # Extract with validation
        is_valid, nodes, edges, feedback = self._session_map_extractor.extract_with_validation(
            messages=messages_since_last,
            existing_map=existing_map,
            start_turn=last_extracted,
        )
        
        if is_valid and nodes:
            self._session_map_store.merge_nodes(
                context_id=context_id,
                new_nodes=nodes,
                new_edges=edges,
                turn=current_turn,
            )
    
    def _get_messages_since_turn(self, last_turn: int) -> list[dict]:
        """Get messages since the last extracted turn."""
        messages = []
        user_turn_count = 0
        
        for msg in self.context.context:
            if msg.get("role") == "user":
                user_turn_count += 1
            
            if user_turn_count > last_turn:
                messages.append(msg)
        
        return messages
    
    def process(self, line: str):
        """Process a user message."""
        # ... existing process logic ...
        
        # After processing, check for session map extraction
        self._maybe_extract_session_map()
        
        return response
```

---

## Server Integration

### File: `src/wichy/server.py` (modifications)

```python
# Add import
from wichy.session_map import register as register_session_map

# Add to register_blueprints function
def register_blueprints(app: Flask) -> None:
    # ... existing registrations ...
    register_session_map(app)

# Add to initialization (after context handler is set up)
def init_session_map(context_handler):
    """Initialize session map with context handler reference."""
    from wichy.session_map.api import set_context_handler
    set_context_handler(context_handler)
```

---

## Web GUI

### File: `src/wichy/templates/session_map.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wichy - Session Map</title>
    <link rel="stylesheet" href="/shared/shared.css">
    <link rel="stylesheet" href="{{ url_for('session_map.static', filename='session_map.css') }}">
    <script src="/static/vis-network/vis-network.min.js"></script>
</head>
<body>
    <div class="container">
        <header class="header-simple">
            <div>
                <h1 class="header-title">Session Map</h1>
                <p class="header-subtitle">Visual overview of your conversation</p>
            </div>
            <nav class="header-nav">
                <a href="/">Back to Home</a>
            </nav>
        </header>

        <div class="main-layout">
            <!-- Sidebar -->
            <aside class="sidebar">
                <div class="sidebar-section">
                    <h3 class="sidebar-title">Status</h3>
                    <div id="status">
                        <p><strong>Turn:</strong> <span id="current-turn">-</span></p>
                        <p><strong>Last extracted:</strong> <span id="last-extracted">-</span></p>
                        <p><strong>Next extraction:</strong> <span id="next-extraction">-</span></p>
                    </div>
                </div>

                <div class="sidebar-section">
                    <h3 class="sidebar-title">Filters</h3>
                    <div class="filters">
                        <label><input type="checkbox" checked data-type="question"> Questions</label>
                        <label><input type="checkbox" checked data-type="finding"> Findings</label>
                        <label><input type="checkbox" checked data-type="decision"> Decisions</label>
                        <label><input type="checkbox" checked data-type="file"> Files</label>
                        <label><input type="checkbox" data-type="dead_end"> Dead ends</label>
                        <label><input type="checkbox" checked data-type="note"> Notes</label>
                    </div>
                </div>

                <div class="sidebar-section">
                    <h3 class="sidebar-title">Actions</h3>
                    <div class="sidebar-controls">
                        <button id="btn-extract" class="btn btn-primary">Extract Now</button>
                        <button id="btn-add-note" class="btn btn-secondary">+ Add Note</button>
                        <button id="btn-clear" class="btn btn-danger">Clear Map</button>
                    </div>
                </div>
            </aside>

            <!-- Canvas -->
            <main class="canvas-container">
                <div id="network"></div>
            </main>
        </div>

        <!-- Node detail panel -->
        <div id="node-detail" class="node-detail hidden">
            <div class="node-detail-header">
                <span id="node-type-badge" class="type-badge"></span>
                <span id="node-turn-badge" class="turn-badge"></span>
                <button id="btn-close-detail" class="btn-close">×</button>
            </div>
            <div id="node-content" class="node-content"></div>
            <div class="node-detail-actions">
                <button id="btn-delete-node" class="btn btn-danger btn-sm">Delete</button>
            </div>
        </div>

        <!-- Add note modal -->
        <div id="add-note-modal" class="modal-overlay hidden">
            <div class="modal-content">
                <h3>Add Note</h3>
                <div class="form-group">
                    <label class="form-label">Content:</label>
                    <textarea id="note-content" class="form-textarea" rows="4" placeholder="Enter your note..."></textarea>
                </div>
                <div class="form-actions">
                    <button id="btn-save-note" class="btn btn-primary">Save</button>
                    <button id="btn-cancel-note" class="btn btn-secondary">Cancel</button>
                </div>
            </div>
        </div>
    </div>

    <script src="{{ url_for('session_map.static', filename='session_map.js') }}"></script>
</body>
</html>
```

---

### File: `src/wichy/static/session_map.js`

```javascript
/**
 * Session Map Web UI
 */

// =============================================================================
// State
// =============================================================================

let network = null;
let nodes = null;
let edges = null;
let selectedNode = null;
let pollInterval = null;

const nodeColors = {
    question: '#4A90D9',    // Blue
    finding: '#5CB85C',     // Green
    decision: '#F0AD4E',    // Orange
    file: '#5BC0DE',        // Cyan
    dead_end: '#D9534F',    // Red
    note: '#999999',        // Gray
};

const filterState = {
    question: true,
    finding: true,
    decision: true,
    file: true,
    dead_end: false,
    note: true,
};

// =============================================================================
// Initialization
// =============================================================================

document.addEventListener('DOMContentLoaded', () => {
    initNetwork();
    initFilters();
    initActions();
    loadMap();
    startPolling();
});

function initNetwork() {
    const container = document.getElementById('network');
    
    nodes = new vis.DataSet([]);
    edges = new vis.DataSet([]);
    
    const data = { nodes, edges };
    
    const options = {
        nodes: {
            shape: 'box',
            shapeProperties: {
                borderRadius: 6,
            },
            font: {
                size: 14,
                face: 'Arial',
            },
            widthConstraint: {
                maximum: 200,
            },
            heightConstraint: {
                minimum: 40,
            },
            labelHighlightBold: false,
            borderWidth: 2,
            shadow: true,
        },
        edges: {
            arrows: 'to',
            color: { color: '#888', highlight: '#4A90D9' },
            smooth: {
                type: 'continuous',
            },
        },
        physics: {
            stabilization: true,
            barnesHut: {
                gravitationalConstant: -2000,
                springLength: 150,
            },
        },
        interaction: {
            hover: true,
            tooltipDelay: 200,
        },
    };
    
    network = new vis.Network(container, data, options);
    
    // Click handler for node selection
    network.on('click', (params) => {
        if (params.nodes.length > 0) {
            selectNode(params.nodes[0]);
        } else {
            deselectNode();
        }
    });
}

function initFilters() {
    document.querySelectorAll('.filters input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', (e) => {
            filterState[e.target.dataset.type] = e.target.checked;
            updateNodeVisibility();
        });
    });
}

function initActions() {
    // Extract now
    document.getElementById('btn-extract').addEventListener('click', triggerExtraction);
    
    // Add note
    document.getElementById('btn-add-note').addEventListener('click', showAddNoteModal);
    document.getElementById('btn-save-note').addEventListener('click', saveNote);
    document.getElementById('btn-cancel-note').addEventListener('click', hideAddNoteModal);
    
    // Clear map
    document.getElementById('btn-clear').addEventListener('click', clearMap);
    
    // Node detail
    document.getElementById('btn-close-detail').addEventListener('click', deselectNode);
    document.getElementById('btn-delete-node').addEventListener('click', deleteSelectedNode);
}

// =============================================================================
// Data Loading
// =============================================================================

async function loadMap() {
    try {
        const [mapRes, statusRes] = await Promise.all([
            fetch('/tools/session-map/api/map'),
            fetch('/tools/session-map/api/status'),
        ]);
        
        const mapData = await mapRes.json();
        const statusData = await statusRes.json();
        
        updateNetwork(mapData);
        updateStatus(statusData);
    } catch (err) {
        console.error('Failed to load session map:', err);
    }
}

function updateNetwork(mapData) {
    // Clear existing
    nodes.clear();
    edges.clear();
    
    // Add nodes
    mapData.nodes.forEach(node => {
        nodes.add({
            id: node.id,
            label: truncate(node.content, 50),
            title: node.content,
            color: nodeColors[node.type],
            type: node.type,
            data: node,
        });
    });
    
    // Add edges
    mapData.edges.forEach(edge => {
        edges.add({
            id: `${edge.from}-${edge.to}`,
            from: edge.from,
            to: edge.to,
            arrows: 'to',
        });
    });
    
    updateNodeVisibility();
}

function updateStatus(statusData) {
    document.getElementById('current-turn').textContent = statusData.current_turn || '-';
    document.getElementById('last-extracted').textContent = statusData.last_extracted_turn || '-';
    document.getElementById('next-extraction').textContent = 
        statusData.next_extraction_in ? `in ${statusData.next_extraction_in} turns` : '-';
}

function updateNodeVisibility() {
    nodes.forEach(node => {
        const visible = filterState[node.type];
        nodes.update({ id: node.id, hidden: !visible });
    });
}

// =============================================================================
// Actions
// =============================================================================

async function triggerExtraction() {
    const btn = document.getElementById('btn-extract');
    btn.textContent = 'Extracting...';
    btn.disabled = true;
    
    try {
        const res = await fetch('/tools/session-map/api/extract', { method: 'POST' });
        const data = await res.json();
        
        if (data.success) {
            loadMap();
        } else {
            alert(`Extraction failed: ${data.feedback || 'Unknown error'}`);
        }
    } catch (err) {
        console.error('Extraction failed:', err);
        alert('Extraction failed');
    } finally {
        btn.textContent = 'Extract Now';
        btn.disabled = false;
    }
}

function showAddNoteModal() {
    document.getElementById('add-note-modal').classList.remove('hidden');
    document.getElementById('note-content').focus();
}

function hideAddNoteModal() {
    document.getElementById('add-note-modal').classList.add('hidden');
    document.getElementById('note-content').value = '';
}

async function saveNote() {
    const content = document.getElementById('note-content').value.trim();
    if (!content) return;
    
    try {
        const res = await fetch('/tools/session-map/api/node', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'note', content }),
        });
        
        if (res.ok) {
            hideAddNoteModal();
            loadMap();
        }
    } catch (err) {
        console.error('Failed to save note:', err);
    }
}

async function clearMap() {
    if (!confirm('Are you sure you want to clear the session map?')) return;
    
    try {
        await fetch('/tools/session-map/api/clear', { method: 'POST' });
        loadMap();
    } catch (err) {
        console.error('Failed to clear map:', err);
    }
}

// =============================================================================
// Node Selection
// =============================================================================

function selectNode(nodeId) {
    const node = nodes.get(nodeId);
    if (!node) return;
    
    selectedNode = node;
    
    const detail = document.getElementById('node-detail');
    detail.classList.remove('hidden');
    
    document.getElementById('node-type-badge').textContent = node.data.type.toUpperCase();
    document.getElementById('node-type-badge').style.backgroundColor = nodeColors[node.data.type];
    document.getElementById('node-turn-badge').textContent = `Turn ${node.data.turn}`;
    document.getElementById('node-content').textContent = node.data.content;
}

function deselectNode() {
    selectedNode = null;
    document.getElementById('node-detail').classList.add('hidden');
}

async function deleteSelectedNode() {
    if (!selectedNode) return;
    if (!confirm('Delete this node?')) return;
    
    try {
        await fetch(`/tools/session-map/api/node/${selectedNode.id}`, { method: 'DELETE' });
        deselectNode();
        loadMap();
    } catch (err) {
        console.error('Failed to delete node:', err);
    }
}

// =============================================================================
// Polling
// =============================================================================

function startPolling() {
    // Poll for updates every 5 seconds
    pollInterval = setInterval(() => {
        loadMap();
    }, 5000);
}

// =============================================================================
// Utilities
// =============================================================================

function truncate(str, maxLen) {
    if (str.length <= maxLen) return str;
    return str.substring(0, maxLen) + '...';
}
```

---

### File: `src/wichy/static/session_map.css`

```css
/* Session Map Styles */

.container {
    display: flex;
    flex-direction: column;
    height: 100vh;
}

.main-layout {
    display: flex;
    flex: 1;
    overflow: hidden;
}

/* Sidebar */
.sidebar {
    width: 250px;
    background: #f5f5f5;
    border-right: 1px solid #ddd;
    padding: 15px;
    overflow-y: auto;
}

.sidebar-section {
    margin-bottom: 20px;
}

.sidebar-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    color: #666;
    margin-bottom: 10px;
}

.sidebar-controls {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

/* Filters */
.filters {
    display: flex;
    flex-direction: column;
    gap: 5px;
}

.filters label {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    cursor: pointer;
}

.filters input[type="checkbox"] {
    width: 14px;
    height: 14px;
}

/* Canvas */
.canvas-container {
    flex: 1;
    position: relative;
}

#network {
    width: 100%;
    height: 100%;
}

/* Node Detail Panel */
.node-detail {
    position: fixed;
    right: 20px;
    top: 80px;
    width: 350px;
    background: white;
    border: 1px solid #ddd;
    border-radius: 8px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    z-index: 1000;
}

.node-detail.hidden {
    display: none;
}

.node-detail-header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px;
    border-bottom: 1px solid #eee;
}

.type-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 4px;
    color: white;
}

.turn-badge {
    font-size: 11px;
    color: #666;
}

.btn-close {
    margin-left: auto;
    background: none;
    border: none;
    font-size: 20px;
    cursor: pointer;
    color: #999;
}

.btn-close:hover {
    color: #333;
}

.node-content {
    padding: 15px;
    font-size: 14px;
    line-height: 1.6;
    max-height: 300px;
    overflow-y: auto;
    white-space: pre-wrap;
}

.node-detail-actions {
    padding: 10px 12px;
    border-top: 1px solid #eee;
}

/* Modal */
.modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 2000;
}

.modal-overlay.hidden {
    display: none;
}

.modal-content {
    background: white;
    padding: 20px;
    border-radius: 8px;
    width: 400px;
    max-width: 90%;
}

.modal-content h3 {
    margin-top: 0;
    margin-bottom: 15px;
}

/* Buttons */
.btn {
    padding: 8px 16px;
    border: none;
    border-radius: 4px;
    cursor: pointer;
    font-size: 14px;
    transition: background 0.2s;
}

.btn-primary {
    background: #4A90D9;
    color: white;
}

.btn-primary:hover {
    background: #357ABD;
}

.btn-primary:disabled {
    background: #ccc;
    cursor: not-allowed;
}

.btn-secondary {
    background: #f5f5f5;
    color: #333;
    border: 1px solid #ddd;
}

.btn-secondary:hover {
    background: #e5e5e5;
}

.btn-danger {
    background: #D9534F;
    color: white;
}

.btn-danger:hover {
    background: #C9302C;
}

.btn-sm {
    padding: 4px 12px;
    font-size: 12px;
}

/* Form elements */
.form-group {
    margin-bottom: 15px;
}

.form-label {
    display: block;
    margin-bottom: 5px;
    font-weight: 500;
}

.form-textarea {
    width: 100%;
    padding: 8px;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 14px;
    resize: vertical;
}

.form-actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
}

/* Status */
#status p {
    margin: 5px 0;
    font-size: 13px;
}

#status strong {
    color: #444;
}
```

---

## LLM Tool (Phase 5)

### File: `src/wichy/tools/read_session_map.py` (future)

```python
"""Tool for LLM to read the session map."""

from pydantic import BaseModel, Field
from .base import BaseTool
from ..session_map.store import SessionMapStore


class ReadSessionMapInput(BaseModel):
    """Input for reading session map."""
    pass


class ReadSessionMapOutput(BaseModel):
    """Output for reading session map."""
    nodes: list[dict] = Field(default_factory=list, description="List of nodes in the session map")
    edges: list[dict] = Field(default_factory=list, description="List of edges in the session map")
    summary: str = Field(default="", description="Text summary of the session map")


class ReadSessionMapTool(BaseTool):
    """Read the current session map to understand conversation history."""
    
    name = "read_session_map"
    description = "Read the session map to see questions, findings, decisions, and files explored in this conversation. Use this to understand what has been discussed and avoid repeating yourself."
    parameters_model = ReadSessionMapInput
    result_model = ReadSessionMapOutput
    
    # Will be set by main initialization
    _store: SessionMapStore | None = None
    _context_handler = None
    
    @classmethod
    def set_context(cls, context_handler):
        """Set the context handler reference."""
        cls._context_handler = context_handler
        cls._store = SessionMapStore()
    
    def execute(self) -> ReadSessionMapOutput:
        if self._store is None or self._context_handler is None:
            return ReadSessionMapOutput(
                nodes=[],
                edges=[],
                summary="Session map not initialized.",
            )
        
        context_id = str(self._context_handler.path)
        session_map = self._store.get(context_id)
        
        if session_map is None or not session_map.nodes:
            return ReadSessionMapOutput(
                nodes=[],
                edges=[],
                summary="Session map is empty.",
            )
        
        return ReadSessionMapOutput(
            nodes=[n.to_dict() for n in session_map.nodes],
            edges=[e.to_dict() for e in session_map.edges],
            summary=session_map.get_summary(),
        )
```

---

## File Structure Summary

```
src/wichy/
├── config/
│   └── settings.py                  # Add session_map_* settings
├── session_map/
│   ├── __init__.py                  # Blueprint registration
│   ├── models.py                    # Node, Edge, SessionMap dataclasses
│   ├── store.py                     # SQLite persistence (SessionMapStore)
│   ├── extractor.py                 # LLM extraction with validation
│   ├── validation.py                # Validation logic
│   └── api.py                       # Flask routes
├── static/
│   ├── session_map.js               # Frontend JavaScript
│   └── session_map.css              # Frontend styles
├── templates/
│   └── session_map.html             # Web UI HTML
├── root_agent/
│   └── root_agent.py                # Add _maybe_extract_session_map()
├── server.py                        # Add register_session_map blueprint
└── tools/
    └── read_session_map.py          # LLM tool (Phase 5)
```

---

## Implementation Phases

### Phase 1: Core Models and Storage
- [ ] Create `src/wichy/session_map/` directory
- [ ] Implement `models.py` (Node, Edge, SessionMap)
- [ ] Implement `store.py` (SessionMapStore with SQLite)
- [ ] Add settings to `config/settings.py`
- [ ] Write unit tests for models and store

### Phase 2: Extraction Logic
- [ ] Implement `extractor.py` (EXTRACTION_PROMPT, VALIDATION_PROMPT)
- [ ] Implement `validation.py` (validation helpers)
- [ ] Write unit tests for extraction and validation
- [ ] Test with various conversation types

### Phase 3: RootAgent Integration
- [ ] Modify `root_agent.py` to add extraction trigger
- [ ] Implement `_get_user_turn_count()`, `_get_messages_since_turn()`
- [ ] Implement `_maybe_extract_session_map()`
- [ ] Test extraction timing (every N turns)

### Phase 4: API and Web GUI
- [ ] Implement `api.py` (all routes)
- [ ] Create `session_map.html` template
- [ ] Create `session_map.js` frontend
- [ ] Create `session_map.css` styles
- [ ] Register blueprint in `server.py`
- [ ] Link from landing page

### Phase 5: LLM Tool
- [ ] Create `read_session_map.py` tool
- [ ] Register tool in `tools/__init__.py`
- [ ] Test tool with LLM context
- [ ] Document tool usage

### Phase 6: Polish and Testing
- [ ] End-to-end testing
- [ ] Performance testing (large maps)
- [ ] Manual deletion in GUI
- [ ] Error handling robustness
- [ ] Documentation

---

## Future Enhancements

1. **Manual node editing** - Allow editing node content
2. **Node connections via GUI** - Drag to connect nodes
3. **Search/filter** - Search nodes by content
4. **Export** - Export map as JSON or image
5. **History** - View previous versions of the map
6. **Merge LLM** - Add merge-step LLM for deduplication if needed
7. **Context resume** - Load session map when resuming a conversation

---

## Notes

- **Deduplication**: Currently trusts extraction LLM + validation. If duplicates are a problem in practice, add merge-step LLM.
- **Performance**: SQLite handles maps well. For very large sessions, consider pagination or summaries.
- **Extraction timing**: Default 10 turns is configurable. May want adaptive intervals (shorter early, longer later).
- **Model choice**: Extraction can use a smaller/faster model if quality is acceptable.