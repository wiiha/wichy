"""Browser tests for the landing page and its /health-driven status pill.

These execute the real page in Chromium, replacing the old source-text guard that
only asserted the ordering of string literals in ``landing.html``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import Page, expect
import pytest

pytestmark = pytest.mark.e2e


def _goto(page: Page, base_url: str) -> None:
    page.goto(base_url + "/", wait_until="domcontentloaded")


def test_the_tool_cards_link_to_every_gui(page: Page, e2e_server: str) -> None:
    _goto(page, e2e_server)
    hrefs = page.locator(".tool-card").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    for path in ("/tools/notes/", "/tools/graph/", "/tools/context/", "/tools/data/"):
        assert path in hrefs, f"landing is missing a card for {path}"


def test_a_never_started_repl_session_reads_as_repl_active(
    page: Page,
    e2e_server: str,
    set_active_session: Callable[[Any], None],
    fake_session: Any,
) -> None:
    """thread_alive is null for a holder session; that must win over "Stopped"."""
    set_active_session(fake_session(alive=None, status="idle"))
    _goto(page, e2e_server)
    expect(page.locator("#statusPill")).to_have_attribute("data-state", "repl")
    expect(page.locator("#statusText")).to_have_text("REPL active")


def test_a_live_session_reads_as_running(
    page: Page,
    e2e_server: str,
    set_active_session: Callable[[Any], None],
    fake_session: Any,
) -> None:
    set_active_session(fake_session(alive=True, status="running"))
    _goto(page, e2e_server)
    expect(page.locator("#statusPill")).to_have_attribute("data-state", "running")
    expect(page.locator("#statusText")).to_have_text("Running")


def test_a_dead_session_reads_as_stopped(
    page: Page,
    e2e_server: str,
    set_active_session: Callable[[Any], None],
    fake_session: Any,
) -> None:
    set_active_session(fake_session(alive=False, status="stopped"))
    _goto(page, e2e_server)
    expect(page.locator("#statusPill")).to_have_attribute("data-state", "stopped")
    expect(page.locator("#statusText")).to_have_text("Stopped")


def test_health_reports_a_sessionless_repl_server(page: Page, e2e_server: str) -> None:
    """With no session set, /health reports a session-less server-mode contract.

    In repl mode /health reports thread_alive null, which the page renders as
    "REPL active" (covered above); the raw contract is what distinguishes a
    missing session, so it is asserted directly here.
    """
    health = page.request.get(e2e_server + "/health").json()
    assert health["mode"] == "repl"
    assert health["session"] == "none"
    assert health["chat_available"] is False
