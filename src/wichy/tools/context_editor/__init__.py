"""Context Editor Web UI - edit conversation context via browser."""

from flask import Blueprint, render_template

# Create the main blueprint for the context editor
bp = Blueprint(
    "context_editor",
    __name__,
    url_prefix="/tools/context",
    template_folder="templates",
    static_folder="static",
)


def register(app):
    """Register the context editor blueprint with the Flask app."""
    # Import and register API routes on this blueprint
    from . import api

    api.register_routes(bp)

    # Register the main editor route
    @bp.route("/", methods=["GET"])
    def editor():
        return render_template("context_editor.html")

    # Finally, register the blueprint with the app
    app.register_blueprint(bp)
