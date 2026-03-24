"""API endpoints for context editor."""

from flask import request, jsonify, Blueprint

# Global reference to active context handler (set by __main__)
_active_context = None  # Will be ContextHandler

# Global reference to active root agent (set by __main__)
_active_root_agent = None  # Will be RootAgent


def set_active_context(ctx):
    """Set the currently active context handler for the web editor to manipulate."""
    global _active_context
    _active_context = ctx


def set_active_root_agent(root_agent):
    """Set the currently active root agent for the web editor to expose token state."""
    global _active_root_agent
    _active_root_agent = root_agent


def register_routes(bp: Blueprint):
    """Register all API routes on the given blueprint."""

    @bp.route("/api/status")
    def status():
        """Get status of the current context."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        ctx = _active_context
        root_agent = _active_root_agent
        return jsonify(
            {
                "filename": ctx._path.name if ctx._path else None,
                "message_count": len(ctx.context),
                "log_count": len(ctx.logs),
                "mtime": ctx._file_mtime,
                "path": str(ctx._path) if ctx._path else None,
                "current_prompt_tokens": (
                    root_agent.current_prompt_tokens
                    if root_agent is not None
                    else None
                ),
                "auto_compact_threshold": (
                    root_agent.auto_compact_threshold
                    if root_agent is not None
                    else None
                ),
            }
        )

    @bp.route("/api/messages")
    def get_messages():
        """Get all messages in the context."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        return jsonify(_active_context.context)

    @bp.route("/api/messages", methods=["PUT"])
    def replace_messages():
        """Replace all messages (atomic bulk replace)."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        data = request.get_json()
        if not isinstance(data, list):
            return jsonify({"error": "Expected list of message objects"}), 400

        # Validate messages have role and content
        for msg in data:
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                return (
                    jsonify({"error": "Each message must have 'role' and 'content'"}),
                    400,
                )

        try:
            _active_context.replace_all(data)
            return jsonify(
                {"success": True, "message_count": len(_active_context.context)}
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/messages", methods=["POST"])
    def append_message():
        """Append a single message."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        data = request.get_json()
        if not isinstance(data, dict) or "role" not in data or "content" not in data:
            return jsonify({"error": "Message must have 'role' and 'content'"}), 400

        # Append to context handler (which persists to file)
        _active_context.append(data)
        return jsonify({"success": True, "message": data}), 200

    @bp.route("/api/messages/<int:index>", methods=["PUT"])
    def edit_message(index):
        """Edit a specific message by index."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        data = request.get_json()
        if not isinstance(data, dict) or "role" not in data or "content" not in data:
            return jsonify({"error": "Message must have 'role' and 'content'"}), 400

        try:
            _active_context.update_message(index, data)
            return jsonify({"success": True})
        except IndexError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/messages/<int:index>", methods=["DELETE"])
    def delete_message(index):
        """Delete a specific message by index."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        try:
            _active_context.delete_message(index)
            return jsonify({"success": True})
        except IndexError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/drop", methods=["POST"])
    def drop_last():
        """Drop the last N messages from the context."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        data = request.get_json() or {}
        n = data.get("n", 1)
        try:
            n = int(n)
        except ValueError:
            return jsonify({"error": "Invalid 'n' parameter"}), 400

        if n < 1:
            return jsonify({"error": "n must be >= 1"}), 400

        if n > len(_active_context.context):
            return (
                jsonify(
                    {
                        "error": f"Cannot drop {n} messages; only {len(_active_context.context)} exist"
                    }
                ),
                400,
            )

        # Use context handler's drop method which handles file modification atomically
        _active_context.drop(n)
        return jsonify(
            {"success": True, "dropped": n, "remaining": len(_active_context.context)}
        )

    @bp.route("/api/messages/<int:index>/truncate", methods=["POST"])
    def truncate_message(index):
        """Truncate a message's content, storing original in _truncated_from."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        data = request.get_json() or {}
        max_chars = data.get("max_chars", 200)

        try:
            max_chars = int(max_chars)
            if max_chars < 10:
                return jsonify({"error": "max_chars must be at least 10"}), 400
        except ValueError:
            return jsonify({"error": "Invalid max_chars parameter"}), 400

        try:
            _active_context.truncate_message(index, max_chars)
            return jsonify({"success": True, "index": index, "max_chars": max_chars})
        except IndexError as e:
            return jsonify({"error": str(e)}), 400
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/messages/<int:index>/expand", methods=["POST"])
    def expand_message(index):
        """Restore a truncated message's original content."""
        if _active_context is None:
            return jsonify({"error": "No active context"}), 404

        try:
            _active_context.expand_message(index)
            return jsonify({"success": True, "index": index})
        except IndexError as e:
            return jsonify({"error": str(e)}), 400
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
