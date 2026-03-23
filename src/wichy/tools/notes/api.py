"""API endpoints for the notes and scratchpad tool."""

import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from wichy.skills.skill import parse_markdown_frontmatter

from . import get_notes_dir, get_scratchpad_slug, set_scratchpad_slug


def register_routes(bp: Blueprint):
    """Register all API routes on the given blueprint."""

    def _generate_slug(title: str) -> str:
        """Generate a slug from a title.

        Lowercase, replace spaces with hyphens, strip non-alphanumeric
        characters except hyphens.
        """
        slug = title.lower().replace(" ", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        slug = slug.strip("-")
        return slug

    def _slug_exists(slug: str) -> bool:
        """Check if a note with the given slug already exists."""
        notes_dir = Path(get_notes_dir())
        return (notes_dir / f"{slug}.md").exists()

    def _make_unique_slug(base_slug: str) -> str:
        """Return a slug that's guaranteed not to conflict with existing notes."""
        slug = base_slug
        counter = 1
        while _slug_exists(slug):
            slug = f"{base_slug}-{counter}"
            counter += 1
        return slug

    def _write_note(slug: str, title: str, content: str, created: str) -> str:
        """Write a note file and return the updated timestamp."""
        notes_dir = Path(get_notes_dir())
        updated = datetime.utcnow().isoformat()
        file_path = notes_dir / f"{slug}.md"
        file_content = f"---\ntitle: {title}\ncreated: {created}\nupdated: {updated}\n---\n{content}"
        with open(file_path, "w") as f:
            f.write(file_content)
        return updated

    def _read_note(slug: str) -> dict | None:
        """Read a note file and return its data, or None if not found."""
        notes_dir = Path(get_notes_dir())
        file_path = notes_dir / f"{slug}.md"
        if not file_path.exists():
            return None
        try:
            with open(file_path, "r") as f:
                raw = f.read()
            metadata, body = parse_markdown_frontmatter(raw)
            return {
                "slug": slug,
                "title": metadata.get("title", slug),
                "content": body,
                "created": metadata.get("created", ""),
                "updated": metadata.get("updated", ""),
            }
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Routes
    # -------------------------------------------------------------------------

    @bp.route("/api/notes")
    def list_notes():
        """List all notes (excluding the scratchpad marker)."""
        try:
            notes_dir = Path(get_notes_dir())
            notes = []
            for file_path in notes_dir.glob("*.md"):
                # Skip .scratchpad marker file
                if file_path.name == ".scratchpad":
                    continue
                slug = file_path.stem  # filename without .md
                try:
                    with open(file_path, "r") as f:
                        raw = f.read()
                    metadata, _ = parse_markdown_frontmatter(raw)
                    notes.append(
                        {
                            "slug": slug,
                            "title": metadata.get("title", slug),
                            "created": metadata.get("created", ""),
                            "updated": metadata.get("updated", ""),
                        }
                    )
                except Exception:
                    # Skip files that can't be read
                    continue
            return jsonify({"notes": notes})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/notes", methods=["POST"])
    def create_note():
        """Create a new note."""
        try:
            data = request.get_json() or {}
            title = data.get("title", "")
            content = data.get("content", "")

            if not title:
                return jsonify({"error": "title is required"}), 400

            base_slug = _generate_slug(title)
            slug = _make_unique_slug(base_slug)

            # Check for exact duplicate (title results in exact slug with no suffix)
            if slug == base_slug and _slug_exists(slug):
                return jsonify({"error": "A note with this title already exists"}), 409

            created = datetime.utcnow().isoformat()
            updated = _write_note(slug, title, content, created)

            return (
                jsonify(
                    {
                        "slug": slug,
                        "title": title,
                        "created": created,
                        "updated": updated,
                    }
                ),
                201,
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/notes/<slug>")
    def get_note(slug: str):
        """Read a specific note by slug."""
        try:
            note = _read_note(slug)
            if note is None:
                return jsonify({"error": "Note not found"}), 404
            return jsonify(note)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/notes/<slug>", methods=["PUT"])
    def update_note(slug: str):
        """Update an existing note."""
        try:
            existing = _read_note(slug)
            if existing is None:
                return jsonify({"error": "Note not found"}), 404

            data = request.get_json() or {}
            new_title = data.get("title", existing["title"])
            new_content = data.get("content", existing["content"])

            # Determine new slug if title changed
            new_slug = _generate_slug(new_title)
            if new_slug != slug:
                # Title changed — need to check for conflicts
                if _slug_exists(new_slug):
                    return jsonify({"error": "A note with this title already exists"}), 409
                new_slug = _make_unique_slug(new_slug)

            # If slug changed, delete old file and write new one
            if new_slug != slug:
                old_path = Path(get_notes_dir()) / f"{slug}.md"
                if old_path.exists():
                    old_path.unlink()
                created = datetime.utcnow().isoformat()
                updated = _write_note(new_slug, new_title, new_content, created)
            else:
                # Same slug — rewrite with updated timestamp
                created = existing["created"]
                updated = _write_note(slug, new_title, new_content, existing["created"])

            return jsonify(
                {
                    "slug": new_slug,
                    "title": new_title,
                    "created": created,
                    "updated": updated,
                }
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/notes/<slug>", methods=["DELETE"])
    def delete_note(slug: str):
        """Delete a note by slug."""
        try:
            notes_dir = Path(get_notes_dir())
            file_path = notes_dir / f"{slug}.md"
            if not file_path.exists():
                return jsonify({"error": "Note not found"}), 404

            # Check if this was the scratchpad
            current_scratchpad = get_scratchpad_slug()
            if current_scratchpad == slug:
                set_scratchpad_slug(None)

            file_path.unlink()
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/notes/set-scratchpad", methods=["POST"])
    def set_scratchpad():
        """Set or clear the scratchpad note."""
        try:
            data = request.get_json() or {}
            slug = data.get("slug")

            if not slug:
                # Clear the scratchpad marker
                set_scratchpad_slug(None)
                return jsonify({"slug": None})

            # Verify the note exists
            if not _slug_exists(slug):
                return jsonify({"error": "Note not found"}), 404

            set_scratchpad_slug(slug)
            return jsonify({"slug": slug})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/scratchpad-status")
    def scratchpad_status():
        """Get the current scratchpad status."""
        try:
            slug = get_scratchpad_slug()
            title = None
            if slug:
                note = _read_note(slug)
                if note:
                    title = note.get("title")
            return jsonify({"slug": slug, "title": title})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
