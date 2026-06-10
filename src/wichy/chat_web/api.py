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

    @bp.route("/api/send", methods=["POST"])
    def send_message():
        data = request.get_json(silent=True) or {}
        content = str(data.get("content", "")).strip()
        if not content:
            return jsonify({"error": "content required"}), 400

        import requests

        port = get_server_port()
        if port is None:
            return jsonify({"error": "server not running"}), 503

        base = f"http://127.0.0.1:{port}"
        try:
            resp = requests.post(
                f"{base}/server/api/messages",
                json={"line": content},
                timeout=5.0,
            )
            _ = resp.text
            if resp.status_code != 200:
                return jsonify({"error": f"server returned {resp.status_code}"}), 502

            entry = state.create_entry("user", content)
            state.append(entry)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"error": str(e)}), 502
