"""
Activity Graph blueprint for the central Flask server.

Provides a read-only web UI that visualizes root agent + task agent + tool call
history as an interactive SVG graph.
"""

from flask import Blueprint, render_template

bp = Blueprint(
    "activity",
    __name__,
    url_prefix="/tools/activity",
    template_folder="templates",
    static_folder="static",
)


@bp.route("/")
def index():
    """Serve the Activity Graph HTML page."""
    return render_template("activity.html")


def register(app):
    """Register the activity blueprint with the Flask app."""
    app.register_blueprint(bp)
