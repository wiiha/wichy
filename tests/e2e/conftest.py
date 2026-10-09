"""Harness for the Playwright browser tests.

The real Flask app is booted once per session: its blueprints are module-level and
may be registered only once per process. Data isolation comes from running in a
temp CWD, because the note/graph/log dirs are resolved relative to CWD; the
per-test fixture clears that data so tests from a known empty state.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from werkzeug.serving import make_server


class _FakeThread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _FakeSession:
    """Minimal ChatSession stand-in: /health reads ``_thread`` and ``status()``."""

    def __init__(self, thread: _FakeThread | None, status: str) -> None:
        self._thread = thread
        self._status = status

    def status(self) -> dict[str, object]:
        return {"status": self._status, "last_line_seen": 0.0, "turn_seconds": 0.0}


def _wait_for_health(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(f"server at {base_url} never became healthy")


@pytest.fixture(scope="session")
def e2e_server() -> Iterator[str]:
    original_cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="wichy-e2e-")
    os.chdir(tmp)
    for sub in ("notes", "graphs", "logs"):
        (Path(tmp) / ".wichy" / sub).mkdir(parents=True, exist_ok=True)

    from wichy.server import create_app

    app = create_app(no_chat=True, mode="repl")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    _wait_for_health(base_url)
    try:
        yield base_url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        os.chdir(original_cwd)


@pytest.fixture(autouse=True)
def _clean_state(e2e_server: str) -> Iterator[None]:
    """Reset in-memory note state and wipe on-disk notes/graphs per test."""
    from wichy.tools.notes.state import reset_state

    reset_state()
    for name in ("notes", "graphs"):
        target = Path(".wichy") / name
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
    yield
    reset_state()


@pytest.fixture(scope="session")
def _browser(e2e_server: str) -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def browser_errors() -> list[str]:
    return []


@pytest.fixture
def page(_browser: Any, browser_errors: list[str]) -> Iterator[Any]:
    context = _browser.new_context()
    browser_page = context.new_page()
    browser_page.on("pageerror", lambda exc: browser_errors.append(str(exc)))
    yield browser_page
    context.close()


@pytest.fixture
def set_active_session() -> Iterator[Callable[[Any], None]]:
    from wichy.wichy_server import api as server_api

    def _set(session: Any) -> None:
        server_api.set_active_session(session)

    yield _set
    server_api.set_active_session(None)


@pytest.fixture
def fake_session() -> Callable[..., _FakeSession]:
    def _make(*, alive: bool | None, status: str = "idle") -> _FakeSession:
        thread = None if alive is None else _FakeThread(alive)
        return _FakeSession(thread, status)

    return _make
