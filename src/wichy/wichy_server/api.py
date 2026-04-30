"""API endpoints for Wichy server mode"""

from queue import Queue
from typing import Optional

from flask import Blueprint, jsonify, request
from wichy.console import user_console

from wichy.helpers.verification_provider import get_verification_provider
from wichy.wichy_server.verification_provider import ServerVerificationProvider

_input_queue: Optional[Queue[str]] = None


def set_input_queue(q: Queue[str]):
    """Set the currently active context handler for the web editor to manipulate."""
    global _input_queue
    _input_queue = q


def get_input_queue():
    return _input_queue


def register_routes(bp: Blueprint):
    """Register all API routes on the given blueprint."""

    @bp.route("/messages", methods=["GET"])
    def get_responses():
        msgs = user_console.get_messages()
        return jsonify(msgs)

    @bp.route("/messages", methods=["POST"])
    def post_new_message():
        q = get_input_queue()
        if not q:
            return (
                jsonify({"error": "Server input not available: no active input queue"}),
                503,
            )
        data = request.get_json(silent=True) or {}
        q.put(data.get("line", ""))
        return jsonify({"status": "ok"})

    @bp.route("/verifications", methods=["GET"])
    def get_pending():
        vp = get_verification_provider()
        if not isinstance(vp, ServerVerificationProvider):
            return jsonify({"error": "not configured"}), 503
        return jsonify(
            [
                {"id": p.id, "label": p.label, "message": p.message, "args": p.args}
                for p in vp.list_pending()
            ]
        )

    @bp.route("/verifications/<vid>", methods=["POST"])
    def post_single(vid):
        vp = get_verification_provider()
        if not isinstance(vp, ServerVerificationProvider):
            return jsonify({"error": "not configured"}), 503

        data = request.get_json(silent=True) or {}
        approved = bool(data.get("approved", False))
        reason = str(data.get("reason", ""))

        if not vp.respond(vid, approved, reason):
            return jsonify({"error": "not found or already resolved"}), 404

        return jsonify({"status": "ok"})
