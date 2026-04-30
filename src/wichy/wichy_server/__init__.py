"""Wichy running in server mode, exposing an API instead of REPL interaction"""

from flask import Blueprint
from wichy.wichy_server.chat_session import ChatSession
from wichy.wichy_server.api import set_input_queue

# Create the main blueprint for the data explorer
bp = Blueprint(
    "server",
    __name__,
    url_prefix="/server/api",
)


def register(app):
    """Register the data explorer blueprint with the Flask app."""
    # Import and register API routes on this blueprint
    from . import api

    api.register_routes(bp)

    # Register a test route, this one will be shadowed by routes in api if they register the same route.
    @bp.route("/", methods=["GET"])
    def base():
        return "Hello World"

    # Finally, register the blueprint with the app
    app.register_blueprint(bp)


__all__ = ["ChatSession", "set_input_queue", "register"]
