"""API endpoints for Wichy server mode"""

from __future__ import annotations

from queue import Queue
from typing import TYPE_CHECKING, Optional

from flask import Blueprint, jsonify, request
from wichy.console import user_console

if TYPE_CHECKING:
    from wichy.wichy_server.chat_session import ChatSession

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


_active_session: Optional["ChatSession"] = None


def set_active_session(session: Optional["ChatSession"]):
    """Set the currently active ChatSession so API routes can reach it."""
    global _active_session
    _active_session = session


def get_active_session() -> Optional["ChatSession"]:
    return _active_session


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

    @bp.route("/steer", methods=["POST"])
    def steer():
        session = get_active_session()
        if session is None:
            return jsonify({"error": "no active session"}), 503
        data = request.get_json(silent=True) or {}
        role = data.get("role", "user")
        content = data.get("content", "")
        session.root_agent.steer(role=role, content=content)
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

    @bp.route("/root/context", methods=["GET"])
    def get_root_context():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        ctx = session.root_agent.context
        return jsonify(
            {
                "filename": ctx.path.name,
                "entries": ctx.get_entries(),
            }
        )

    @bp.route("/root/status", methods=["GET"])
    def get_root_status():
        session = get_active_session()
        if session is None or session.root_agent is None:
            return jsonify({"error": "no active root agent"}), 503

        ra = session.root_agent
        return jsonify(
            {
                "model": ra.model_str,
                "name": ra.name,
                "display_name": ra.display_name,
                "message_count": len(ra.context),
                "current_prompt_tokens": ra.current_prompt_tokens,
                "auto_compact_threshold": ra.auto_compact_threshold,
            }
        )

    @bp.route("/slashcommands", methods=["GET"])
    def get_slash_commands():
        session = get_active_session()
        if session is None or session.cmd_checker is None:
            return jsonify({"error": "no active session"}), 503

        commands = session.cmd_checker.list_commands()
        return jsonify({"commands": commands})
