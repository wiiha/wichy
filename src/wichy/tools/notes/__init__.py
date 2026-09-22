"""Notes tool Blueprint for the wichy scratchpad and notes UI."""

import json
import os
from typing import Any

from flask import Blueprint, render_template

from wichy.config import settings


def _get_easymde_dir():
    """Return the absolute path to the shared EasyMDE static files."""
    wichy_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(wichy_root, "static", "easymde")


# Create the blueprint with URL prefix
# static_folder is set dynamically at registration time (after imports) to point to easymde
bp = Blueprint(
    "notes", __name__, url_prefix="/tools/notes", template_folder="templates"
)


def register(app):
    """Register the notes blueprint with the Flask app."""
    # Point blueprint static folder to the shared easymde dir (set at registration, not construction,
    # because the easymde dir uses the same root as the blueprint's __file__)
    bp.static_folder = _get_easymde_dir()

    # Subscribe the busy indicator to agent turn boundaries. Done here because
    # registration happens at app setup in every mode, before any turn starts.
    from .busy import install_busy_observer

    install_busy_observer()

    # Import and register API routes on this blueprint
    from . import api

    api.register_routes(bp)

    # Register the main notes route
    @bp.route("/", methods=["GET"])
    def index():
        # The intervals are passed in rather than hardcoded in the script, so a
        # deployment can change them without editing JavaScript.
        settings_for_page = {
            "poll_interval_ms": settings.notes_poll_interval_ms,
            "change_debounce_ms": settings.notes_change_debounce_ms,
            "save_debounce_ms": settings.notes_save_debounce_ms,
            "notification_default_mode": settings.notification_default_mode,
            "enable_block_editor": settings.notes_enable_block_editor,
        }
        return render_template("notes.html", notes_settings=settings_for_page)

    # Finally, register the blueprint with the app
    app.register_blueprint(bp)


def get_notes_dir():
    """Get the notes directory, creating it if needed."""
    notes_dir = settings.notes_dir
    notes_dir.mkdir(parents=True, exist_ok=True)
    return str(notes_dir)


def get_scratchpad_state() -> dict[str, Any]:
    """Read the scratchpad marker.

    The marker has two fields:

    - ``primary`` -- the one agent scratchpad. This is the only field that
      decides which document the agent tools edit; a marker listing several
      pinned documents does not make several of them editable.
    - ``pinned`` -- presentation state for the sidebar's pinned marker.

    A legacy ``{"slug": ...}`` marker is still read, so an existing pin keeps
    working across the upgrade.

    Returns:
        dict: ``{"primary": str | None, "pinned": list[str]}``. An absent or
        unreadable marker reads as unpinned, which is the normal state.
    """
    marker_path = settings.scratchpad_marker_path
    if marker_path.exists():
        try:
            with open(marker_path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "primary" in data:
                    primary = data["primary"]
                else:
                    # Legacy marker: a single slug, or an explicit null.
                    primary = data.get("slug")
                pinned = data.get("pinned")
                return {
                    "primary": primary or None,
                    "pinned": (
                        [s for s in pinned if s] if isinstance(pinned, list) else []
                    ),
                }
        except (json.JSONDecodeError, IOError):
            pass
    return {"primary": None, "pinned": []}


def get_scratchpad_slug() -> str | None:
    """Read the primary scratchpad slug from the marker file.

    Returns:
        The slug if the marker names one, else None. None is the ordinary state
        rather than an error: the pin is cleared on every CLI start.
    """
    primary = get_scratchpad_state()["primary"]
    # Narrowed here rather than trusted: the marker is a file a user can edit, so
    # a hand-written `{"primary": 5}` must read as unpinned, not as a slug.
    return primary if isinstance(primary, str) else None


def set_scratchpad_state(primary: str | None, pinned: list[str] | None = None) -> None:
    """Write the scratchpad marker.

    Clearing writes explicit nulls rather than deleting the file, matching how
    the marker has always been cleared, so a cleared pin is distinguishable
    from a marker that was never written.

    The notes directory must already exist (call get_notes_dir() first).

    Args:
        primary: The slug to pin, or None to clear.
        pinned: The pinned-display list. Entries are de-duplicated, and the
            primary slug is always included so the sidebar marker and the
            agent's actual target cannot disagree.
    """
    marker_path = settings.scratchpad_marker_path
    entries: list[str] = []
    for slug in list(pinned or []) + ([primary] if primary else []):
        if slug and slug not in entries:
            entries.append(slug)
    try:
        with open(marker_path, "w") as f:
            json.dump({"primary": primary, "pinned": entries}, f)
    except IOError:
        pass


def set_scratchpad_slug(slug: str | None) -> None:
    """Set or clear the primary scratchpad, replacing the pinned list.

    This is the single-slug entry point. It resets ``pinned`` to match, rather
    than preserving a list it knows nothing about: there is exactly one
    scratchpad, and clearing must leave no slug claiming to be one.

    Args:
        slug: The slug string to persist, or None to clear.
    """
    set_scratchpad_state(slug)
