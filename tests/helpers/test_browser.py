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
"""

from unittest.mock import AsyncMock, MagicMock, patch


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

        with patch("wichy.helpers.browser.async_playwright") as mock_async_playwright:
            mock_async_playwright.return_value.__aenter__ = AsyncMock(
                return_value=mock_playwright
            )
            mock_playwright.chromium.launch.return_value = mock_browser
            mock_browser.new_context.return_value = mock_context
            mock_context.new_page.return_value = mock_page

            # Run async initialize
            import asyncio

            asyncio.get_event_loop().run_until_complete(manager.initialize())

            assert manager._browser is mock_browser
            assert manager._context is mock_context
            assert manager._page is mock_page

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

        # Run async get_page
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(manager.get_page())

        assert result is mock_page
        # Should NOT create new page since existing one is valid
        mock_context.new_page.assert_not_called()

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

        # Run async get_page
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(manager.get_page())

        # Should create new page on existing context
        mock_context.new_page.assert_called_once()
        assert result is new_page

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

        # Run async get_page
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(manager.get_page())

        # Should create new context
        mock_browser.new_context.assert_called_once()
        assert manager._context is mock_context
        assert result is mock_page

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

        # Run async get_page
        import asyncio

        result = asyncio.get_event_loop().run_until_complete(manager.get_page())

        # Should have created a new context after the old one failed
        mock_browser.new_context.assert_called_once()
        assert manager._context is new_context
        assert result is new_page

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

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(manager.status())

        assert result == {"url": "https://example.com", "title": "Example Domain"}

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

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(manager.status())

        assert result == {"status": "no active page"}

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

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            manager.navigate("https://example.com")
        )

        mock_page.goto.assert_called_once_with(
            "https://example.com", wait_until="networkidle"
        )
        assert result["status"] == "success"
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"

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

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            manager.screenshot(fullpage=False)
        )

        mock_page.screenshot.assert_called_once_with(full_page=False)
        assert result == b"fake_png_data"

        # Cleanup
        BrowserManager._instance = None


class TestBrowserManagerClose:
    """Tests for close method."""

    def test_close_cleans_up_resources(self):
        """Test that close cleans up all resources."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_playwright = AsyncMock()

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page
        manager._playwright = mock_playwright

        import asyncio

        asyncio.get_event_loop().run_until_complete(manager.close())

        mock_page.close.assert_called_once()
        mock_context.close.assert_called_once()
        mock_browser.close.assert_called_once()
        mock_playwright.stop.assert_called_once()

        assert manager._page is None
        assert manager._context is None
        assert manager._browser is None
        assert manager._playwright is None


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

    def test_get_page_recovers_from_browser_crash(self):
        """Test that get_page detects crash and triggers recovery."""
        from unittest.mock import patch
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        # Create mocks for the INITIAL state (broken)
        mock_playwright_initial = AsyncMock()
        mock_browser_initial = AsyncMock()
        mock_context_initial = AsyncMock()

        # Simulate: browser appears alive, but creating page fails with crash error
        mock_browser_initial.contexts = []  # Healthy browser
        mock_context_initial.new_page = AsyncMock(
            side_effect=Exception("pipe closed by peer")
        )

        mock_browser_initial.new_context.return_value = mock_context_initial
        mock_playwright_initial.chromium.launch.return_value = mock_browser_initial

        # Set up manager with initial broken state
        manager._playwright = mock_playwright_initial
        manager._browser = mock_browser_initial
        manager._context = mock_context_initial
        manager._page = None  # No page - will trigger page creation

        # Create mocks for the RECOVERED state (working)
        mock_playwright_recovered = AsyncMock()
        mock_browser_recovered = AsyncMock()
        mock_context_recovered = AsyncMock()
        mock_page_recovered = AsyncMock()
        mock_page_recovered.is_closed = MagicMock(return_value=False)

        mock_browser_recovered.contexts = []
        mock_context_recovered.new_page.return_value = mock_page_recovered
        mock_browser_recovered.new_context.return_value = mock_context_recovered
        mock_playwright_recovered.chromium.launch.return_value = mock_browser_recovered

        # Mock async_playwright to return RECOVERED mocks for recovery
        with patch("wichy.helpers.browser.async_playwright") as mock_async_playwright:
            # Return recovered playwright on second call (during recovery)
            mock_async_playwright.return_value.__aenter__ = AsyncMock(
                return_value=mock_playwright_recovered
            )

            import asyncio

            # This should:
            # 1. Check browser is alive (True - contexts list is empty)
            # 2. Try to create page from broken context -> crash error
            # 3. Call _recover_from_crash() -> clears state, calls initialize()
            # 4. initialize() gets fresh mocks from async_playwright
            # 5. Returns working page
            result = asyncio.get_event_loop().run_until_complete(manager.get_page())

            # Should have recovered with fresh mocks
            assert manager._playwright is mock_playwright_recovered
            assert manager._browser is mock_browser_recovered
            assert manager._context is mock_context_recovered
            assert result is mock_page_recovered

        # Cleanup
        BrowserManager._instance = None

    def test_navigate_retries_after_crash_recovery(self):
        """Test that navigate retries after recovering from a crash."""
        from unittest.mock import patch
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

            import asyncio

            result = asyncio.get_event_loop().run_until_complete(
                manager.navigate("https://example.com")
            )

            # Should have recovered and succeeded
            assert result["status"] == "success"
            assert goto_call_count[0] == 2  # First failed, second succeeded

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

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            manager._is_browser_alive()
        )

        assert result is False

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

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            manager._is_browser_alive()
        )

        assert result is True

        # Cleanup
        BrowserManager._instance = None

    def test_is_browser_alive_returns_false_when_browser_is_none(self):
        """Test that _is_browser_alive returns False when browser is not initialized."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()
        manager._browser = None

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(
            manager._is_browser_alive()
        )

        assert result is False

        # Cleanup
        BrowserManager._instance = None
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
