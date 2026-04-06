"""Data models for session map feature."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class NodeType(Enum):
    """Type of node in the session map."""

    QUESTION = "question"  # Questions asked during investigation
    FINDING = "finding"  # Facts/discoveries found
    DECISION = "decision"  # Choices made
    FILE = "file"  # Files explored (with brief summary)
    DEAD_END = "dead_end"  # Abandoned paths / wrong turns
    NOTE = "note"  # User-added notes


class EdgeType(Enum):
    """Type of connection between nodes."""

    LED_TO = "led_to"  # Question led to finding
    ANSWERED_BY = "answered_by"  # Question answered by finding
    EXPLORED = "explored"  # Finding came from file exploration
    RULED_OUT = "ruled_out"  # Decision ruled out a path
    RELATED = "related"  # General relationship
    FOLLOWS = "follows"  # Temporal sequence


@dataclass
class Node:
    """A node in the session map representing a question, finding, decision, etc."""

    id: str
    type: NodeType
    content: str
    created_at: datetime
    turn: int
    source_msg_idx: int | None = None
    connects_to: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize node to dictionary."""
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
        """Deserialize node from dictionary."""
        try:
            return cls(
                id=data["id"],
                type=NodeType(data["type"]),
                content=data["content"],
                created_at=datetime.fromisoformat(data["created_at"]),
                turn=data["turn"],
                source_msg_idx=data.get("source_msg_idx"),
                connects_to=data.get("connects_to", []),
            )
        except KeyError as e:
            raise KeyError(f"Missing required field for Node: {e}") from e
        except ValueError as e:
            raise ValueError(f"Invalid value for Node: {e}") from e


@dataclass
class Edge:
    """An edge connecting two nodes in the session map."""

    from_id: str
    to_id: str
    type: EdgeType

    def to_dict(self) -> dict:
        """Serialize edge to dictionary."""
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Edge":
        """Deserialize edge from dictionary."""
        try:
            return cls(
                from_id=data["from"],
                to_id=data["to"],
                type=EdgeType(data["type"]),
            )
        except KeyError as e:
            raise KeyError(f"Missing required field for Edge: {e}") from e
        except ValueError as e:
            raise ValueError(f"Invalid value for Edge: {e}") from e


@dataclass
class SessionMap:
    """A map of the investigation session showing questions, findings, decisions."""

    context_id: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    last_extracted_turn: int = 0
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        """Serialize session map to dictionary."""
        return {
            "context_id": self.context_id,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "last_extracted_turn": self.last_extracted_turn,
            "updated_at": self.updated_at.isoformat(),
        }

    def to_json(self) -> str:
        """Serialize session map to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMap":
        """Deserialize session map from dictionary."""
        try:
            return cls(
                context_id=data["context_id"],
                nodes=[Node.from_dict(n) for n in data.get("nodes", [])],
                edges=[Edge.from_dict(e) for e in data.get("edges", [])],
                last_extracted_turn=data.get("last_extracted_turn", 0),
                updated_at=(
                    datetime.fromisoformat(data["updated_at"])
                    if "updated_at" in data
                    else datetime.now()
                ),
            )
        except KeyError as e:
            raise KeyError(f"Missing required field for SessionMap: {e}") from e

    @classmethod
    def from_json(cls, json_str: str) -> "SessionMap":
        """Deserialize session map from JSON string."""
        return cls.from_dict(json.loads(json_str))

    def get_summary(self, max_nodes: int = 20, max_content_len: int = 100) -> str:
        """Generate a text summary of the session map for extraction LLM."""
        if not self.nodes:
            return "Empty session map - no nodes yet."

        lines = []

        # Group nodes by type
        nodes_by_type: dict[NodeType, list[Node]] = {}
        for node in self.nodes:
            if node.type not in nodes_by_type:
                nodes_by_type[node.type] = []
            nodes_by_type[node.type].append(node)

        # Build summary by type
        for node_type in NodeType:
            if node_type in nodes_by_type:
                type_nodes = nodes_by_type[node_type][:max_nodes]
                lines.append(f"\n{node_type.name}:")
                for node in type_nodes:
                    content = node.content[:max_content_len]
                    if len(node.content) > max_content_len:
                        content += "..."
                    lines.append(f"  - [{node.id}] {content}")

        return "\n".join(lines)


def generate_node_id() -> str:
    """Generate a unique node ID."""
    return f"node_{uuid.uuid4().hex[:8]}"
