"""Notes tool Blueprint for the wichy scratchpad and notes UI."""

import json
import os

from flask import Blueprint, render_template

from wichy.config import settings


def _get_easymde_dir():
    """Return the absolute path to the shared EasyMDE static files."""
    wichy_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(wichy_root, "static", "easymde")


# Create the blueprint with URL prefix
# static_folder is set dynamically at registration time (after imports) to point to easymde
bp = Blueprint("notes", __name__, url_prefix="/tools/notes", template_folder="templates")


def register(app):
    """Register the notes blueprint with the Flask app."""
    # Point blueprint static folder to the shared easymde dir (set at registration, not construction,
    # because the easymde dir uses the same root as the blueprint's __file__)
    bp.static_folder = _get_easymde_dir()

    # Import and register API routes on this blueprint
    from . import api

    api.register_routes(bp)

    # Register the main notes route
    @bp.route("/", methods=["GET"])
    def index():
        return render_template("notes.html")

    # Finally, register the blueprint with the app
    app.register_blueprint(bp)


def get_notes_dir():
    """Get the notes directory, creating it if needed."""
    notes_dir = settings.notes_dir
    notes_dir.mkdir(parents=True, exist_ok=True)
    return str(notes_dir)


def get_scratchpad_slug():
    """Read the current scratchpad slug from the marker file.

    Returns:
        str or None: The slug string if the marker file exists and is valid,
                     None otherwise.
    """
    marker_path = settings.scratchpad_marker_path
    if marker_path.exists():
        try:
            with open(marker_path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and "slug" in data:
                return data["slug"]
        except (json.JSONDecodeError, IOError):
            pass
    return None


def set_scratchpad_slug(slug: str):
    """Write the scratchpad slug to the marker file.

    The notes directory must already exist (call get_notes_dir() first).

    Args:
        slug: The slug string to persist.
    """
    marker_path = settings.scratchpad_marker_path
    with open(marker_path, "w") as f:
        json.dump({"slug": slug}, f)
