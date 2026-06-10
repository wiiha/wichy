"""Chat module API routes — minimal v1."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import state
from .poller import get_server_port


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/history", methods=["GET"])
    def get_history():
        return jsonify(state.load_history())

    @bp.route("/api/status", methods=["GET"])
    def get_status():
        return jsonify({"connected": get_server_port() is not None})

    @bp.route("/api/clear", methods=["POST"])
    def clear_chat():
        state.clear_history()
        return jsonify({"status": "ok"})

    @bp.route("/api/send", methods=["POST"])
    def send_message():
        data = request.get_json(silent=True) or {}
        content = str(data.get("content", "")).strip()
        if not content:
            return jsonify({"error": "content required"}), 400
        msg_type = data.get("type", "message")

        import requests

        port = get_server_port()
        if port is None:
            return jsonify({"error": "server not running"}), 503

        base = f"http://127.0.0.1:{port}"
        try:
            if msg_type == "steer":
                resp = requests.post(
                    f"{base}/server/api/steer",
                    json={"role": "user", "content": content},
                    timeout=5.0,
                )
            else:
                resp = requests.post(
                    f"{base}/server/api/messages",
                    json={"line": content},
                    timeout=5.0,
                )
            _ = resp.text
            if resp.status_code != 200:
                return jsonify({"error": f"server returned {resp.status_code}"}), 502

            role = "steer" if msg_type == "steer" else "user"
            entry = state.create_entry(role, content)
            state.append(entry)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    @bp.route("/api/verifications/<vid>/resolve", methods=["POST"])
    def resolve_verification(vid: str):
        data = request.get_json(silent=True) or {}
        approved = bool(data.get("approved", False))
        reason = str(data.get("reason", ""))

        import requests

        port = get_server_port()
        if port is None:
            return jsonify({"error": "server not running"}), 503

        base = f"http://127.0.0.1:{port}"
        try:
            resp = requests.post(
                f"{base}/server/api/verifications/{vid}",
                json={"approved": approved, "reason": reason},
                timeout=5.0,
            )
            _ = resp.text
            if resp.status_code == 404:
                return jsonify({"error": "not found"}), 404
            if resp.status_code != 200:
                return jsonify({"error": f"server returned {resp.status_code}"}), 502

            state.resolve_verification(vid, approved)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"error": str(e)}), 502

    @bp.route("/api/questions/<qid>/answer", methods=["POST"])
    def answer_question(qid: str):
        data = request.get_json(silent=True) or {}
        answers = data.get("answers")
        if not isinstance(answers, dict):
            return jsonify({"error": "answers dict required"}), 400

        import requests

        port = get_server_port()
        if port is None:
            return jsonify({"error": "server not running"}), 503

        base = f"http://127.0.0.1:{port}"
        try:
            resp = requests.post(
                f"{base}/server/api/questions/{qid}",
                json={"answers": answers},
                timeout=5.0,
            )
            _ = resp.text
            if resp.status_code == 404:
                return jsonify({"error": "not found"}), 404
            if resp.status_code != 200:
                return jsonify({"error": f"server returned {resp.status_code}"}), 502

            state.resolve_question(qid, answers)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"error": str(e)}), 502
