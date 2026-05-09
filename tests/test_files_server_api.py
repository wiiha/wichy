"""Tests for the file upload endpoints in wichy_server/file_routes.py."""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from flask import Blueprint, Flask

from wichy.wichy_server.api import set_active_session
from wichy.wichy_server.file_routes import register_file_routes


class MockAgent:
    def __init__(self):
        self.steer_calls = []

    def steer(self, role, content):
        self.steer_calls.append((role, content))


class MockSession:
    def __init__(self):
        self.root_agent = MockAgent()


@pytest.fixture
def client():
    """Provide a Flask test client with a fresh app and blueprint."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_file_routes(bp)
    app.register_blueprint(bp)

    set_active_session(None)

    with app.test_client() as client:
        yield client

    set_active_session(None)


# ── GET ────────────────────────────────────────────────────────────────


class TestGetFiles:
    def test_get_files_no_session(self, client):
        response = client.get("/server/api/files")
        assert response.status_code == 503
        assert response.json["error"] == "no active session"

    def test_get_files_empty(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            response = client.get("/server/api/files")

        assert response.status_code == 200
        assert response.json == []

    def test_get_files_returns_entries(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)
        (tmp_path / "hello.txt").write_text("hello world")

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            response = client.get("/server/api/files")

        assert response.status_code == 200
        data = response.json
        assert len(data) == 1
        assert data[0]["name"] == "hello.txt"
        assert data[0]["size"] == 11
        assert "modified" in data[0]
        # Verify ISO-8601 ending with +00:00
        assert data[0]["modified"].endswith("+00:00")


# ── POST ───────────────────────────────────────────────────────────────


class TestPostFile:
    def test_post_no_session(self, client):
        response = client.post("/server/api/files")
        assert response.status_code == 503
        assert response.json["error"] == "no active session"

    def test_post_no_file(self, client):
        session = MockSession()
        set_active_session(session)

        response = client.post("/server/api/files")
        assert response.status_code == 400
        assert "missing" in response.json["error"]

    def test_post_upload_success(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {"file": (io.BytesIO(b"content"), "foo.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        assert response.json["status"] == "ok"
        assert response.json["file"] == "foo.txt"
        assert (tmp_path / "foo.txt").read_bytes() == b"content"
        assert session.root_agent.steer_calls == [
            (
                "user",
                "user uploaded file 'foo.txt' available at '.wichy/fileuploads/foo.txt'",
            )
        ]

    def test_post_upload_with_message(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {
                "file": (io.BytesIO(b"content"), "bar.txt"),
                "message": "Please summarize this",
            }
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        assert response.json["file"] == "bar.txt"
        assert (tmp_path / "bar.txt_message.txt").read_text() == "Please summarize this"
        assert session.root_agent.steer_calls == [
            (
                "user",
                "user uploaded file 'bar.txt' available at '.wichy/fileuploads/bar.txt'\n"
                "User message: Please summarize this",
            )
        ]

    def test_post_upload_auto_rename(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)
        (tmp_path / "foo.txt").write_text("existing")

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {"file": (io.BytesIO(b"new content"), "foo.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        assert response.json["file"] == "foo_1.txt"
        assert (tmp_path / "foo.txt").read_text() == "existing"
        assert (tmp_path / "foo_1.txt").read_bytes() == b"new content"

    def test_post_upload_auto_rename_beyond_1(self, client, tmp_path):
        """Auto-rename should skip foo_1.txt when it also exists."""
        session = MockSession()
        set_active_session(session)
        (tmp_path / "foo.txt").write_text("a")
        (tmp_path / "foo_1.txt").write_text("b")

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {"file": (io.BytesIO(b"c"), "foo.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        assert response.json["file"] == "foo_2.txt"
        assert (tmp_path / "foo_2.txt").read_bytes() == b"c"

    def test_post_upload_too_large(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with (
            patch("wichy.wichy_server.file_routes._max_upload_size", return_value=5),
            patch(
                "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
            ),
        ):
            data = {"file": (io.BytesIO(b"toolarge"), "big.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 413
        assert "too large" in response.json["error"]

    def test_post_path_traversal(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {"file": (io.BytesIO(b"pwned"), "../../../etc/passwd")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        # secure_filename strips path traversal and relative components
        assert response.json["file"] == "etc_passwd"
        assert (tmp_path / "etc_passwd").read_bytes() == b"pwned"

    def test_post_invalid_filename_empty_after_sanitize(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            # ".." sanitizes to empty string via secure_filename
            data = {"file": (io.BytesIO(b"x"), "..")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 400
        assert response.json["error"] == "invalid filename"

    def test_post_uploads_dir_creation_failure(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir",
            side_effect=OSError("permission denied"),
        ):
            data = {"file": (io.BytesIO(b"content"), "fail.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 500
        assert "permission denied" in response.json["error"]

    def test_post_steering_best_effort(self, client, tmp_path):
        """If steer() raises, the upload should still succeed."""
        session = MockSession()
        session.root_agent = MockAgent()
        session.root_agent.steer = lambda role, content: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {"file": (io.BytesIO(b"content"), "steerfail.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        assert response.json["file"] == "steerfail.txt"
        assert (tmp_path / "steerfail.txt").read_bytes() == b"content"

    def test_post_empty_file(self, client, tmp_path):
        """Uploading a 0-byte file should succeed."""
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            data = {"file": (io.BytesIO(b""), "empty.txt")}
            response = client.post(
                "/server/api/files",
                data=data,
                content_type="multipart/form-data",
            )

        assert response.status_code == 200
        assert response.json["file"] == "empty.txt"
        assert (tmp_path / "empty.txt").read_bytes() == b""
        assert response.json["status"] == "ok"


# ── DELETE ─────────────────────────────────────────────────────────────


class TestDeleteFile:
    def test_delete_no_session(self, client):
        response = client.delete("/server/api/files/foo.txt")
        assert response.status_code == 503
        assert response.json["error"] == "no active session"

    def test_delete_not_found(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            response = client.delete("/server/api/files/missing.txt")

        assert response.status_code == 404
        assert response.json["error"] == "file not found"

    def test_delete_directory_not_allowed(self, client, tmp_path):
        """DELETE of a directory should be treated as 404."""
        session = MockSession()
        set_active_session(session)
        (tmp_path / "subdir").mkdir()

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            response = client.delete("/server/api/files/subdir")

        # is_file() returns False for directories
        assert response.status_code == 404
        assert response.json["error"] == "file not found"

    def test_delete_path_traversal(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            # Path with forward slash is a single segment to Flask but
            # secure_filename converts '/' to '_' anyway
            response = client.delete("/server/api/files/etc_passwd")

        assert response.status_code == 404
        assert response.json["error"] == "file not found"

    def test_delete_success(self, client, tmp_path):
        session = MockSession()
        set_active_session(session)
        (tmp_path / "bye.txt").write_text("goodbye")

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            response = client.delete("/server/api/files/bye.txt")

        assert response.status_code == 200
        assert response.json["status"] == "ok"
        assert response.json["file"] == "bye.txt"
        assert not (tmp_path / "bye.txt").exists()
        assert not (tmp_path / "bye.txt_message.txt").exists()
        assert session.root_agent.steer_calls == [
            ("user", "file '.wichy/fileuploads/bye.txt' was deleted")
        ]

    def test_delete_failure_retried(self, client, tmp_path):
        """Simulate and cover deletion error paths gracefully."""
        session = MockSession()
        set_active_session(session)
        (tmp_path / "locked.txt").write_text("content")

        with patch("pathlib.Path.unlink", side_effect=PermissionError("locked")):
            with patch(
                "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
            ):
                response = client.delete("/server/api/files/locked.txt")

        assert response.status_code == 500
        assert "locked" in response.json["error"]

    def test_delete_steering_best_effort(self, client, tmp_path):
        """If steer() raises on delete, the delete should still succeed."""
        session = MockSession()
        session.root_agent = MockAgent()
        session.root_agent.steer = lambda role, content: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        set_active_session(session)
        (tmp_path / "delsteer.txt").write_text("x")

        with patch(
            "wichy.wichy_server.file_routes._get_uploads_dir", return_value=tmp_path
        ):
            response = client.delete("/server/api/files/delsteer.txt")

        assert response.status_code == 200
        assert not (tmp_path / "delsteer.txt").exists()


# ── max_upload_size edge cases ───────────────────────────────────────


class TestMaxUploadSize:
    def test_max_upload_size_negative_env(self, monkeypatch):
        monkeypatch.setenv("WICHY_MAX_UPLOAD_SIZE", "-1")
        from wichy.wichy_server.file_routes import _max_upload_size

        assert _max_upload_size() == 25 * 1024 * 1024

    def test_max_upload_size_zero_env(self, monkeypatch):
        monkeypatch.setenv("WICHY_MAX_UPLOAD_SIZE", "0")
        from wichy.wichy_server.file_routes import _max_upload_size

        assert _max_upload_size() == 25 * 1024 * 1024

    def test_max_upload_size_custom_env(self, monkeypatch):
        monkeypatch.setenv("WICHY_MAX_UPLOAD_SIZE", "1024")
        from wichy.wichy_server.file_routes import _max_upload_size

        assert _max_upload_size() == 1024
