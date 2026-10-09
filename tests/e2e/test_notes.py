"""Browser tests for the notes editor.

These exercise the real EditorJS-backed page in Chromium, replacing the
source-text guards that only grepped the template and JavaScript.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect
import pytest

pytestmark = pytest.mark.e2e

NOTES = "/tools/notes/"

#: The globals each vendored bundle defines and the editor config consumes.
EDITOR_GLOBALS = (
    "EditorJS",
    "Header",
    "List",
    "CodeTool",
    "Quote",
    "Checklist",
    "Delimiter",
)


def _open_notes(page: Page, base_url: str) -> None:
    page.goto(base_url + NOTES, wait_until="domcontentloaded")
    expect(page.locator("#btn-new-note")).to_be_visible()


def _create_note(page: Page) -> None:
    page.click("#btn-new-note")
    expect(page.locator("#note-editor")).to_be_visible()
    page.wait_for_selector("#block-editor .ce-block")


def test_a_new_note_opens_a_live_editor(
    page: Page, e2e_server: str, browser_errors: list[str]
) -> None:
    _open_notes(page, e2e_server)
    page.click("#btn-new-note")
    expect(page.locator("#note-editor")).to_be_visible()
    expect(page.locator("#block-editor .ce-block").first).to_be_visible()
    assert browser_errors == [], f"page raised errors: {browser_errors}"


def test_the_vendored_editor_globals_are_live(page: Page, e2e_server: str) -> None:
    """A re-vendor that renamed a global breaks the editor; assert it here."""
    _open_notes(page, e2e_server)
    page.wait_for_function("() => window.editorjsReady === true")
    live = page.evaluate("Object.keys(window)")
    for name in EDITOR_GLOBALS:
        assert name in live, f"global {name} is missing"
    assert page.evaluate("typeof window.WichyCustomBlocks") == "object"
    assert page.evaluate("window.editorjsMissing") == []


def test_the_new_note_appears_in_the_list(page: Page, e2e_server: str) -> None:
    _open_notes(page, e2e_server)
    assert page.locator(".note-item").count() == 0
    _create_note(page)
    expect(page.locator(".note-item").first).to_be_visible()


def test_pinning_a_note_sets_the_scratchpad(page: Page, e2e_server: str) -> None:
    _open_notes(page, e2e_server)
    _create_note(page)
    page.click("#btn-pin")
    primary = None
    for _ in range(20):
        primary = page.request.get(
            e2e_server + "/tools/notes/api/notes/scratchpad"
        ).json()
        if primary.get("primary"):
            break
        page.wait_for_timeout(100)
    assert primary and primary.get("primary"), "pinning did not set a scratchpad note"


def test_opening_a_note_enables_the_toolbar(page: Page, e2e_server: str) -> None:
    """Controls are disabled until a note is open; opening one enables them."""
    _open_notes(page, e2e_server)
    export = page.locator('#toolbar button[data-action="export"]')
    expect(export).to_be_disabled()
    _create_note(page)
    for action in ("export", "pin", "delete"):
        expect(page.locator(f'#toolbar button[data-action="{action}"]')).to_be_enabled()
    # A block-format note cannot be converted -- that is only for legacy markdown.
    expect(page.locator('#toolbar button[data-action="convert"]')).to_be_disabled()


def test_the_page_offers_its_review_and_history_surfaces(
    page: Page, e2e_server: str
) -> None:
    """The toolbar surfaces the notes feature needs are present on the served page."""
    _open_notes(page, e2e_server)
    expect(page.locator("#btn-proposals")).to_be_visible()
    expect(page.locator('#toolbar button[data-action="history"]')).to_be_visible()
    for selector in (
        "#conflict-banner",
        "#convert-modal",
        "#compare-modal",
        "#history-modal",
    ):
        expect(page.locator(selector)).to_be_attached()
        expect(page.locator(selector)).to_be_hidden()


def test_sidebar_renders_hostile_titles_inert(page: Page, e2e_server: str) -> None:
    """A note title with markup must render as literal text, not as HTML."""
    hostile = '<img src=x onerror="window.__pwned=1">'
    response = page.request.post(
        e2e_server + "/tools/notes/api/notes", data={"title": hostile}
    )
    assert response.ok, response.text()

    _open_notes(page, e2e_server)
    row = page.locator(".note-item").first
    expect(row).to_contain_text(hostile)
    assert page.locator(".note-item img").count() == 0
    assert page.evaluate("window.__pwned") is None
