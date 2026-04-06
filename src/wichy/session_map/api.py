"""Flask API routes for session map."""

from flask import Blueprint, jsonify, request

from .store import SessionMapStore
from .models import NodeType
from wichy.config import settings

# Global references (set by main initialization)
_session_map_store: SessionMapStore | None = None
_context_handler = None  # Will be ContextHandler
_session_map_model_str: str | None = None  # Model string for extraction


def set_session_map_store(store: SessionMapStore):
    """Set the session map store instance."""
    global _session_map_store
    _session_map_store = store


def set_context_handler(ctx):
    """Set the context handler instance."""
    global _context_handler
    _context_handler = ctx


def set_session_map_model_str(model_str: str | None):
    """Set the model string for session map extraction."""
    global _session_map_model_str
    _session_map_model_str = model_str


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
            return jsonify(
                {
                    "nodes": [],
                    "edges": [],
                    "last_extracted_turn": 0,
                    "updated_at": None,
                }
            )

        return jsonify(session_map.to_dict())

    @bp.route("/api/status", methods=["GET"])
    def get_status():
        """Get extraction status."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500

        context_id = str(_context_handler.path)

        # Get current turn count
        user_turns = len(
            [m for m in _context_handler.context if m.get("role") == "user"]
        )
        last_extracted = _session_map_store.get_last_turn(context_id)

        turns_since = (user_turns - last_extracted) % settings.session_map_interval
        return jsonify(
            {
                "current_turn": user_turns,
                "last_extracted_turn": last_extracted,
                "next_extraction_in": (
                    0
                    if turns_since == 0
                    else settings.session_map_interval - turns_since
                ),
                "enabled": True,
            }
        )

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
        current_turn = len(
            [m for m in _context_handler.context if m.get("role") == "user"]
        )

        parent_ids = data.get("parent_ids", [])

        node = _session_map_store.add_manual_node(
            context_id=context_id,
            node_type=node_type,
            content=content,
            turn=current_turn,
            parent_ids=parent_ids,
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

        if _session_map_model_str is None:
            return jsonify({"error": "Session map not enabled"}), 400

        from .extractor import SessionMapExtractor

        context_id = str(_context_handler.path)
        last_turn = _session_map_store.get_last_turn(context_id)

        # Get messages since last extraction
        messages = _context_handler.context[last_turn:]

        # Get existing map
        existing_map = _session_map_store.get(context_id)

        # Extract - use the model_str that was set during initialization
        extractor = SessionMapExtractor(model_str=_session_map_model_str)
        current_turn = len(
            [m for m in _context_handler.context if m.get("role") == "user"]
        )

        is_valid, nodes, edges, feedback = extractor.extract_with_validation(
            messages=messages,
            existing_map=existing_map,
            start_turn=last_turn,
        )

        if is_valid and nodes:
            _session_map_store.merge_nodes(context_id, nodes, edges, current_turn)

        return jsonify(
            {
                "success": is_valid,
                "nodes_added": len(nodes),
                "edges_added": len(edges),
                "feedback": feedback,
            }
        )

    @bp.route("/api/clear", methods=["POST"])
    def clear_map():
        """Clear the session map."""
        if _session_map_store is None or _context_handler is None:
            return jsonify({"error": "Not initialized"}), 500

        context_id = str(_context_handler.path)
        _session_map_store.clear(context_id)

        return jsonify({"success": True})
