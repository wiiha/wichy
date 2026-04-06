"""Validation utilities for session map feature."""

from dataclasses import dataclass


@dataclass
class ValidationResult:
    """Result of validating a session map."""

    is_valid: bool
    nodes: list[dict]
    edges: list[dict]
    feedback: str | None = None


def validate_node_types(nodes: list[dict], valid_types: set[str]) -> list[str]:
    """Check that all node types are valid. Returns list of issues."""
    issues = []
    for node in nodes:
        node_type = node.get("type")
        if node_type is None:
            issues.append(f"Node missing 'type' field: {node}")
        elif node_type not in valid_types:
            issues.append(
                f"Node has invalid type '{node_type}'. Valid types are: {valid_types}"
            )
    return issues


def validate_edge_types(edges: list[dict], valid_types: set[str]) -> list[str]:
    """Check that all edge types are valid. Returns list of issues."""
    issues = []
    for edge in edges:
        edge_type = edge.get("type")
        if edge_type is None:
            issues.append(f"Edge missing 'type' field: {edge}")
        elif edge_type not in valid_types:
            issues.append(
                f"Edge has invalid type '{edge_type}'. Valid types are: {valid_types}"
            )
    return issues


def validate_references(edges: list[dict], node_ids: set[str]) -> list[str]:
    """Check that all edge references point to valid node IDs. Returns list of issues."""
    issues = []
    for edge in edges:
        from_id = edge.get("from")
        to_id = edge.get("to")
        if from_id is not None and from_id not in node_ids:
            issues.append(f"Edge references non-existent 'from' node: '{from_id}'")
        if to_id is not None and to_id not in node_ids:
            issues.append(f"Edge references non-existent 'to' node: '{to_id}'")
    return issues
