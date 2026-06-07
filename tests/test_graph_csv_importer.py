"""Tests for the CSV graph importer."""

import pytest
from wichy.tools.graph.csv_importer import (
    parse_csv_text,
    import_graph_from_csv,
    CSVParseError,
    NodeCapError,
    MAX_NODES,
    AUTO_COLORS,
)

SAMPLE_CSV = """source,target,relation
Alice,Bob,knows
Bob,Charlie,reports_to
Alice,Charlie,works_with"""


# ─── parse_csv_text ───


def test_parse_csv_preview():
    result = parse_csv_text(SAMPLE_CSV)
    assert result["columns"] == ["source", "target", "relation"]
    assert result["row_count"] == 3
    assert len(result["preview"]) == 3
    assert result["preview"][0]["source"] == "Alice"


def test_parse_csv_empty_raises():
    with pytest.raises(CSVParseError):
        parse_csv_text("")


# ─── import_graph_from_csv ───


def test_import_basic():
    result = import_graph_from_csv(
        SAMPLE_CSV, source_col="source", target_col="target"
    )
    assert result["status"] == "ok"
    assert result["nodes_created"] == 3  # Alice, Bob, Charlie
    assert result["edges_created"] == 3
    node_ids = {n["id"] for n in result["nodes"]}
    assert node_ids == {"Alice", "Bob", "Charlie"}


def test_import_with_edge_label():
    result = import_graph_from_csv(
        SAMPLE_CSV,
        source_col="source",
        target_col="target",
        edge_label_col="relation",
    )
    labels = [e["label"] for e in result["edges"]]
    assert "knows" in labels
    assert "reports_to" in labels


def test_import_with_group_col():
    csv = "s,t,g\nAlice,Bob,TeamA\nCharlie,Dave,TeamB"
    result = import_graph_from_csv(
        csv, source_col="s", target_col="t", group_col="g"
    )
    groups = {n["id"]: n["group"] for n in result["nodes"]}
    assert groups["Alice"] == "TeamA"
    assert groups["Charlie"] == "TeamB"
    colors = {n["id"]: n["color"] for n in result["nodes"]}
    assert colors["Alice"] != colors["Charlie"]
    assert colors["Alice"] in AUTO_COLORS


def test_import_with_color_col_valid_hex():
    csv = "s,t,c\nAlice,Bob,#FF0000\nCharlie,Dave,#00FF00"
    result = import_graph_from_csv(
        csv, source_col="s", target_col="t", color_col="c"
    )
    colors = {n["id"]: n["color"] for n in result["nodes"]}
    assert colors["Alice"] == "#FF0000"
    assert colors["Charlie"] == "#00FF00"


def test_import_with_color_col_invalid_hex_fallback():
    csv = "s,t,c\nAlice,Bob,RED\nCharlie,Dave,#00FF00"
    result = import_graph_from_csv(
        csv, source_col="s", target_col="t", color_col="c"
    )
    colors = {n["id"]: n["color"] for n in result["nodes"]}
    assert colors["Alice"] == AUTO_COLORS[0]  # falls to default
    assert colors["Charlie"] == "#00FF00"


# ─── Invariants ───


def test_self_loop_allowed():
    r = import_graph_from_csv("s,t\nAlice,Alice", source_col="s", target_col="t")
    assert r["edges_created"] == 1
    assert r["edges"][0]["from"] == "Alice"
    assert r["edges"][0]["to"] == "Alice"


def test_multi_edge_accumulates():
    r = import_graph_from_csv(
        "s,t\nAlice,Bob\nAlice,Bob", source_col="s", target_col="t"
    )
    assert r["edges_created"] == 2
    assert r["nodes_created"] == 2
    edge_ids = {e["id"] for e in r["edges"]}
    assert len(edge_ids) == 2  # each edge has unique id


def test_first_occurrence_wins():
    csv = "s,t,g\nAlice,Bob,TeamA\nAlice,Bob,TeamB"
    r = import_graph_from_csv(
        csv, source_col="s", target_col="t", group_col="g"
    )
    alice = next(n for n in r["nodes"] if n["id"] == "Alice")
    assert alice["group"] == "TeamA"  # first row wins
    bob = next(n for n in r["nodes"] if n["id"] == "Bob")
    assert bob["group"] == "TeamA"


def test_node_cap_raises():
    # 501 unique sources over a shared target = 501 + 1 = 502 nodes, hits cap
    rows = "s,t\n" + "\n".join(f"n{i},shared" for i in range(501))
    with pytest.raises(NodeCapError) as exc_info:
        import_graph_from_csv(rows, source_col="s", target_col="t")
    assert "502 nodes" in str(exc_info.value)
    assert str(MAX_NODES) in str(exc_info.value)


def test_node_cap_exact_boundary_allowed():
    # 499 unique sources over a shared target = 499 + 1 = 500 nodes (exactly the cap)
    rows = "s,t\n" + "\n".join(f"n{i},shared" for i in range(499))
    r = import_graph_from_csv(rows, source_col="s", target_col="t")
    assert r["nodes_created"] == 500
    assert r["status"] == "ok"


# ─── Edge Cases ───


def test_empty_values_skipped():
    # Empty source or target value for a row should be skipped
    csv = "s,t\nAlice,Bob\n,Bob\nAlice,"
    r = import_graph_from_csv(csv, source_col="s", target_col="t")
    assert r["nodes_created"] == 2
    assert r["edges_created"] == 1


def test_drop_excess_rows():
    # CSV > 10 rows: preview only returns first 10, import processes all
    rows = [f"n{i},{i%3}" for i in range(20)]
    csv_text = "s,t\n" + "\n".join(rows)
    preview = parse_csv_text(csv_text)
    assert preview["row_count"] == 20
    assert len(preview["preview"]) == 10
    result = import_graph_from_csv(csv_text, source_col="s", target_col="t")
    assert result["edges_created"] == 20  # all rows processed, not just preview
