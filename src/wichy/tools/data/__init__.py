"""Data Explorer Web UI - DuckDB data explorer via browser."""

from flask import Blueprint, render_template

# Create the main blueprint for the data explorer
bp = Blueprint(
    "data",
    __name__,
    url_prefix="/tools/data",
    template_folder="templates",
)


def register(app):
    """Register the data explorer blueprint with the Flask app."""
    # Import and register API routes on this blueprint
    from . import api

    api.register_routes(bp)

    # Register the main explorer route
    @bp.route("/", methods=["GET"])
    def explorer():
        return render_template("data_explorer.html")

    # Finally, register the blueprint with the app
    app.register_blueprint(bp)
