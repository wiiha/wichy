"""Tests for /server/api/events and /server/api/sub-agents/{id}/events endpoints."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Blueprint, Flask

from wichy.event_log import get_agent_event_store, get_event_store
from wichy.wichy_server.api import register_routes, set_active_session


class FakeSession:
    """Minimal stand-in for ChatSession with a root agent."""

    def __init__(self, session_id: str):
        self.root_agent = MagicMock()
        self.root_agent.context = MagicMock()
        self.root_agent.context.session_id = session_id
        self.root_agent.model_str = "test/model"
        self.root_agent.name = "test-agent"
        self.root_agent.context.path = Path("/tmp/fake_context.json")


@pytest.fixture
def client():
    app = Flask(__name__)
    bp = Blueprint("server", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def temp_settings(tmp_path):
    class FakeSettings:
        events_dir = tmp_path / "events"
        events_max_count = 50_000
        events_max_size_mb = 10
        events_retention_days = 7
        events_queue_size = 10_000

    with patch("wichy.event_log.store.settings", FakeSettings()):
        with patch("wichy.event_log.paths.settings", FakeSettings()):
            yield


def test_get_events_returns_paginated_events(client, tmp_path, temp_settings):
    session_id = "test-session-1"
    set_active_session(FakeSession(session_id))

    store = get_event_store(session_id)
    store.emit("user_message_received", {"content_preview": "hi"})
    store.emit("llm_call_started", {"model_str": "test/model"})
    store.flush(timeout=2.0)

    response = client.get("/server/api/events")
    assert response.status_code == 200
    data = response.get_json()
    assert data["session_id"] == session_id
    assert len(data["events"]) == 2
    assert data["events"][0]["event_type"] == "user_message_received"
    assert data["events"][1]["event_type"] == "llm_call_started"
    assert data["last_id"] == 2
    assert data["has_more"] is False


def test_get_events_with_since_id(client, tmp_path, temp_settings):
    session_id = "test-session-2"
    set_active_session(FakeSession(session_id))

    store = get_event_store(session_id)
    store.emit("a", {})
    store.emit("b", {})
    store.flush(timeout=2.0)

    response = client.get("/server/api/events?since_id=1")
    assert response.status_code == 200
    data = response.get_json()
    assert [e["event_type"] for e in data["events"]] == ["b"]
    assert data["last_id"] == 2


def test_get_events_returns_503_without_session(client):
    set_active_session(None)
    response = client.get("/server/api/events")
    assert response.status_code == 503


def test_get_sub_agent_events(client, tmp_path, temp_settings):
    session_id = "test-session-3"
    set_active_session(FakeSession(session_id))

    store = get_agent_event_store(session_id, "agent-1")
    store.emit("task_agent_registered", {"model": "test/model"})
    store.emit("task_agent_completed", {"status": "completed"})
    store.flush(timeout=2.0)

    response = client.get("/server/api/sub-agents/agent-1/events")
    assert response.status_code == 200, response.get_json()
    data = response.get_json()
    assert data["agent_id"] == "agent-1"
    assert data["session_id"] == session_id
    assert len(data["events"]) == 2
    assert data["events"][0]["event_type"] == "task_agent_registered"


def test_clear_events(client, tmp_path, temp_settings):
    session_id = "test-session-4"
    set_active_session(FakeSession(session_id))

    store = get_event_store(session_id)
    store.emit("a", {})
    store.flush(timeout=2.0)

    response = client.post("/server/api/events/clear")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["backup"]

    response = client.get("/server/api/events")
    data = response.get_json()
    assert data["events"] == []
