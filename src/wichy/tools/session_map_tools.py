"""
Session map tools for reading investigation progress.

This module provides tools for the LLM agent to read session maps,
which track investigation progress, questions, findings, and decisions.
"""

from typing import Literal, Optional

from pydantic import Field

from wichy.session_map.store import SessionMapStore
from wichy.session_map.models import Node, SessionMap
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error

# --- Module-level globals ---

_session_map_store: SessionMapStore | None = None
_context_handler = None


def set_session_map_globals(store: SessionMapStore | None, context_handler) -> None:
    """Set global references for session map tools.

    Called by root_agent when initializing session map feature.

    Args:
        store: The SessionMapStore instance for persisting session maps
        context_handler: The ContextHandler instance for accessing current context
    """
    global _session_map_store, _context_handler
    _session_map_store = store
    _context_handler = context_handler


# --- Parameters model ---


class ReadSessionMapParameters(ParametersModel):
    """Parameters for read_session_map tool."""

    node_types: Optional[list[str]] = Field(
        None,
        description="Filter to specific node types. Valid types: 'question', 'finding', 'decision', 'file', 'dead_end', 'note'. If not provided, returns all types.",
    )
    detail: Literal["quick", "full"] = Field(
        "quick",
        description="Output detail level. 'quick' returns summary counts. 'full' returns complete node content and edges.",
    )
    limit: int = Field(
        100,
        description="Maximum number of nodes to return. Default 100. Use to control output size.",
        ge=1,
        le=500,
    )

    def info(self) -> str:
        """Generate a human-readable string of the parameters."""
        parts = []
        if self.node_types:
            parts.append(f"types={self.node_types}")
        parts.append(f"detail={self.detail}")
        parts.append(f"limit={self.limit}")
        return " ".join(parts)


# --- Tool implementation ---


class ReadSessionMapTool(BaseTool):
    """Tool to read the current session map showing investigation progress."""

    name = "read_session_map"
    description = "Read the current session map showing investigation progress, questions, findings, and decisions"
    description_long = """Read the session map to see the investigation progress.

A session map tracks:
- Questions asked during the investigation
- Findings and discoveries
- Decisions made
- Files explored
- Dead ends encountered

Use this to:
1. Review what has been discovered so far
2. See relationships between findings
3. Understand the investigation timeline
4. Check if a topic has already been explored

Parameters:
- node_types: Filter to specific types ('question', 'finding', 'decision', 'file', 'dead_end', 'note')
- detail: 'quick' for summary with counts, 'full' for complete content with edges
- limit: Max nodes to return (default 100)"""

    parameters_model = ReadSessionMapParameters

    # Valid node types for validation
    VALID_NODE_TYPES = {"question", "finding", "decision", "file", "dead_end", "note"}

    def execute(
        self,
        node_types: Optional[list[str]] = None,
        detail: str = "quick",
        limit: int = 100,
    ) -> str:
        """Execute the read_session_map tool.

        Args:
            node_types: Filter to specific node types
            detail: Output detail level ('quick' or 'full')
            limit: Maximum number of nodes to return

        Returns:
            Formatted session map output or error message
        """
        # Check if globals are initialized
        if _session_map_store is None or _context_handler is None:
            return format_error(
                "Session map feature not initialized. Start wichy with --session-map flag."
            )

        # Validate node types
        if node_types:
            node_types_lower = [t.lower() for t in node_types]
            invalid = set(node_types_lower) - self.VALID_NODE_TYPES
            if invalid:
                return format_error(
                    f"Invalid node types: {invalid}. Valid types are: {self.VALID_NODE_TYPES}"
                )

        # Get context ID and session map
        try:
            context_id = str(_context_handler.path)
            session_map = _session_map_store.get(context_id)

            if session_map is None:
                return format_error("No session map found for current context.")

        except Exception as e:
            return format_error(f"Failed to retrieve session map: {e}")

        # Filter nodes by type
        if node_types:
            node_types_lower = [t.lower() for t in node_types]
            filtered_nodes = [
                n for n in session_map.nodes if n.type.value in node_types_lower
            ]
        else:
            filtered_nodes = session_map.nodes

        # Apply limit
        limited = len(filtered_nodes) > limit
        nodes = filtered_nodes[:limit]

        # Format output based on detail level
        if detail == "quick":
            return self._format_quick(session_map, nodes, limited, limit)
        else:
            return self._format_full(session_map, nodes, limited, limit)

    def _format_quick(
        self, session_map: SessionMap, nodes: list[Node], limited: bool, limit: int
    ) -> str:
        """Format session map as quick summary with counts.

        Args:
            session_map: The full session map
            nodes: Filtered and limited nodes
            limited: Whether output was limited
            limit: The limit value used

        Returns:
            Formatted quick summary string
        """
        lines = ["# Session Map Summary"]

        # Total counts
        total_nodes = len(session_map.nodes)
        total_edges = len(session_map.edges)
        lines.append(f"\nTotal nodes: {total_nodes}, Total edges: {total_edges}")
        lines.append(f"Last extracted turn: {session_map.last_extracted_turn}")

        # Node counts by type
        lines.append("\n## Node Counts by Type")
        type_counts: dict[str, int] = {}
        for node in session_map.nodes:
            type_counts[node.type.value] = type_counts.get(node.type.value, 0) + 1

        for node_type in [
            "question",
            "finding",
            "decision",
            "file",
            "dead_end",
            "note",
        ]:
            count = type_counts.get(node_type, 0)
            lines.append(f"  - {node_type}: {count}")

        # List filtered nodes
        lines.append(f"\n## Nodes (filtered, showing {len(nodes)} of {total_nodes})")
        for node in nodes:
            # Truncate content for quick view
            content = node.content[:80]
            if len(node.content) > 80:
                content += "..."
            lines.append(f"- [{node.type.value}] [{node.id}] {content}")

        # Add truncation notice
        if limited:
            lines.append(
                f"\n---\n(Limited to {limit} nodes. Use higher limit or filter by node_types to see more.)"
            )

        return "\n".join(lines)

    def _format_full(
        self, session_map: SessionMap, nodes: list[Node], limited: bool, limit: int
    ) -> str:
        """Format session map with full details including edges.

        Args:
            session_map: The full session map
            nodes: Filtered and limited nodes
            limited: Whether output was limited
            limit: The limit value used

        Returns:
            Formatted full details string
        """
        lines = ["# Session Map (Full Details)"]

        # Context info
        lines.append(f"\nContext: {session_map.context_id}")
        lines.append(
            f"Total nodes: {len(session_map.nodes)}, Total edges: {len(session_map.edges)}"
        )
        lines.append(f"Last extracted turn: {session_map.last_extracted_turn}")

        # Build node lookup for edge resolution
        node_map = {n.id: n for n in session_map.nodes}
        filtered_ids = {n.id for n in nodes}

        # Filter edges to only those involving filtered nodes
        relevant_edges = [
            e
            for e in session_map.edges
            if e.from_id in filtered_ids or e.to_id in filtered_ids
        ]

        # List nodes with full details
        lines.append(f"\n## Nodes ({len(nodes)})")

        for node in nodes:
            lines.append(f"\n### [{node.id}] {node.type.value}")
            lines.append(f"Turn: {node.turn}")
            lines.append(f"Content: {node.content}")
            if node.source_msg_idx is not None:
                lines.append(f"Source message index: {node.source_msg_idx}")

            # Find connected nodes
            connected_to = []
            for edge in relevant_edges:
                if edge.from_id == node.id:
                    target = node_map.get(edge.to_id, None)
                    if target:
                        connected_to.append(f"[{target.id}]")
                elif edge.to_id == node.id:
                    source = node_map.get(edge.from_id, None)
                    if source:
                        connected_to.append(f"[{source.id}]")

            if connected_to:
                lines.append(f"Connects to: {', '.join(connected_to)}")

        # List edges
        lines.append("\n## Edges")
        for edge in relevant_edges:
            from_node = node_map.get(edge.from_id)
            to_node = node_map.get(edge.to_id)

            from_content = from_node.content[:50] if from_node else edge.from_id
            if from_node and len(from_node.content) > 50:
                from_content += "..."
            to_content = to_node.content[:50] if to_node else edge.to_id
            if to_node and len(to_node.content) > 50:
                to_content += "..."

            lines.append(f"- {from_content}")
            lines.append(f"  --[{edge.type.value}]-->")
            lines.append(f"  {to_content}")

        # Add truncation notice
        if limited:
            lines.append(
                f"\n---\n(Showing {len(nodes)} nodes. Use limit parameter to see more.)"
            )

        return "\n".join(lines)
