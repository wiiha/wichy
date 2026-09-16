"""Tests for the tool-call kill registry endpoints in wichy_server/api.py.

The routes are registry-backed and intentionally carry NO active-session
guard: the registry is process-global, so the endpoints must work in
both server mode and REPL-companion mode (where no ChatSession exists).
"""

import json
import threading

import pytest
from flask import Blueprint, Flask

from wichy.tools import kill_registry
from wichy.tools.kill_registry import _reset_for_tests
from wichy.wichy_server.api import register_routes
from wichy.tools.context_editor.api import register_routes as register_editor_routes


@pytest.fixture(autouse=True)
def clean_registry():
    """Give every test a pristine kill registry."""
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def client():
    """Provide a Flask test client with a fresh app and blueprint.

    No ChatSession is ever registered: the endpoints must not depend on
    one (that is the point of the registry-backed design).
    """
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("wichy_server_api", __name__, url_prefix="/server/api")
    register_routes(bp)
    app.register_blueprint(bp)

    with app.test_client() as client:
        yield client


@pytest.fixture
def editor_client():
    """Context editor blueprint client (own-prefix mirror routes)."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    bp = Blueprint("context_editor", __name__, url_prefix="/tools/context")
    register_editor_routes(bp)
    app.register_blueprint(bp)

    with app.test_client() as client:
        yield client


class _Unprintable:
    """A value whose __str__ raises; _safe_args must tolerate it."""

    def __str__(self):
        raise ValueError("no string for you")


def _register_in_flight(
    tool_call_id: str = "call-1",
    tool_name: str = "bash",
    arguments: dict | None = None,
    agent_id: str = "root",
) -> None:
    """Fake an in-flight call directly in the registry."""
    kill_registry.register(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments if arguments is not None else {"command": "sleep 30"},
        agent_id=agent_id,
    )


class TestGetToolCalls:
    def test_empty_registry_lists_nothing(self, client):
        response = client.get("/server/api/tool-calls")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["tool_calls"] == []
        assert data["killed"] == []

    def test_lists_in_flight_call_snapshot(self, client):
        _register_in_flight(
            tool_call_id="call-1",
            tool_name="bash",
            arguments={"command": "sleep 30"},
            agent_id="root",
        )

        response = client.get("/server/api/tool-calls")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data["tool_calls"]) == 1
        entry = data["tool_calls"][0]
        assert entry["tool_call_id"] == "call-1"
        assert entry["tool_name"] == "bash"
        assert entry["agent_id"] == "root"
        assert entry["status"] == "running"
        assert entry["arguments"] == {"command": "sleep 30"}
        assert entry["duration_s"] >= 0

    def test_lists_task_agent_call_with_agent_id(self, client):
        _register_in_flight(
            tool_call_id="call-2",
            tool_name="glob",
            arguments={"pattern": "**/*.py"},
            agent_id="task_abc123",
        )

        response = client.get("/server/api/tool-calls")
        data = json.loads(response.data)
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["agent_id"] == "task_abc123"

    def test_hidden_kwargs_excluded_from_listing(self, client):
        _register_in_flight(
            tool_call_id="call-3",
            arguments={
                "command": "ls",
                "_tool_call_id": "call-3",
                "_agent_id": "root",
            },
        )

        response = client.get("/server/api/tool-calls")
        data = json.loads(response.data)
        entry = data["tool_calls"][0]
        assert entry["arguments"] == {"command": "ls"}

    def test_hostile_arguments_do_not_crash_listing(self, client):
        # Non-serializable values, a misbehaving __str__, and non-string
        # keys must not 500 the listing (the route has no try/except;
        # _safe_args is the only defense).
        _register_in_flight(
            tool_call_id="call-4",
            tool_name="glob",
            arguments={
                "weird": {"nested": object()},
                "broken": _Unprintable(),
                42: "int key",
            },
        )

        response = client.get("/server/api/tool-calls")
        assert response.status_code == 200
        entry = json.loads(response.data)["tool_calls"][0]
        assert entry["arguments"]["broken"] == "<unrepresentable>"
        assert entry["arguments"]["42"] == "int key"
        assert "weird" in entry["arguments"]

    def test_unregistered_call_disappears_from_listing(self, client):
        _register_in_flight(tool_call_id="call-1")
        kill_registry.unregister("call-1")

        response = client.get("/server/api/tool-calls")
        data = json.loads(response.data)
        assert data["tool_calls"] == []

    def test_killed_ids_listed_from_history(self, client):
        _register_in_flight(tool_call_id="call-1")
        kill_registry.request_kill("call-1", reason="too broad")
        # Killed entries move to the bounded history only when the call
        # finishes; while in flight they still appear (as "killed").
        response = client.get("/server/api/tool-calls")
        data = json.loads(response.data)
        assert data["tool_calls"][0]["status"] == "killed"

        kill_registry.finish_call("call-1")
        response = client.get("/server/api/tool-calls")
        data = json.loads(response.data)
        assert data["tool_calls"] == []
        assert data["killed"] == ["call-1"]


class TestKillToolCall:
    def test_kill_unknown_id_404(self, client):
        response = client.post(
            "/server/api/tool-calls/nope-123/kill", json={"reason": "b"}
        )
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data["error"] == "unknown tool call id"

    def test_kill_in_flight_call(self, client):
        _register_in_flight(tool_call_id="call-1", tool_name="glob")

        response = client.post(
            "/server/api/tool-calls/call-1/kill", json={"reason": "too slow"}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert data["killed"] is True
        assert data["reason"] == "too slow"

        # The registry mark must be visible to the executing thread.
        assert kill_registry.was_killed("call-1")
        assert kill_registry.killed_reason("call-1") == "too slow"

    def test_kill_without_reason(self, client):
        _register_in_flight(tool_call_id="call-1")

        response = client.post("/server/api/tool-calls/call-1/kill")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["killed"] is True
        assert data["reason"] is None
        assert kill_registry.killed_reason("call-1") is None

    def test_kill_with_non_string_reason_400(self, client):
        _register_in_flight(tool_call_id="call-1")

        response = client.post(
            "/server/api/tool-calls/call-1/kill", json={"reason": 42}
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert data["error"] == "reason must be a string"
        # Not marked: the request was malformed, not a kill.
        assert kill_registry.was_killed("call-1") is False

    def test_kill_already_killed_call_is_idempotent(self, client):
        _register_in_flight(tool_call_id="call-1")

        first = client.post("/server/api/tool-calls/call-1/kill")
        assert json.loads(first.data)["killed"] is True

        second = client.post("/server/api/tool-calls/call-1/kill")
        assert second.status_code == 200
        data = json.loads(second.data)
        assert data["killed"] is True

    def test_kill_finished_killed_call_is_race_tolerant_noop(self, client):
        # A call that finished after being killed is known to the
        # bounded history -> 200 with killed:false, never 404.
        _register_in_flight(tool_call_id="call-1")
        kill_registry.request_kill("call-1", reason="stop")
        kill_registry.finish_call("call-1")

        response = client.post("/server/api/tool-calls/call-1/kill")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert data["killed"] is False
        assert data["note"] == "call already finished"

    def test_kill_normally_finished_call_is_race_tolerant_noop(self, client):
        # The stale-UI-card race: a call finishes NORMALLY between the
        # user's listing and their kill click. The registry saw the id,
        # so the kill must answer 200 killed:false, never 404.
        _register_in_flight(tool_call_id="call-1", tool_name="glob")
        kill_registry.finish_call("call-1")

        response = client.post(
            "/server/api/tool-calls/call-1/kill", json={"reason": "too slow"}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert data["killed"] is False
        assert data["note"] == "call already finished"

    def test_kill_null_reason_behaves_like_missing(self, client):
        _register_in_flight(tool_call_id="call-1")

        response = client.post(
            "/server/api/tool-calls/call-1/kill", json={"reason": None}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["killed"] is True
        assert data["reason"] is None
        assert kill_registry.killed_reason("call-1") is None

    def test_kill_blank_reason_normalized_to_none(self, client):
        _register_in_flight(tool_call_id="call-1")

        response = client.post(
            "/server/api/tool-calls/call-1/kill", json={"reason": "   "}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["killed"] is True
        # Registry and response agree: no empty-string reason stored
        # (the LLM kill notice omits falsy reasons).
        assert data["reason"] is None
        assert kill_registry.killed_reason("call-1") is None

    def test_kill_never_registered_id_404(self, client):
        response = client.post("/server/api/tool-calls/never-registered/kill")
        assert response.status_code == 404


class TestKillAgainstRealExecution:
    """End-to-end: kill a call executing on a real worker thread."""

    def test_kill_releases_executing_thread(self, client):
        started = threading.Event()
        release = threading.Event()
        outcome: list[str] = []

        # The registry captures the executing thread at register time,
        # so register must happen on the thread that runs execute().
        th = threading.Thread(
            target=self._run_and_register,
            args=(started, release, outcome),
            daemon=True,
        )
        th.start()
        assert started.wait(timeout=5), "worker thread never started"

        response = client.post(
            "/server/api/tool-calls/call-1/kill", json={"reason": "too broad"}
        )
        assert response.status_code == 200
        assert json.loads(response.data)["killed"] is True

        th.join(timeout=15)
        assert not th.is_alive(), "killed thread must exit"
        # The async-raise monitor must have delivered ToolKilledError
        # into the blocked Event.wait (cond waits poll + reacquire the
        # GIL, so they are interruptible; time.sleep is NOT).
        assert outcome[0] == "ToolKilledError"
        # And the call moved to the killed history once it finished.
        assert "call-1" in kill_registry.list_killed_ids()
        # Straggler re-deliveries landing after finish_call are
        # tolerated by design (the mark wins); they may or may not
        # appear as a second outcome entry.

    @staticmethod
    def _run_and_register(started, release, outcome):
        kill_registry.register(
            tool_call_id="call-1",
            tool_name="glob",
            arguments={"pattern": "**/*.py"},
            agent_id="root",
        )
        try:
            try:
                started.set()
                # Poll with a SHORT wait: async-raise lands only at a
                # bytecode boundary, so a single long cond wait would
                # park the pending exception until timeout expiry. Each
                # 0.2s tick returns to Python bytecode and the kill pops.
                while not release.wait(timeout=0.2):
                    pass
                outcome.append("returned")
            except BaseException as exc:
                outcome.append(type(exc).__name__)
            finally:
                kill_registry.finish_call("call-1")
        except BaseException as exc:
            # A monitor straggler may land in the tiny tail after the
            # call finished; the design accepts this (the mark wins).
            outcome.append(f"straggler:{type(exc).__name__}")

    def test_status_flips_to_killed_in_listing(self, client):
        kill_registry.register(
            tool_call_id="call-1",
            tool_name="bash",
            arguments={"command": "sleep 300"},
            agent_id="root",
        )
        try:
            before = client.get("/server/api/tool-calls")
            assert json.loads(before.data)["tool_calls"][0]["status"] == "running"

            response = client.post(
                "/server/api/tool-calls/call-1/kill", json={"reason": "r"}
            )
            assert response.status_code == 200

            after = client.get("/server/api/tool-calls")
            entry = json.loads(after.data)["tool_calls"][0]
            assert entry["status"] == "killed"
        finally:
            kill_registry.finish_call("call-1")


class TestContextEditorMirror:
    """The context editor blueprint mirrors the kill routes under its own
    prefix, backed by the SAME in-process registry (own-prefix-only
    convention; no active-context state involved)."""

    def test_lists_in_flight(self, editor_client):
        _register_in_flight(
            tool_call_id="call-1",
            tool_name="bash",
            arguments={"command": "sleep 30"},
        )

        response = editor_client.get("/tools/context/api/tool-calls")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data["tool_calls"]) == 1
        assert data["tool_calls"][0]["tool_call_id"] == "call-1"
        assert data["killed"] == []

    def test_kill_in_flight(self, editor_client):
        _register_in_flight(tool_call_id="call-1", tool_name="glob")

        response = editor_client.post(
            "/tools/context/api/tool-calls/call-1/kill",
            json={"reason": "too broad"},
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["status"] == "ok"
        assert data["killed"] is True
        assert data["reason"] == "too broad"
        assert kill_registry.was_killed("call-1")

    def test_kill_unknown_404(self, editor_client):
        response = editor_client.post("/tools/context/api/tool-calls/nope/kill")
        assert response.status_code == 404

    def test_normally_finished_race_tolerant_200(self, editor_client):
        _register_in_flight(tool_call_id="call-1")
        kill_registry.finish_call("call-1")

        response = editor_client.post("/tools/context/api/tool-calls/call-1/kill")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["killed"] is False
        assert data["note"] == "call already finished"

    def test_blank_reason_normalized(self, editor_client):
        _register_in_flight(tool_call_id="call-1")

        response = editor_client.post(
            "/tools/context/api/tool-calls/call-1/kill", json={"reason": ""}
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["killed"] is True
        assert data["reason"] is None

    def test_non_string_reason_400(self, editor_client):
        _register_in_flight(tool_call_id="call-1")

        response = editor_client.post(
            "/tools/context/api/tool-calls/call-1/kill", json={"reason": []}
        )
        assert response.status_code == 400
        assert kill_registry.was_killed("call-1") is False
