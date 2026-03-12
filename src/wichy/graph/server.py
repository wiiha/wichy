import json
import os
import sys
from datetime import datetime

from flask import Flask, jsonify, render_template, request, send_from_directory

app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)


def get_graphs_dir():
    """Get the graphs directory relative to workspace."""
    workspace = os.getcwd()
    graphs_dir = os.path.join(workspace, ".wichy", "graphs")
    os.makedirs(graphs_dir, exist_ok=True)
    return graphs_dir


@app.route("/static/<path:filename>")
def serve_static(filename):
    """Serve static files (vis.js, etc.)."""
    return send_from_directory(app.static_folder, filename)


@app.route("/graph")
def graph_editor():
    """Serve the graph editor HTML page."""
    return render_template("editor.html")


@app.route("/graph/list")
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


@app.route("/graph/load/<filename>")
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


@app.route("/graph/save", methods=["POST"])
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


def run_server(port=7891):
    """Run the Flask development server."""
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_server()
