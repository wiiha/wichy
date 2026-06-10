"""Minimal chat web GUI registration."""

from flask import Blueprint, render_template

from wichy.server import get_server_port

bp = Blueprint("chat_web", __name__, url_prefix="/chat", template_folder="templates")


def register(app) -> None:
    """Register chat blueprint and start poller."""
    from . import api, poller

    api.register_routes(bp)

    @bp.route("/")
    def chat_page():
        port = get_server_port()
        if port is None:
            return "Server not running", 404
        return render_template("chat.html")

    app.register_blueprint(bp)

    port = get_server_port()
    if port is not None:
        poller.start_poller(port)
