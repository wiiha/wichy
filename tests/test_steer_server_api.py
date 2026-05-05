"""Tests for the POST /steer endpoint in wichy_server/api.py."""

import json
from unittest.mock import patch

import pytest
from flask import Blueprint, Flask

from wichy.wichy_server.api import (
    register_routes,
    set_active_session,
)


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
    register_routes(bp)
    app.register_blueprint(bp)

    # Ensure no session leaks between tests
    set_active_session(None)

    with app.test_client() as client:
        yield client

    set_active_session(None)


@pytest.fixture(autouse=True)
def patch_user_console_print():
    """Patch user_console.print to avoid side effects during tests."""
    with patch("wichy.wichy_server.api.user_console.print"):
        yield


class TestSteerEndpoint:
    def test_steer_no_active_session(self, client):
        response = client.post(
            "/server/api/steer", json={"role": "user", "content": "hello"}
        )
        assert response.status_code == 503
        data = json.loads(response.data)
        assert data["error"] == "no active session"

    def test_steer_default_role(self, client):
        session = MockSession()
        set_active_session(session)

        response = client.post("/server/api/steer", json={"content": "hello world"})
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert session.root_agent.steer_calls == [("user", "hello world")]

    def test_steer_custom_role(self, client):
        session = MockSession()
        set_active_session(session)

        response = client.post(
            "/server/api/steer", json={"role": "system", "content": "be helpful"}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert session.root_agent.steer_calls == [("system", "be helpful")]

    def test_steer_empty_request(self, client):
        session = MockSession()
        set_active_session(session)

        response = client.post("/server/api/steer")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert session.root_agent.steer_calls == [("user", "")]
