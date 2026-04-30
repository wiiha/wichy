"""API endpoints for Wichy server mode"""

from queue import Queue
from typing import Optional

from flask import Blueprint, jsonify, request
from wichy.console import user_console

from wichy.helpers.verification_provider import get_verification_provider
from wichy.wichy_server.verification_provider import ServerVerificationProvider

from wichy.helpers.interaction_provider import get_interaction_provider
from wichy.wichy_server.interaction_provider import ServerInteractionProvider

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

    @bp.route("/questions", methods=["GET"])
    def get_pending_questions():
        provider = get_interaction_provider()
        if not isinstance(provider, ServerInteractionProvider):
            return jsonify({"error": "not configured"}), 503

        pending = provider.list_pending()
        return jsonify(
            [
                {
                    "id": p.id,
                    "timestamp": p.timestamp,
                    "metadata": p.metadata,
                    "questions": [q.model_dump(mode="json") for q in p.questions],
                }
                for p in pending
            ]
        )

    @bp.route("/questions/<qid>", methods=["POST"])
    def post_question_answers(qid):
        provider = get_interaction_provider()
        if not isinstance(provider, ServerInteractionProvider):
            return jsonify({"error": "not configured"}), 503

        data = request.get_json(silent=True) or {}
        answers = data.get("answers")
        if not isinstance(answers, dict):
            return jsonify({"error": "answers dict required"}), 400

        if not provider.respond(qid, answers):
            return jsonify({"error": "not found or already answered"}), 404

        return jsonify({"status": "ok"})
