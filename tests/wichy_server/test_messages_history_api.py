"""Tests for the non-destructive GET /server/api/messages/history endpoint.

The history state lives in the module-level ``user_console`` proxy's current
implementation, so the fixture swaps in a fresh ``ServerConsole`` and restores
the previous implementation afterwards.
"""

import pytest
from flask import Blueprint, Flask

from wichy.config.settings import settings
from wichy.console.user import ServerConsole, ThreadSafeConsole, user_console
from wichy.wichy_server import api as server_api
from wichy.wichy_server.api import register_routes


@pytest.fixture
def api_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)
    server_api.set_active_session(None)
    server_api.set_input_queue(None)
    prior = user_console._impl
    user_console.set_impl(ServerConsole())
    try:
        with app.test_client() as client:
            yield client
    finally:
        user_console.set_impl(prior)
        server_api.set_active_session(None)
        server_api.set_input_queue(None)


def _history(client, since_id=0, limit=100):
    resp = client.get(f"/server/api/messages/history?since_id={since_id}&limit={limit}")
    assert resp.status_code == 200
    return resp.get_json()


def test_history_empty_initially(api_client):
    resp = api_client.get("/server/api/messages/history")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "messages": [],
        "last_id": 0,
        "has_more": False,
        "overflowed": False,
    }


def test_history_returns_printed_output(api_client):
    user_console.print("alpha")
    user_console.print("beta")
    body = _history(api_client)
    assert body["messages"] == ["alpha", "beta"]
    assert body["last_id"] == 2
    assert body["has_more"] is False


def test_history_non_destructive(api_client):
    user_console.print("gamma")
    user_console.print("delta")
    first = api_client.get("/server/api/messages/history")
    second = api_client.get("/server/api/messages/history")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json() == second.get_json()
    assert first.get_json()["messages"] == ["gamma", "delta"]

    user_console.print("epsilon")
    body = _history(api_client, since_id=2)
    assert body["messages"] == ["epsilon"]
    assert body["last_id"] == 3


def test_history_pagination(api_client):
    for name in ["m1", "m2", "m3", "m4", "m5"]:
        user_console.print(name)

    page1 = _history(api_client, since_id=0, limit=2)
    assert page1["messages"] == ["m1", "m2"]
    assert page1["has_more"] is True
    assert page1["last_id"] == 2

    page2 = _history(api_client, since_id=page1["last_id"], limit=2)
    assert page2["messages"] == ["m3", "m4"]
    assert page2["has_more"] is True
    assert page2["last_id"] == 4

    page3 = _history(api_client, since_id=page2["last_id"], limit=2)
    assert page3["messages"] == ["m5"]
    assert page3["has_more"] is False
    assert page3["last_id"] == 5

    collected = page1["messages"] + page2["messages"] + page3["messages"]
    assert collected == ["m1", "m2", "m3", "m4", "m5"]


def test_drain_still_destructive(api_client):
    user_console.print("a")
    user_console.print("b")

    drain1 = api_client.get("/server/api/messages")
    assert drain1.status_code == 200
    assert drain1.get_json() == ["a", "b"]

    drain2 = api_client.get("/server/api/messages")
    assert drain2.status_code == 200
    assert drain2.get_json() == []

    history = _history(api_client)
    assert history["messages"] == ["a", "b"]


def test_history_cap_overflow_via_settings(api_client, monkeypatch):
    monkeypatch.setattr(settings, "console_history_max", 2)
    for name in ["c1", "c2", "c3", "c4"]:
        user_console.print(name)

    overflowed = _history(api_client, since_id=0)
    assert overflowed["overflowed"] is True
    assert overflowed["messages"] == ["c3", "c4"]
    assert overflowed["last_id"] == 4

    resumed = _history(api_client, since_id=3)
    assert resumed["overflowed"] is False
    assert resumed["messages"] == ["c4"]
    assert resumed["last_id"] == 4


def test_history_with_non_server_impl(api_client):
    user_console.set_impl(ThreadSafeConsole())
    resp = api_client.get("/server/api/messages/history")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "messages": [],
        "last_id": 0,
        "has_more": False,
        "overflowed": False,
    }


def test_history_malformed_args_returns_400(api_client):
    resp = api_client.get("/server/api/messages/history?since_id=abc")
    assert resp.status_code == 400
    assert "error" in resp.get_json()

    resp = api_client.get("/server/api/messages/history?limit=xyz")
    assert resp.status_code == 400
    assert "error" in resp.get_json()

    resp = api_client.get("/server/api/messages/history?since_id=")
    assert resp.status_code == 200
    assert resp.get_json() == {
        "messages": [],
        "last_id": 0,
        "has_more": False,
        "overflowed": False,
    }
