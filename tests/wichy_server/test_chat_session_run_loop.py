"""Tests for ChatSession.run() resilience and its status() reporting.

The loop is driven for real: a stub root agent, a real input queue, and the
real thread. Printed output is observed through ServerConsole, the only
capture path -- user_console is a proxy whose get_messages() returns [] for
any other implementation.
"""

import json
import threading
import time
from queue import Queue

import pytest
from flask import Blueprint, Flask

from wichy.console.user import ServerConsole, user_console
from wichy.helpers.shutdown import shutdown_requested
from wichy.server import create_app
from wichy.wichy_server import api as server_api
from wichy.wichy_server import chat_session as chat_session_module
from wichy.wichy_server.api import register_routes
from wichy.wichy_server.chat_session import ChatSession


class _StubChecker:
    def check_command(self, line):
        return None


class _StubRootAgent:
    def __init__(self, behavior=None, first_initiative=False, display_name="Stub"):
        self.agent_has_first_initiative = first_initiative
        self.display_name = display_name
        self._behavior = behavior

    def process(self, line):
        if self._behavior is not None:
            return self._behavior(line)
        return "ok"


class _FakeThread:
    def __init__(self, alive=True):
        self._alive = alive
        self.joins = []

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.joins.append(timeout)


def _make_session(behavior=None, first_initiative=False):
    return ChatSession(
        root_agent=_StubRootAgent(behavior, first_initiative),
        cmd_checker=_StubChecker(),
    )


def _drain_until(console, predicate, timeout=3.0):
    """Accumulate output until predicate holds or the deadline passes."""
    deadline = time.monotonic() + timeout
    collected = []
    while time.monotonic() < deadline:
        collected.extend(console.get_messages())
        if predicate(collected):
            return collected
        time.sleep(0.02)
    return collected


@pytest.fixture
def server_console():
    prior = user_console._impl
    user_console.set_impl(ServerConsole())
    try:
        yield user_console
    finally:
        user_console.set_impl(prior)


@pytest.fixture(autouse=True)
def _clear_shutdown_flag():
    shutdown_requested.clear()
    yield
    shutdown_requested.clear()


# -- INV-001: unexpected Exception is printed and the loop continues ---------


def test_exception_is_printed_and_loop_survives(server_console):
    def behave(line):
        if line == "boom":
            raise RuntimeError("kaboom")
        return "processed"

    session = _make_session(behave)
    session.start()
    try:
        session.input_queue.put("boom")
        got = _drain_until(
            server_console, lambda ms: any("unexpected failure" in m for m in ms)
        )
        assert any("kaboom" in m for m in got)
        assert session._thread.is_alive()

        session.input_queue.put("hello")
        got = _drain_until(server_console, lambda ms: any("### Stub" in m for m in got))
        assert any("### Stub" in m for m in got)
    finally:
        session.stop()


# -- INV-002: BaseException (SystemExit / KeyboardInterrupt) is absorbed ------


@pytest.mark.parametrize("exc", [SystemExit, KeyboardInterrupt])
def test_base_exception_is_absorbed(server_console, exc):
    raised = {"count": 0}

    def behave(line):
        raised["count"] += 1
        if raised["count"] == 1:
            raise exc()
        return "processed"

    session = _make_session(behave)
    session.start()
    try:
        session.input_queue.put("boom")
        got = _drain_until(
            server_console, lambda ms: any("unexpected failure" in m for m in ms)
        )
        assert any(type(exc()).__name__ in m for m in got)
        assert session._thread.is_alive()
    finally:
        session.stop()


# -- INV-006: a wake-up failure does not zombify the session -----------------


def test_wake_up_failure_does_not_zombify(server_console):
    def behave(line):
        if line == "wake":
            raise RuntimeError("wake failed")
        return "processed"

    session = _make_session(behave, first_initiative=True)
    original = chat_session_module.settings.wake_up_message
    chat_session_module.settings.wake_up_message = "wake"
    session.start()
    try:
        got = _drain_until(
            server_console, lambda ms: any("wake failed" in m for m in ms)
        )
        assert any("wake failed" in m for m in got)
        assert session._thread.is_alive()

        session.input_queue.put("hello")
        got = _drain_until(server_console, lambda ms: any("### Stub" in m for m in got))
        assert any("### Stub" in m for m in got)
    finally:
        session.stop()
        chat_session_module.settings.wake_up_message = original


# -- INV-003: EOFError prints and sets the single shutdown flag --------------


def test_eof_error_sets_shutdown_flag(server_console):
    def behave(line):
        raise EOFError()

    session = _make_session(behave)
    session.start()
    try:
        session.input_queue.put("x")
        got = _drain_until(
            server_console, lambda ms: any("exiting..." in m for m in ms)
        )
        assert any("exiting..." in m for m in got)
        assert shutdown_requested.is_set()
    finally:
        session.stop()


# -- INV-004: stop() bounds the join without hanging -------------------------


def test_stop_passes_timeout_to_join():
    session = _make_session()
    session._thread = _FakeThread(alive=False)
    session.stop()
    assert session._thread.joins == [5.0]
    session.stop(None)
    assert session._thread.joins[-1] is None
    session.stop(0.1)
    assert session._thread.joins[-1] == 0.1


def test_stop_without_thread_is_a_noop():
    session = _make_session()
    session.stop()


# -- INV-007: repetition guard ----------------------------------------------


def test_repeat_guard_collapses_after_threshold(server_console):
    session = _make_session()
    for _ in range(chat_session_module._REPEAT_COLLAPSE_AFTER + 3):
        session._print_failure(RuntimeError("same"))
    got = server_console.get_messages()
    assert any("(repeated)" in m for m in got)


def test_repeat_guard_resets_on_different_exception(server_console):
    session = _make_session()
    for _ in range(chat_session_module._REPEAT_COLLAPSE_AFTER + 1):
        session._print_failure(RuntimeError("same"))
    server_console.get_messages()
    session._print_failure(ValueError("different"))
    got = server_console.get_messages()
    assert any("unexpected failure" in m for m in got)
    assert not any("(repeated)" in m for m in got)


# -- status() (AMEND-1 thread-state rule) -----------------------------------


def test_status_none_when_never_started():
    assert _make_session().status()["status"] == "none"


def test_status_stopped_when_thread_dead():
    session = _make_session()
    session._thread = _FakeThread(alive=False)
    assert session.status()["status"] == "stopped"


def test_status_stopping_when_stop_requested_but_alive():
    session = _make_session()
    session._thread = _FakeThread(alive=True)
    session._stop_event.set()
    assert session.status()["status"] == "stopping"


def test_status_idle_running():
    session = _make_session()
    session._thread = _FakeThread(alive=True)
    assert session.status()["status"] == "idle"

    session._turn_started_at = time.monotonic()
    assert session.status()["status"] == "running"


def test_status_long_turn_with_queued_message_is_still_running():
    """A turn over the old 60s watchdog must not read as something is wrong."""
    session = _make_session()
    session._thread = _FakeThread(alive=True)
    session.input_queue.put("pending")
    session._turn_started_at = time.monotonic() - 120.0
    session.last_line_seen = time.monotonic() - 120.0
    state = session.status()
    assert state["status"] == "running"
    assert state["turn_seconds"] >= 120.0


def test_status_turn_seconds_zero_when_idle():
    session = _make_session()
    session._thread = _FakeThread(alive=True)
    assert session.status()["turn_seconds"] == 0.0


def test_status_running_while_real_turn_in_flight():
    """The loop itself marks the turn, and clears the mark when it returns."""
    entered = threading.Event()
    release = threading.Event()

    def behavior(line):
        entered.set()
        release.wait(timeout=3.0)
        return "ok"

    session = _make_session(behavior)
    session.start()
    try:
        session.input_queue.put("go")
        assert entered.wait(timeout=3.0)
        state = session.status()
        assert state["status"] == "running"
        assert state["turn_seconds"] > 0.0

        release.set()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and session.status()["status"] != "idle":
            time.sleep(0.02)
        assert session.status()["status"] == "idle"
        assert session.status()["turn_seconds"] == 0.0
    finally:
        release.set()
        session.stop()


# -- Stage 2: server surface (INV-005, INV-024, INV-025, INV-026) -----------


class _Session:
    """Session whose _thread and status() a test can set directly."""

    def __init__(self, thread=None, status="none"):
        self._thread = thread
        self.root_agent = None
        self.cmd_checker = None
        self._status = status

    def status(self):
        return {"status": self._status, "last_line_seen": 0.0, "turn_seconds": 0.0}


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


def test_post_messages_no_session_503(api_client):
    resp = api_client.post("/server/api/messages", json={"line": "hi"})
    assert resp.status_code == 503
    assert json.loads(resp.data)["error"] == "no active session"


def test_post_messages_never_started_503(api_client):
    server_api.set_active_session(_Session(thread=None))
    server_api.set_input_queue(Queue())
    resp = api_client.post("/server/api/messages", json={"line": "hi"})
    assert resp.status_code == 503
    assert json.loads(resp.data)["error"] == "session run thread is not alive"


def test_post_messages_dead_thread_503(api_client):
    fake = _FakeThread(alive=False)
    server_api.set_active_session(_Session(thread=fake))
    server_api.set_input_queue(Queue())
    resp = api_client.post("/server/api/messages", json={"line": "hi"})
    assert resp.status_code == 503
    assert json.loads(resp.data)["error"] == "session run thread is not alive"


def test_post_messages_alive_but_no_queue_503(api_client):
    server_api.set_active_session(_Session(thread=_FakeThread(alive=True)))
    server_api.set_input_queue(None)
    resp = api_client.post("/server/api/messages", json={"line": "hi"})
    assert resp.status_code == 503
    assert "no active input queue" in json.loads(resp.data)["error"]


def test_post_messages_accepts_when_live(api_client):
    session = _Session(thread=_FakeThread(alive=True))
    q: Queue = Queue()
    server_api.set_active_session(session)
    server_api.set_input_queue(q)
    resp = api_client.post("/server/api/messages", json={"line": "hi"})
    assert resp.status_code == 200
    assert q.get_nowait() == "hi"


def _health_client(monkeypatch, mode, session):
    # create_app registers module-level blueprints that may only be
    # registered once per process; /health is added directly to the app,
    # so stub blueprint registration to keep every call independent.
    monkeypatch.setattr(
        "wichy.server.register_blueprints", lambda app, no_chat=False: None
    )
    app = create_app(no_chat=mode == "repl", mode=mode)
    server_api.set_active_session(session)
    return app


def test_health_no_session(monkeypatch):
    app = _health_client(monkeypatch, "server", None)
    monkeypatch.setattr(server_api, "get_active_session", lambda: None)
    with app.test_client() as client:
        data = json.loads(client.get("/health").data)
    assert data["thread_alive"] is None
    assert data["session_status"] == "none"
    server_api.set_active_session(None)


def test_health_repl_never_started_reports_null(monkeypatch):
    app = _health_client(monkeypatch, "repl", _Session(thread=None, status="none"))
    with app.test_client() as client:
        data = json.loads(client.get("/health").data)
    assert data["thread_alive"] is None
    assert data["session_status"] == "none"
    assert data["mode"] == "repl"
    server_api.set_active_session(None)


def test_health_server_stopped_reports_false(monkeypatch):
    app = _health_client(
        monkeypatch,
        "server",
        _Session(thread=_FakeThread(alive=False), status="stopped"),
    )
    with app.test_client() as client:
        data = json.loads(client.get("/health").data)
    assert data["thread_alive"] is False
    assert data["session_status"] == "stopped"
    server_api.set_active_session(None)


@pytest.mark.parametrize("state", ["running", "idle", "stopping"])
def test_health_reports_active_states(monkeypatch, state):
    app = _health_client(
        monkeypatch, "server", _Session(thread=_FakeThread(alive=True), status=state)
    )
    with app.test_client() as client:
        data = json.loads(client.get("/health").data)
    assert data["thread_alive"] is True
    assert data["session_status"] == state
    server_api.set_active_session(None)


def test_repeat_guard_resets_on_processed_message():
    """A successfully processed line clears the repeat counter."""

    def behave(line):
        if line == "bad":
            raise RuntimeError("same")
        return "processed"

    session = _make_session(behave)
    session.start()
    try:
        for _ in range(chat_session_module._REPEAT_COLLAPSE_AFTER + 5):
            session.input_queue.put("bad")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and session._repeat_count <= 10:
            time.sleep(0.02)
        assert session._repeat_count > 10

        session.input_queue.put("good")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and session._repeat_count != 0:
            time.sleep(0.02)
        assert session._repeat_count == 0
        assert session._last_error_type is None
    finally:
        session.stop()
