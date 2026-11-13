"""HTTP contract tests for the pending input-queue API."""

from __future__ import annotations

from queue import Queue

import pytest
from flask import Blueprint, Flask

from wichy.wichy_server import api as server_api
from wichy.wichy_server.api import register_routes
from wichy.wichy_server.input_queue import TrackedInputQueue

NOT_FOUND = {"error": "message not found or no longer pending"}
NO_SESSION = {"error": "no active session"}
DEAD_THREAD = {"error": "session run thread is not alive"}
NO_QUEUE = {"error": "Server input not available: no active input queue"}


class _FakeThread:
    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _Session:
    """Session whose ``_thread`` a test can control directly."""

    def __init__(self, thread: object | None = None) -> None:
        self._thread = thread
        self.root_agent = None
        self.cmd_checker = None


@pytest.fixture
def api_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)
    server_api.set_active_session(None)
    server_api.set_input_queue(None)
    try:
        with app.test_client() as client:
            yield client
    finally:
        server_api.set_active_session(None)
        server_api.set_input_queue(None)


def _live(queue: TrackedInputQueue | None = None) -> tuple[_Session, TrackedInputQueue]:
    """Install a live session and (by default) a fresh tracked queue."""
    session = _Session(thread=_FakeThread(alive=True))
    q = TrackedInputQueue() if queue is None else queue
    server_api.set_active_session(session)
    server_api.set_input_queue(q)
    return session, q


def _put(client, message_id: str, body):
    return client.put(f"/server/api/messages/pending/{message_id}", **body)


def _pending(client) -> dict:
    resp = client.get("/server/api/messages/pending")
    assert resp.status_code == 200
    return resp.get_json()


def _lines(client) -> list[str]:
    return [entry["line"] for entry in _pending(client)["messages"]]


def _ids(client) -> list[str]:
    return [entry["id"] for entry in _pending(client)["messages"]]


def _positions(client) -> list[int]:
    return [entry["position"] for entry in _pending(client)["messages"]]


def _route_calls(client):
    return {
        "post": lambda: client.post("/server/api/messages", json={"line": "hi"}),
        "get": lambda: client.get("/server/api/messages/pending"),
        "put": lambda: client.put(
            "/server/api/messages/pending/some-id", json={"line": "hi"}
        ),
        "delete": lambda: client.delete("/server/api/messages/pending/some-id"),
    }


# -- Snapshots ---------------------------------------------------------------


def test_pending_empty_queue_returns_empty_list(api_client):
    _live()
    resp = api_client.get("/server/api/messages/pending")
    assert resp.status_code == 200
    assert resp.get_json() == {"messages": []}


def test_pending_snapshot_fifo_positions_repeatable_non_destructive(api_client):
    _, q = _live()
    ids = [q.put("first"), q.put("second"), q.put("third")]

    first = _pending(api_client)
    second = _pending(api_client)

    assert first == second
    assert [entry["line"] for entry in first["messages"]] == [
        "first",
        "second",
        "third",
    ]
    assert [entry["id"] for entry in first["messages"]] == ids
    assert [entry["position"] for entry in first["messages"]] == [0, 1, 2]
    assert q.qsize() == 3
    assert q.get_nowait() == "first"
    assert q.get_nowait() == "second"
    assert q.get_nowait() == "third"


# -- POST --------------------------------------------------------------------


def test_post_accepts_id_legacy_empty_object_and_verbatim_lines(api_client):
    _, q = _live()

    resp = api_client.post("/server/api/messages", json={"line": "hello"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert isinstance(body["id"], str) and body["id"]

    message_id = body["id"]
    assert _ids(api_client) == [message_id]
    assert _lines(api_client) == ["hello"]

    resp = _put(api_client, message_id, {"json": {"line": "edited"}})
    assert resp.status_code == 200
    assert q.get_nowait() == "edited"
    assert _pending(api_client) == {"messages": []}

    resp = api_client.post("/server/api/messages", json={})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"
    assert q.get_nowait() == ""

    resp = api_client.post("/server/api/messages", json={"line": "  keep me  "})
    assert resp.status_code == 200
    assert _lines(api_client) == ["  keep me  "]
    assert q.get_nowait() == "  keep me  "


# -- PUT success -------------------------------------------------------------


def test_put_success_exact_schema_text_position_and_delivery(api_client):
    _, q = _live()
    first_id = q.put("first")
    target_id = q.put("/help")
    last_id = q.put("last")

    resp = _put(api_client, target_id, {"json": {"line": "  /help verbose  "}})

    assert resp.status_code == 200
    assert resp.get_json() == {
        "status": "ok",
        "message": {
            "id": target_id,
            "line": "  /help verbose  ",
            "position": 1,
        },
    }
    assert _pending(api_client) == {
        "messages": [
            {"id": first_id, "line": "first", "position": 0},
            {"id": target_id, "line": "  /help verbose  ", "position": 1},
            {"id": last_id, "line": "last", "position": 2},
        ]
    }
    assert q.get_nowait() == "first"
    assert q.get_nowait() == "  /help verbose  "
    assert q.get_nowait() == "last"
    assert _pending(api_client) == {"messages": []}


# -- PUT validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="no-body"),
        pytest.param(
            {"data": "{not valid json", "content_type": "application/json"},
            id="malformed-json",
        ),
        pytest.param({"json": ["not", "an", "object"]}, id="list-body"),
        pytest.param({"json": {}}, id="missing-line"),
        pytest.param({"json": {"line": 123}}, id="non-string-line"),
        pytest.param({"json": {"line": "   "}}, id="blank-line"),
    ],
)
def test_put_invalid_body_returns_400_and_leaves_queue_unchanged(api_client, kwargs):
    _, q = _live()
    message_id = q.put("original")

    resp = _put(api_client, message_id, kwargs)

    assert resp.status_code == 400
    body = resp.get_json()
    assert isinstance(body.get("error"), str) and body["error"]
    assert _pending(api_client) == {
        "messages": [{"id": message_id, "line": "original", "position": 0}]
    }
    assert q.qsize() == 1
    assert q.get_nowait() == "original"


def test_put_invalid_body_precedes_unknown_id_lookup(api_client):
    _live()

    resp = _put(api_client, "no-such-id", {})

    assert resp.status_code == 400
    assert isinstance(resp.get_json().get("error"), str)


# -- DELETE success ----------------------------------------------------------


def test_delete_success_preserves_fifo_closes_positions_and_ignores_body(api_client):
    _, q = _live()
    first_id = q.put("first")
    target_id = q.put("/clear")
    last_id = q.put("last")

    resp = api_client.delete(
        f"/server/api/messages/pending/{target_id}",
        data="{definitely not json",
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}
    assert _pending(api_client) == {
        "messages": [
            {"id": first_id, "line": "first", "position": 0},
            {"id": last_id, "line": "last", "position": 1},
        ]
    }
    assert q.get_nowait() == "first"
    assert q.get_nowait() == "last"
    assert _pending(api_client) == {"messages": []}


# -- Unknown / deleted / consumed IDs ---------------------------------------


def test_unknown_id_put_and_delete_return_exact_404(api_client):
    _live()

    put_resp = _put(api_client, "unknown-id", {"json": {"line": "replacement"}})
    delete_resp = api_client.delete("/server/api/messages/pending/unknown-id")

    assert put_resp.status_code == 404
    assert put_resp.get_json() == NOT_FOUND
    assert delete_resp.status_code == 404
    assert delete_resp.get_json() == NOT_FOUND


def test_deleted_id_put_and_delete_return_exact_404_without_requeue(api_client):
    _, q = _live()
    message_id = q.put("to delete")
    kept_id = q.put("kept")

    assert (
        api_client.delete(f"/server/api/messages/pending/{message_id}").status_code
        == 200
    )

    put_resp = _put(api_client, message_id, {"json": {"line": "revived?"}})
    delete_resp = api_client.delete(f"/server/api/messages/pending/{message_id}")

    assert put_resp.status_code == 404
    assert put_resp.get_json() == NOT_FOUND
    assert delete_resp.status_code == 404
    assert delete_resp.get_json() == NOT_FOUND
    assert _pending(api_client) == {
        "messages": [{"id": kept_id, "line": "kept", "position": 0}]
    }
    assert q.qsize() == 1
    assert q.get_nowait() == "kept"


def test_consumed_id_put_and_delete_return_exact_404_without_requeue(api_client):
    _, q = _live()

    put_id = q.put("put-consumed")
    put_text = q.get_nowait()
    assert put_text == "put-consumed"
    put_resp = _put(api_client, put_id, {"json": {"line": "edited too late"}})

    delete_id = q.put("delete-consumed")
    delete_text = q.get_nowait()
    assert delete_text == "delete-consumed"
    delete_resp = api_client.delete(f"/server/api/messages/pending/{delete_id}")

    assert put_resp.status_code == 404
    assert put_resp.get_json() == NOT_FOUND
    assert delete_resp.status_code == 404
    assert delete_resp.get_json() == NOT_FOUND
    assert put_text == "put-consumed"
    assert delete_text == "delete-consumed"
    assert _pending(api_client) == {"messages": []}
    assert q.qsize() == 0


# -- Readiness matrix and precedence ---------------------------------------


def test_readiness_errors_are_route_independent_and_precedence_ordered(api_client):
    states = [
        ("no-session", None, TrackedInputQueue(), NO_SESSION),
        ("missing-thread", _Session(thread=None), None, DEAD_THREAD),
        ("dead-thread", _Session(thread=_FakeThread(alive=False)), None, DEAD_THREAD),
        ("alive-no-queue", _Session(thread=_FakeThread(alive=True)), None, NO_QUEUE),
    ]
    calls = _route_calls(api_client)

    for _name, session, queue, expected in states:
        for route, call in calls.items():
            server_api.set_active_session(session)
            server_api.set_input_queue(queue)
            resp = call()
            assert resp.status_code == 503, route
            assert resp.get_json() == expected, route

    server_api.set_active_session(None)
    server_api.set_input_queue(None)


def test_readiness_guard_prevents_queue_mutation(api_client):
    _, q = _live()
    message_id = q.put("kept")
    server_api.set_input_queue(None)

    put_resp = _put(api_client, message_id, {"json": {"line": "nope"}})
    delete_resp = api_client.delete(f"/server/api/messages/pending/{message_id}")

    assert put_resp.status_code == 503
    assert put_resp.get_json() == NO_QUEUE
    assert delete_resp.status_code == 503
    assert delete_resp.get_json() == NO_QUEUE

    server_api.set_input_queue(q)
    assert _pending(api_client) == {
        "messages": [{"id": message_id, "line": "kept", "position": 0}]
    }


# -- Setter contract ---------------------------------------------------------


def test_set_input_queue_contract(api_client):
    q = TrackedInputQueue()
    server_api.set_input_queue(q)
    assert server_api.get_input_queue() is q
    server_api.set_input_queue(None)
    assert server_api.get_input_queue() is None

    with pytest.raises(TypeError):
        server_api.set_input_queue(Queue())
