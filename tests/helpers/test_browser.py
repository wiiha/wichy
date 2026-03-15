"""
Test cases for the BrowserManager helper.

Tests cover:
- Browser initialization
- Page creation and recreation when closed
- Context handling (BrowserContext doesn't have is_closed() method)
- Navigation, status, and screenshot functionality

Key bug that was fixed: BrowserContext does NOT have is_closed() method,
only Page does. The code was incorrectly calling context.is_closed().
"""

from unittest.mock import AsyncMock, patch


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
        # is_closed is a sync property in Playwright, not async
        mock_page.is_closed = False

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
        # is_closed is a sync property in Playwright, not async
        mock_page.is_closed = False

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
        # is_closed is a sync property in Playwright, not async
        mock_page.is_closed = True

        new_page = AsyncMock()
        new_page.is_closed = False
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
        mock_page.is_closed = False
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
        new_page.is_closed = False
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
    - Page objects have is_closed property
    - BrowserContext objects do NOT have is_closed property/method
    """

    def test_page_has_is_closed_property_in_playwright(self):
        """Verify that Page objects in Playwright have is_closed property.

        This test uses the actual Playwright Page class to verify the API.
        """
        from playwright.async_api import Page

        # Page class should have is_closed property
        assert hasattr(Page, "is_closed"), "Page class should have is_closed property"

    def test_browser_context_lacks_is_closed_in_playwright(self):
        """Verify that BrowserContext does NOT have is_closed property.

        This is the exact bug that was fixed - the code was calling
        context.is_closed() which doesn't exist on BrowserContext.
        """
        from playwright.async_api import BrowserContext

        # BrowserContext should NOT have is_closed property
        # This was the bug: calling context.is_closed() would fail
        assert not hasattr(
            BrowserContext, "is_closed"
        ), "BrowserContext should NOT have is_closed property - this was the bug!"


class TestBrowserManagerStatus:
    """Tests for status method."""

    def test_status_returns_url_and_title(self):
        """Test that status returns current URL and title."""
        from wichy.helpers.browser import BrowserManager

        BrowserManager._instance = None
        manager = BrowserManager()

        mock_page = AsyncMock()
        mock_page.is_closed = False
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
        mock_page.is_closed = True
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
        mock_page.is_closed = False
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
        mock_page.is_closed = False
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
