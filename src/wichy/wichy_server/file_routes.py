"""File upload routes for the Wichy server API."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

from wichy.config import settings
from wichy.wichy_server.api import get_active_session

_DEFAULT_MAX_UPLOAD_SIZE: int = 25 * 1024 * 1024  # 25 MB


def _get_uploads_dir() -> Path:
    """Return the uploads directory, creating it if necessary."""
    uploads_dir = Path(settings.contexts_dir).parent / "fileuploads"
    try:
        uploads_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError) as exc:
        # propagate permission/space issues to the caller for handling
        raise OSError(f"cannot create uploads directory: {exc}") from exc
    return uploads_dir


def _max_upload_size() -> int:
    """Return the maximum allowed upload size in bytes."""
    env = os.environ.get("WICHY_MAX_UPLOAD_SIZE", "")
    try:
        value = int(env)
    except ValueError:
        return _DEFAULT_MAX_UPLOAD_SIZE
    return value if value >= 1 else _DEFAULT_MAX_UPLOAD_SIZE


def _sanitize_filename(name: str) -> str:
    """Sanitize a filename to prevent path traversal."""
    return secure_filename(name)


def _find_unique_filename(directory: Path, name: str) -> str:
    """Return a candidate unique filename.  Callers must use O_EXCL to avoid races."""
    candidate = directory / name
    if not candidate.exists():
        return name

    stem = Path(name).stem
    suffix = Path(name).suffix
    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        if not (directory / new_name).exists():
            return new_name
        counter += 1


def _write_file_atomic(directory: Path, name: str, content: bytes) -> str:
    """Write *content* to a uniquely-named file under *directory* using an exclusive-create open, closing the TOCTOU race. Returns the stored filename."""
    while True:
        stored_name = _find_unique_filename(directory, name)
        file_path = directory / stored_name
        fd = -1
        try:
            fd = os.open(str(file_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            return stored_name
        except FileExistsError:
            # Concurrent request created this name between our check and open.
            # Close the fd (if it was opened) and retry with the next candidate.
            if fd != -1:
                try:
                    os.close(fd)
                except OSError:
                    pass
            continue


def _list_uploaded_files() -> list[dict]:
    """Scan the uploads directory and return file metadata."""
    directory = _get_uploads_dir()
    result: list[dict] = []
    for f in directory.iterdir():
        if f.is_file():
            stat = f.stat()
            result.append(
                {
                    "name": f.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
            )
    return result


def register_file_routes(bp: Blueprint) -> None:
    """Register file upload routes on the given blueprint."""

    @bp.route("/files", methods=["GET"])
    def get_files():
        session = get_active_session()
        if session is None:
            return jsonify({"error": "no active session"}), 503

        files = _list_uploaded_files()
        return jsonify(files)

    @bp.route("/files", methods=["POST"])
    def post_file():
        session = get_active_session()
        if session is None:
            return jsonify({"error": "no active session"}), 503

        if "file" not in request.files:
            return jsonify({"error": "missing 'file' in request"}), 400

        uploaded = request.files["file"]
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "missing 'file' in request"}), 400

        raw_name = uploaded.filename
        name = _sanitize_filename(raw_name)
        if not name:
            return jsonify({"error": "invalid filename"}), 400

        try:
            uploads_dir = _get_uploads_dir()
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500

        content = uploaded.read()
        if len(content) > _max_upload_size():
            return jsonify({"error": "file too large"}), 413

        try:
            stored_name = _write_file_atomic(uploads_dir, name, content)
        except (OSError, PermissionError) as exc:
            return jsonify({"error": f"cannot write file: {exc}"}), 500

        msg = f"user uploaded file '{stored_name}' available at '.wichy/fileuploads/{stored_name}'"
        user_message = request.form.get("message", "").strip()
        if user_message:
            msg += f"\nUser message: {user_message}"
            sidecar = uploads_dir / f"{stored_name}_message.txt"
            try:
                sidecar.write_text(user_message, encoding="utf-8")
            except (OSError, PermissionError):
                pass

        try:
            session.root_agent.steer(role="user", content=msg)
        except Exception:
            # steer injection is best-effort; don't fail the upload
            pass

        return jsonify({"status": "ok", "file": stored_name})

    @bp.route("/files/<filename>", methods=["DELETE"])
    def delete_file(filename: str):
        session = get_active_session()
        if session is None:
            return jsonify({"error": "no active session"}), 503

        name = _sanitize_filename(filename)
        if not name:
            return jsonify({"error": "invalid filename"}), 400

        try:
            uploads_dir = _get_uploads_dir()
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500

        file_path = uploads_dir / name
        if not file_path.exists() or not file_path.is_file():
            return jsonify({"error": "file not found"}), 404

        try:
            file_path.unlink()
        except (OSError, PermissionError) as exc:
            return jsonify({"error": f"cannot delete file: {exc}"}), 500

        sidecar = uploads_dir / f"{name}_message.txt"
        if sidecar.exists():
            try:
                sidecar.unlink()
            except (OSError, PermissionError):
                pass

        try:
            session.root_agent.steer(
                role="user", content=f"file '.wichy/fileuploads/{name}' was deleted"
            )
        except Exception:
            # steer injection is best-effort; don't fail the delete
            pass

        return jsonify({"status": "ok", "file": name})
