"""
Test cases for the BrowserManager helper.

Tests cover:
- Browser initialization
- Page creation and recreation when closed
- Context handling (BrowserContext doesn't have is_closed() method)
- Navigation, status, and screenshot functionality

Key bugs that were fixed:
1. BrowserContext does NOT have is_closed() method, only Page does
2. Page.is_closed() is a METHOD, not a property (must be called with ())

Note: These tests use execute_serialized() because the event loop now runs
in a background thread (required for asyncio.run_coroutine_threadsafe()).
"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch


class EventLoopFixture:
    """Helper to manage a running event loop in a background thread."""

    def __init__(self):
        self.loop = None
        self.thread = None
        self._ready_event = None

    def start(self):
        """Start the event loop in a background thread."""
        self._ready_event = threading.Event()

        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._ready_event.set()
            self.loop.run_forever()

        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()
        self._ready_event.wait(timeout=5.0)  # Wait for loop to be ready

    def stop(self):
        """Stop the event loop and clean up."""
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=2.0)


class TestBrowserManagerInitialization:
    """Tests for BrowserManager initialization."""

    def test_singleton_returns_same_instance(self):
        """Test that BrowserManager is a singleton."""
        from wichy.helpers.browser import BrowserManager

        # Reset singleton
        BrowserManager._instance = None

        manager1 = BrowserManager()
        manager2 = BrowserManager()

        assert manager1 is manager2

        # Cleanup
        BrowserManager._instance = None

    def test_initialize_creates_browser_and_page(self):
        """Test that initialize creates browser, context, and page."""
        from wichy.helpers.browser import BrowserManager

        # Reset singleton
        BrowserManager._instance = None

        mock_playwright = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        # is_closed is a METHOD in Playwright that returns bool
        mock_page.is_closed = MagicMock(return_value=False)

        manager = BrowserManager()

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            with patch("wichy.helpers.browser.async_playwright") as mock_async_playwright:
                mock_async_playwright.return_value.__aenter__ = AsyncMock(
                    return_value=mock_playwright
                )
                mock_playwright.chromium.launch.return_value = mock_browser
                mock_browser.new_context.return_value = mock_context
                mock_context.new_page.return_value = mock_page

                # Use execute_serialized for async operation
                result = manager.execute_serialized(lambda: manager.initialize(), timeout=5.0)

                assert manager._browser is mock_browser
                assert manager._context is mock_context
                assert manager._page is mock_page
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestBrowserManagerGetPage:
    """Tests for get_page method - this is where the is_closed() bug was."""

    def test_get_page_returns_existing_page_when_not_closed(self):
        """Test that get_page returns existing page if it's not closed."""
        from wichy.helpers.browser import BrowserManager

        # Reset singleton
        BrowserManager._instance = None

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        # is_closed is a METHOD in Playwright that returns bool
        mock_page.is_closed = MagicMock(return_value=False)

        manager = BrowserManager()
        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(lambda: manager.get_page(), timeout=5.0)

            assert result is mock_page
            # Should NOT create new page since existing one is valid
            mock_context.new_page.assert_not_called()
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_get_page_creates_new_page_when_closed(self):
        """Test that get_page creates new page when existing page is closed."""
        from wichy.helpers.browser import BrowserManager

        # Reset singleton
        BrowserManager._instance = None

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        # is_closed is a METHOD in Playwright that returns bool
        mock_page.is_closed = MagicMock(return_value=True)

        new_page = AsyncMock()
        new_page.is_closed = MagicMock(return_value=False)
        mock_context.new_page.return_value = new_page

        manager = BrowserManager()
        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(lambda: manager.get_page(), timeout=5.0)

            # Should create new page on existing context
            mock_context.new_page.assert_called_once()
            assert result is new_page
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_get_page_creates_new_context_when_none(self):
        """Test that get_page creates new context when context is None."""
        from wichy.helpers.browser import BrowserManager

        # Reset singleton
        BrowserManager._instance = None

        mock_browser = AsyncMock()

        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        manager = BrowserManager()
        manager._browser = mock_browser
        manager._context = None  # Context is None
        manager._page = None

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(lambda: manager.get_page(), timeout=5.0)

            # Should create new context
            mock_browser.new_context.assert_called_once()
            assert manager._context is mock_context
            assert result is mock_page
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_get_page_recreates_context_when_invalid(self):
        """Test that get_page recreates context when existing context is invalid/closed."""
        from wichy.helpers.browser import BrowserManager

        # Reset singleton
        BrowserManager._instance = None

        mock_browser = AsyncMock()

        # Create a context that will throw an error when trying to create a page
        # This simulates a closed/invalid context
        broken_context = AsyncMock()
        broken_context.new_page = AsyncMock(side_effect=Exception("Context is closed"))

        # Set up new valid context
        new_context = AsyncMock()
        new_page = AsyncMock()
        new_page.is_closed = MagicMock(return_value=False)
        mock_browser.new_context.return_value = new_context
        new_context.new_page.return_value = new_page

        manager = BrowserManager()
        manager._browser = mock_browser
        manager._context = broken_context
        manager._page = None

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(lambda: manager.get_page(), timeout=5.0)

            # Should have created a new context after the old one failed
            mock_browser.new_context.assert_called_once()
            assert manager._context is new_context
            assert result is new_page
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestPageVsContextIsClosedMethod:
    """Tests specifically for Page.is_closed() vs BrowserContext behavior.

    This documents the exact bug that was fixed:
    - Page objects have is_closed() METHOD (must be called)
    - BrowserContext objects do NOT have is_closed at all
    """

    def test_page_has_is_closed_method_in_playwright(self):
        """Verify that Page objects in Playwright have is_closed method.

        This test uses the actual Playwright Page class to verify the API.
        """
        from playwright.async_api import Page

        # Page class should have is_closed method
        assert hasattr(Page, "is_closed"), "Page class should have is_closed method"
        # Verify it's callable (a method, not a property)

        assert callable(
            getattr(Page, "is_closed")
        ), "Page.is_closed should be a callable method"

    def test_browser_context_lacks_is_closed_in_playwright(self):
        """Verify that BrowserContext does NOT have is_closed method.

        This is the exact bug that was fixed - the code was calling
        context.is_closed() which doesn't exist on BrowserContext.
        """
        from playwright.async_api import BrowserContext

        # BrowserContext should NOT have is_closed
        # This was the bug: calling context.is_closed() would fail
        assert not hasattr(
            BrowserContext, "is_closed"
        ), "BrowserContext should NOT have is_closed - this was the bug!"


class TestBrowserManagerStatus:
    """Tests for status method."""

    def test_status_returns_url_and_title(self):
        """Test that status returns current URL and title."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Example Domain")
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(lambda: manager.status(), timeout=5.0)

            assert result == {"url": "https://example.com", "title": "Example Domain"}
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_status_returns_no_active_page_when_closed(self):
        """Test that status returns appropriate message when page is closed."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=True)
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(lambda: manager.status(), timeout=5.0)

            assert result == {"status": "no active page"}
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestBrowserManagerNavigate:
    """Tests for navigate method."""

    def test_navigate_to_url(self):
        """Test navigating to a URL."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Example")

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager.navigate("https://example.com"), timeout=5.0
            )

            mock_page.goto.assert_called_once_with(
                "https://example.com", wait_until="networkidle"
            )
            assert result["status"] == "success"
            assert result["url"] == "https://example.com"
            assert result["title"] == "Example"
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestBrowserManagerScreenshot:
    """Tests for screenshot method."""

    def test_screenshot_returns_bytes(self):
        """Test taking a screenshot."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.screenshot = AsyncMock(return_value=b"fake_png_data")

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager.screenshot(fullpage=False), timeout=5.0
            )

            mock_page.screenshot.assert_called_once_with(full_page=False)
            assert result == b"fake_png_data"
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestBrowserManagerClose:
    """Tests for close method."""

    def test_close_allows_reinitialization(self):
        """Test that after close(), the manager can be reinitialized."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        # Mock a healthy browser state
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            # Close should reset internal state
            manager.execute_serialized(lambda: manager.close(), timeout=5.0)

            # After close, manager should be in a state that allows reinitialization
            assert manager._page is None
            assert manager._context is None
            assert manager._browser is None
            assert manager._playwright is None
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestBrowserCrashRecovery:
    """Tests for browser crash recovery.

    These tests verify that the BrowserManager can detect and recover from
    browser process crashes (e.g., "pipe closed by peer" errors).
    """

    def test_is_browser_crash_error_detects_pipe_closed(self):
        """Test that pipe closed error is detected as a crash."""
        from wichy.helpers.browser import is_browser_crash_error

        error = Exception("pipe closed by peer")
        assert is_browser_crash_error(error) is True

    def test_is_browser_crash_error_detects_connection_closed(self):
        """Test that connection closed error is detected as a crash."""
        from wichy.helpers.browser import is_browser_crash_error

        error = Exception(
            "Browser.new_context: Connection closed while reading from the driver"
        )
        assert is_browser_crash_error(error) is True

    def test_is_browser_crash_error_detects_browser_closed(self):
        """Test that 'Browser has been closed' error is detected."""
        from wichy.helpers.browser import is_browser_crash_error

        error = Exception("Browser has been closed")
        assert is_browser_crash_error(error) is True

    def test_is_browser_crash_error_rejects_normal_error(self):
        """Test that normal errors are not detected as crashes."""
        from wichy.helpers.browser import is_browser_crash_error

        error = Exception("Timeout: 30000ms exceeded")
        assert is_browser_crash_error(error) is False

    def test_get_page_recover_after_close(self):
        """Test that get_page() can recover after browser is closed."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        # Mock browser in closed state
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=True)  # Page is closed
        manager._page = mock_page
        manager._browser = AsyncMock()  # Browser still alive

        # Mock context creation for recovery
        mock_context = AsyncMock()
        new_page = AsyncMock()
        new_page.is_closed = MagicMock(return_value=False)
        mock_context.new_page = AsyncMock(return_value=new_page)
        manager._browser.new_context = AsyncMock(return_value=mock_context)

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            # get_page should detect closed page and create a new one
            result = manager.execute_serialized(lambda: manager.get_page(), timeout=5.0)

            # Should have recovered with a new page
            assert result is new_page
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_navigate_retries_after_crash_recovery(self):
        """Test that navigate retries after recovering from a crash."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_playwright = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Example")

        # First goto fails with crash error
        goto_call_count = [0]

        async def goto_side_effect(url, **kwargs):
            goto_call_count[0] += 1
            if goto_call_count[0] == 1:
                raise Exception("pipe closed by peer")
            # Second call succeeds

        mock_page.goto = AsyncMock(side_effect=goto_side_effect)
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context
        mock_playwright.chromium.launch.return_value = mock_browser

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Mock async_playwright to return our mock for recovery
        with patch("wichy.helpers.browser.async_playwright") as mock_async_playwright:
            mock_async_playwright.return_value.__aenter__ = AsyncMock(
                return_value=mock_playwright
            )

            # Start a running event loop
            loop_fixture = EventLoopFixture()
            loop_fixture.start()

            try:
                manager._loop = loop_fixture.loop

                result = manager.execute_serialized(
                    lambda: manager.navigate("https://example.com"), timeout=5.0
                )

                # Should have recovered and succeeded
                assert result["status"] == "success"
                assert goto_call_count[0] == 2  # First failed, second succeeded
            finally:
                loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_is_browser_alive_detects_crash(self):
        """Test that _is_browser_alive detects crashed browser."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_browser = AsyncMock()
        # Accessing .contexts raises crash error
        mock_browser.contexts = None  # Will raise when accessed
        type(mock_browser).contexts = property(
            lambda self: (_ for _ in ()).throw(Exception("pipe closed by peer"))
        )

        manager._browser = mock_browser

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._is_browser_alive(), timeout=5.0
            )

            assert result is False
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_is_browser_alive_returns_true_for_healthy_browser(self):
        """Test that _is_browser_alive returns True for healthy browser."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_browser = AsyncMock()
        mock_browser.contexts = []  # Healthy browser has contexts list

        manager._browser = mock_browser

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._is_browser_alive(), timeout=5.0
            )

            assert result is True
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_is_browser_alive_returns_false_when_browser_is_none(self):
        """Test that _is_browser_alive returns False when browser is not initialized."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()
        manager._browser = None

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._is_browser_alive(), timeout=5.0
            )

            assert result is False
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None


class TestBrowserManagerIsInitialized:
    """Tests for is_initialized property."""

    def test_is_initialized_false_when_none(self):
        """Test is_initialized returns False when browser is None."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()
        manager._browser = None

        assert manager.is_initialized is False

        # Cleanup
        BrowserManager._instance = None

    def test_is_initialized_true_when_set(self):
        """Test is_initialized returns True when browser is set."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()
        manager._browser = AsyncMock()  # Not None

        assert manager.is_initialized is True

        # Cleanup
        BrowserManager._instance = None


class TestBrowserPageInfoAndAct:
    """Tests for _get_page_info and _act methods."""

    def test_get_page_info_quick_returns_url_and_title(self):
        """Test that detail='quick' returns just url and title."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Example Domain")

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._get_page_info(detail="quick"), timeout=5.0
            )

            assert result == {"url": "https://example.com", "title": "Example Domain"}
            # Should NOT call query_selector_all for quick mode
            mock_page.query_selector_all.assert_not_called()
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_get_page_info_full_returns_structured_info(self):
        """Test that detail='full' returns headings, links, buttons, inputs, tables."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Example Domain")

        # Mock heading elements
        mock_heading = AsyncMock()
        mock_heading.is_visible = AsyncMock(return_value=True)
        mock_heading.text_content = AsyncMock(return_value="Welcome")
        mock_heading.evaluate = AsyncMock(return_value="h1")

        # Mock link elements
        mock_link = AsyncMock()
        mock_link.is_visible = AsyncMock(return_value=True)
        mock_link.text_content = AsyncMock(return_value="Click Here")
        mock_link.get_attribute = AsyncMock(return_value="https://example.com/link")

        # Mock button elements
        mock_button = AsyncMock()
        mock_button.is_visible = AsyncMock(return_value=True)
        mock_button.text_content = AsyncMock(return_value="Submit")
        mock_button.evaluate = AsyncMock(return_value="button")

        # Mock input elements
        mock_input = AsyncMock()
        mock_input.is_visible = AsyncMock(return_value=True)
        mock_input.get_attribute = AsyncMock(
            side_effect=lambda attr: {
                "type": "text",
                "name": "username",
                "placeholder": "Enter username",
            }.get(attr)
        )
        mock_input.evaluate = AsyncMock(return_value="input")

        # Mock table elements
        mock_table = AsyncMock()
        mock_table.is_visible = AsyncMock(return_value=True)
        mock_th = AsyncMock()
        mock_th.text_content = AsyncMock(return_value="Header")
        mock_tr = AsyncMock()

        # Set up query_selector_all on the table mock
        async def mock_table_query_selector_all(selector):
            if selector == "th":
                return [mock_th]
            elif selector == "tr":
                return [mock_tr, mock_tr]
            return []

        mock_table.query_selector_all = AsyncMock(
            side_effect=mock_table_query_selector_all
        )

        # Set up query_selector_all on the page mock
        async def mock_page_query_selector_all(selector):
            if "h1" in selector:
                return [mock_heading]
            elif selector == "a[href]":
                return [mock_link]
            elif "button" in selector:
                return [mock_button]
            elif selector == "input, textarea, select":
                return [mock_input]
            elif selector == "table":
                return [mock_table]
            return []

        mock_page.query_selector_all = AsyncMock(
            side_effect=mock_page_query_selector_all
        )

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._get_page_info(detail="full"), timeout=5.0
            )

            assert result["url"] == "https://example.com"
            assert result["title"] == "Example Domain"
            assert "headings" in result
            assert "links" in result
            assert "buttons" in result
            assert "inputs" in result
            assert "tables" in result
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_get_page_info_full_handles_empty_page(self):
        """Test graceful handling when page has no structured elements."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.url = "https://example.com"
        mock_page.title = AsyncMock(return_value="Empty Page")

        # Return empty lists for all queries
        mock_page.query_selector_all = AsyncMock(return_value=[])

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._get_page_info(detail="full"), timeout=5.0
            )

            assert result["url"] == "https://example.com"
            assert result["title"] == "Empty Page"
            assert result["headings"] == []
            assert result["links"] == []
            assert result["buttons"] == []
            assert result["inputs"] == []
            assert result["tables"] == []
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_wait_waits_for_selector(self):
        """Test that wait action returns success when element is found."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.wait_for_selector = AsyncMock(return_value=None)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(
                    action="wait",
                    target=".loading-done",
                    value=None,
                    wait_until="none",
                    timeout=5000,
                ),
                timeout=5.0,
            )

            # Verify actual return value, not mock calls
            assert result["status"] == "success"
            assert result["action"] == "wait"
            assert result["target"] == ".loading-done"
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_wait_timeout_returns_error(self):
        """Test that wait returns error on timeout."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        mock_page.wait_for_selector = AsyncMock(
            side_effect=Exception("Timeout: 5000ms exceeded")
        )

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(action="wait", target=".missing-element", timeout=5000),
                timeout=5.0,
            )

            assert result["status"] == "error"
            assert "not found" in result["error"].lower()
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_click_returns_success_when_element_found(self):
        """Test that click action returns success when element is found."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        # Mock locator for get_by_text
        mock_locator = AsyncMock()
        mock_locator.wait_for = AsyncMock(return_value=None)
        mock_locator.click = AsyncMock(return_value=None)
        mock_page.get_by_text = MagicMock(return_value=mock_locator)
        mock_page.wait_for_load_state = AsyncMock(return_value=None)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(
                    action="click",
                    target="Submit",
                    value=None,
                    wait_until="none",
                    timeout=5000,
                ),
                timeout=5.0,
            )

            # Verify actual return value
            assert result["status"] == "success"
            assert result["action"] == "click"
            assert result["target"] == "Submit"
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_click_returns_error_when_element_not_found(self):
        """Test that click returns error when element is not found with any selector."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        # First selector (get_by_text) fails
        mock_locator1 = AsyncMock()
        mock_locator1.wait_for = AsyncMock(side_effect=Exception("not found"))
        mock_page.get_by_text = MagicMock(return_value=mock_locator1)

        # Fallback selector (locator with text=) also fails
        mock_locator2 = AsyncMock()
        mock_locator2.wait_for = AsyncMock(side_effect=Exception("timeout"))
        mock_page.locator = MagicMock(return_value=mock_locator2)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(
                    action="click",
                    target="Missing",
                    value=None,
                    wait_until="none",
                    timeout=5000,
                ),
                timeout=5.0,
            )

            assert result["status"] == "error"
            assert (
                "not found" in result["error"].lower()
                or "missing" in result["error"].lower()
            )
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_fill_succeeds_with_valid_input(self):
        """Test that fill action returns success when input is found."""
        from unittest.mock import AsyncMock, MagicMock
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)
        # First selector finds the input
        mock_input = AsyncMock()
        mock_input.wait_for = AsyncMock(return_value=None)
        mock_input.fill = AsyncMock(return_value=None)
        mock_page.query_selector = AsyncMock(return_value=mock_input)
        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(
                    action="fill",
                    target="email",
                    value="test@example.com",
                    wait_until="none",
                    timeout=5000,
                ),
                timeout=5.0,
            )

            assert result["status"] == "success"
            assert result["action"] == "fill"
            assert result["target"] == "email"
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_fill_returns_error_when_value_not_provided(self):
        """Test that fill returns error when value is not provided."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(action="fill", target="username", value=None, timeout=5000),
                timeout=5.0,
            )

            assert result["status"] == "error"
            assert "value is required" in result["error"].lower()
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_fill_tries_multiple_selectors(self):
        """Test that fill tries multiple selector strategies when first fails."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        # First selector (by name) returns None (not found)
        mock_page.query_selector = AsyncMock(return_value=None)

        # But get_by_label finds the input
        mock_input = AsyncMock()
        mock_input.wait_for = AsyncMock(return_value=None)
        mock_input.fill = AsyncMock(return_value=None)
        mock_page.get_by_label = MagicMock(return_value=mock_input)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(
                    action="fill", target="Email", value="test@example.com", timeout=5000
                ),
                timeout=5.0,
            )

            assert result["status"] == "success"
            assert result["action"] == "fill"
            assert result["target"] == "Email"
            # fill("") is called first to clear, then fill with value
            assert mock_input.fill.call_count == 2
            # Verify query_selector was tried first
            mock_page.query_selector.assert_called()
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_fill_returns_error_when_element_not_found(self):
        """Test that fill returns error when element is not found with any selector."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        # All query_selector attempts return None
        mock_page.query_selector = AsyncMock(return_value=None)

        # get_by_label also fails
        mock_locator = AsyncMock()
        mock_locator.wait_for = AsyncMock(side_effect=Exception("not found"))
        mock_page.get_by_label = MagicMock(return_value=mock_locator)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(
                    action="fill", target="Nonexistent", value="test", timeout=5000
                ),
                timeout=5.0,
            )

            assert result["status"] == "error"
            assert (
                "not found" in result["error"].lower()
                or "nonexistent" in result["error"].lower()
            )
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None

    def test_act_returns_error_for_unknown_action(self):
        """Test that _act returns error for unknown action types."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = AsyncMock()
        manager._context = AsyncMock()
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            manager._loop = loop_fixture.loop

            result = manager.execute_serialized(
                lambda: manager._act(action="unknown", target="something", timeout=5000),
                timeout=5.0,
            )

            assert result["status"] == "error"
            assert "Unknown action" in result["error"]
        finally:
            loop_fixture.stop()

        # Cleanup
        BrowserManager._instance = None