"""CSV import logic for the Graph Editor."""

import csv
import io

AUTO_COLORS = [
    "#4f46e5",
    "#16a34a",
    "#dc2626",
    "#f59e0b",
    "#06b6d4",
    "#8b5cf6",
    "#ec4899",
    "#84cc16",
    "#f97316",
    "#14b8a6",
    "#6366f1",
    "#e11d48",
]

MAX_NODES = 500


class CSVParseError(Exception):
    """Raised when CSV text cannot be parsed."""

    pass


class NodeCapError(Exception):
    """Raised when import would exceed MAX_NODES."""

    message = ""
    node_count = 0

    def __init__(self, node_count):
        self.message = (
            f"Import would create {node_count} nodes. Max allowed: {MAX_NODES}."
        )
        self.node_count = node_count
        super().__init__(self.message)


def parse_csv_text(csv_text):
    """Parse CSV text and return preview data.

    Returns:
        {
            "columns": [str, ...],
            "row_count": int,
            "preview": [{col: val, ...}, ...]  # first 10 rows
        }

    Raises:
        CSVParseError: if parsing fails.
    """
    try:
        raw = csv_text[:1000] if len(csv_text) > 1000 else csv_text
        dialect = csv.Sniffer().sniff(raw, delimiters=",;\t")
        reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
        columns = reader.fieldnames or []
        rows = list(reader)
        row_count = len(rows)
        preview = [dict(r) for r in rows[:10]]
        return {
            "columns": columns,
            "row_count": row_count,
            "preview": preview,
        }
    except csv.Error as exc:
        raise CSVParseError(f"Could not parse CSV. {exc}") from exc
    except Exception as exc:
        raise CSVParseError(f"Could not parse CSV. {exc}") from exc


def _is_hex_color(value):
    """Return True if value looks like a hex color."""
    return isinstance(value, str) and value.startswith("#") and len(value) == 7


def _get_remaining_rows(csv_text, preview_data):
    """Return all rows after the first 10."""
    try:
        raw = csv_text[:1000] if len(csv_text) > 1000 else csv_text
        dialect = csv.Sniffer().sniff(raw, delimiters=",;\t")
    except (csv.Error, Exception):
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(csv_text), dialect=dialect)
    rows = list(reader)
    return rows[10:]


def import_graph_from_csv(
    csv_text,
    source_col,
    target_col,
    edge_label_col=None,
    group_col=None,
    color_col=None,
):
    """Generate graph data {nodes: [...], edges: [...]} from CSV text.

    Args:
        csv_text: Raw CSV text to parse.
        source_col: Column name for source node values.
        target_col: Column name for target node values.
        edge_label_col: Optional column name for edge labels.
        group_col: Optional column name for node.group values.
        color_col: Optional column name for node.color values.

    Returns:
        {"status": "ok", "nodes": [...], "edges": [...], ...}

    Raises:
        CSVParseError: if CSV cannot be parsed.
        ValueError: if source_col or target_col is not in columns.
        NodeCapError: if unique nodes would exceed MAX_NODES.
    """
    if not source_col or not target_col:
        raise ValueError("source_col and target_col are required")

    preview = parse_csv_text(csv_text)
    columns = preview["columns"]
    rows = preview["preview"] + _get_remaining_rows(csv_text, preview)

    if source_col not in columns or target_col not in columns:
        raise ValueError(
            f"source_col={source_col} or target_col={target_col} not in columns={columns}"
        )

    nodes = {}
    edges = []
    group_colors = {}
    color_idx = 0

    for idx, row in enumerate(rows):
        source_val = row.get(source_col, "").strip()
        target_val = row.get(target_col, "").strip()
        if not source_val or not target_val:
            continue

        # --- Create / reuse source node ---
        if source_val not in nodes:
            if len(nodes) >= MAX_NODES:
                raise NodeCapError(len(nodes) + 1)
            group_val = row.get(group_col, "") if group_col else ""
            color_val = ""
            if color_col and _is_hex_color(row.get(color_col, "")):
                color_val = row[color_col]
            elif group_col and group_val:
                if group_val not in group_colors:
                    group_colors[group_val] = AUTO_COLORS[color_idx % len(AUTO_COLORS)]
                    color_idx += 1
                color_val = group_colors[group_val]
            else:
                color_val = AUTO_COLORS[0]
            nodes[source_val] = {
                "id": source_val,
                "label": source_val,
                "group": group_val or "default",
                "color": color_val,
                "shape": "dot",
                "size": 25,
            }

        # --- Create / reuse target node ---
        if target_val not in nodes:
            if len(nodes) >= MAX_NODES:
                raise NodeCapError(len(nodes) + 1)
            group_val = row.get(group_col, "") if group_col else ""
            color_val = ""
            if color_col and _is_hex_color(row.get(color_col, "")):
                color_val = row[color_col]
            elif group_col and group_val:
                if group_val not in group_colors:
                    group_colors[group_val] = AUTO_COLORS[color_idx % len(AUTO_COLORS)]
                    color_idx += 1
                color_val = group_colors[group_val]
            else:
                color_val = AUTO_COLORS[0]
            nodes[target_val] = {
                "id": target_val,
                "label": target_val,
                "group": group_val or "default",
                "color": color_val,
                "shape": "dot",
                "size": 25,
            }

        # --- Edge ---
        label = ""
        if edge_label_col and edge_label_col in row:
            label = row[edge_label_col]
        edges.append(
            {
                "id": f"csv-edge-{idx}",
                "from": source_val,
                "to": target_val,
                "label": label,
                "arrows": "to",
            }
        )

    return {
        "status": "ok",
        "nodes": list(nodes.values()),
        "edges": edges,
        "nodes_created": len(nodes),
        "edges_created": len(edges),
    }
