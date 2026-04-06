"""Session Map feature - auto-extracted conversation visualization."""

from flask import Flask

from .store import SessionMapStore
from .api import bp, register_routes, set_session_map_store, set_context_handler

__all__ = [
    "SessionMapStore",
    "bp",
    "register_routes",
    "set_session_map_store",
    "set_context_handler",
]


def register(app: Flask):
    """Register the session map blueprint with the Flask app."""
    from . import api

    api.register_routes(bp)
    app.register_blueprint(bp)
