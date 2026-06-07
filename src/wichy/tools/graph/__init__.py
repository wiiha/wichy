"""
Graph tool Blueprint for the central Flask server.

This module provides the web UI for graph editing via a Flask Blueprint.
The agent tools (create_graph, read_graph, list_graphs) remain in graph_tools.py.
"""

import json
from datetime import datetime
import os

from flask import Blueprint, jsonify, render_template, request, send_from_directory

from wichy.config import settings

# Create the blueprint with URL prefix
bp = Blueprint("graph", __name__, url_prefix="/tools/graph")

# Static files and templates are in the original graph module
GRAPH_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAPH_STATIC = os.path.join(GRAPH_DIR, "graph", "static")
GRAPH_TEMPLATES = os.path.join(GRAPH_DIR, "graph", "templates")


def get_graphs_dir():
    """Get the graphs directory relative to workspace."""
    graphs_dir = settings.graphs_dir
    graphs_dir.mkdir(parents=True, exist_ok=True)
    return str(graphs_dir)


@bp.route("/static/<path:filename>")
def serve_static(filename):
    """Serve static files (vis.js, etc.)."""
    return send_from_directory(GRAPH_STATIC, filename)


@bp.route("/")
def graph_editor():
    """Serve the graph editor HTML page."""
    return render_template("graph_editor.html")


@bp.route("/api/list")
def list_graphs():
    """List all available graph files."""
    try:
        graphs_dir = get_graphs_dir()
        files = []
        if os.path.exists(graphs_dir):
            for f in sorted(os.listdir(graphs_dir), reverse=True):
                if f.endswith(".json") and os.path.isfile(os.path.join(graphs_dir, f)):
                    filepath = os.path.join(graphs_dir, f)
                    stat = os.stat(filepath)
                    files.append(
                        {"filename": f, "size": stat.st_size, "modified": stat.st_mtime}
                    )
        return jsonify({"graphs": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/load/<filename>")
def load_graph(filename):
    """Load a specific graph file."""
    try:
        graphs_dir = get_graphs_dir()
        filepath = os.path.join(graphs_dir, filename)

        if not os.path.exists(filepath):
            return jsonify({"error": "File not found"}), 404

        with open(filepath, "r") as f:
            data = json.load(f)

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/save", methods=["POST"])
def save_graph():
    """Save graph data to JSON file."""
    try:
        data = request.get_json()
        if not data or "nodes" not in data or "edges" not in data:
            return (
                json.dumps(
                    {
                        "status": "error",
                        "message": "Invalid data format. Expected { nodes: [], edges: [] }",
                    }
                ),
                400,
            )

        graphs_dir = get_graphs_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"graph_{timestamp}.json"
        filepath = os.path.join(graphs_dir, filename)

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        # Also update latest.json
        latest_path = os.path.join(graphs_dir, "latest.json")
        with open(latest_path, "w") as f:
            json.dump(data, f, indent=2)

        return json.dumps(
            {
                "status": "ok",
                "file": os.path.relpath(filepath, os.getcwd()),
                "timestamp": timestamp,
            }
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}), 500



# ——— CSV Import Routes ———


@bp.route("/api/import-csv/preview", methods=["POST"])
def import_csv_preview():
    """Preview CSV: return columns + first 10 rows."""
    from .csv_importer import parse_csv_text, CSVParseError

    try:
        data = request.get_json()
        csv_text = data.get("csv_text", "") if data else ""
        if not csv_text:
            return jsonify({"error": "No csv_text provided"}), 400
        result = parse_csv_text(csv_text)
        return jsonify(result)
    except CSVParseError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/api/import-csv", methods=["POST"])
def import_csv():
    """Import CSV into graph and save."""
    from .csv_importer import (
        import_graph_from_csv,
        CSVParseError,
        NodeCapError,
    )

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        csv_text = data.get("csv_text", "")
        source_col = data.get("source_col", "")
        target_col = data.get("target_col", "")
        edge_label_col = data.get("edge_label_col") or None
        group_col = data.get("group_col") or None
        color_col = data.get("color_col") or None

        if not csv_text:
            return jsonify({"error": "No csv_text provided"}), 400
        if not source_col or not target_col:
            return jsonify({"error": "source_col and target_col are required"}), 400

        result = import_graph_from_csv(
            csv_text,
            source_col=source_col,
            target_col=target_col,
            edge_label_col=edge_label_col,
            group_col=group_col,
            color_col=color_col,
        )

        # Re-use existing save logic
        graph_data = {"nodes": result["nodes"], "edges": result["edges"]}

        graphs_dir = get_graphs_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"graph_{timestamp}.json"
        filepath = os.path.join(graphs_dir, filename)

        with open(filepath, "w") as f:
            json.dump(graph_data, f, indent=2)

        latest_path = os.path.join(graphs_dir, "latest.json")
        with open(latest_path, "w") as f:
            json.dump(graph_data, f, indent=2)

        return jsonify(
            {
                "status": "ok",
                "filename": filename,
                "nodes_created": result["nodes_created"],
                "edges_created": result["edges_created"],
            }
        )
    except CSVParseError as exc:
        return jsonify({"error": str(exc)}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except NodeCapError as exc:
        return jsonify({"error": exc.message}), 413
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def register(app):
    """Register the graph blueprint with the Flask app."""
    app.register_blueprint(bp)
