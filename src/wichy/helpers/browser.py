"""
Playwright browser singleton manager for wichy tools.

Provides a single persistent browser instance for the entire session.
This avoids repeated browser launches and preserves session state (cookies, logins).
"""

import ast
import inspect
import random
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from wichy.config import settings


class BrowserManager:
    """
    Singleton manager for Playwright browser instance.

    Manages a single browser instance with a persistent page to improve
    performance and preserve session state across tool calls.
    """

    _instance: Optional["BrowserManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._playwright = None
            self._browser: Optional[Browser] = None
            self._context: Optional[BrowserContext] = None
            self._page: Optional[Page] = None
            self._initialized = True

    async def initialize(self):
        """Initialize the browser if not already initialized."""
        if self._browser is None:
            self._playwright = await async_playwright().__aenter__()
            self._browser = await self._playwright.chromium.launch(
                headless=settings.browser_headless
            )

            # Create a dedicated context for the persistent page
            self._context = await self._browser.new_context(
                user_agent=random.choice(settings.browser_user_agents),
                viewport=settings.browser_viewport,
                locale=settings.browser_locale,
            )
            self._page = await self._context.new_page()

    async def get_page(self) -> Page:
        """
        Get the persistent page instance.

        Returns:
            The persistent Page instance.
        """
        if self._browser is None:
            await self.initialize()

        # If page is closed, recreate it
        if self._page is None or self._page.is_closed():
            try:
                if self._context is None:
                    raise Exception("Context not initialized")
                self._page = await self._context.new_page()
            except Exception:
                # Context may be closed or invalid, recreate it
                self._context = await self._browser.new_context(
                    user_agent=random.choice(settings.browser_user_agents),
                    viewport=settings.browser_viewport,
                    locale=settings.browser_locale,
                )
                self._page = await self._context.new_page()

        return self._page

    async def status(self) -> dict:
        """
        Get the current status of the persistent page.

        Returns:
            A dictionary with 'url' and 'title' keys, or 'error' if unavailable.
        """
        if self._page is None or self._page.is_closed():
            return {"status": "no active page"}

        try:
            url = self._page.url
            title = await self._page.title()
            return {"url": url, "title": title}
        except Exception as e:
            return {"status": f"error: {str(e)}"}

    async def navigate(self, url: str, wait_until: str = "networkidle") -> dict:
        """
        Navigate the persistent page to a URL.

        Args:
            url: The URL to navigate to
            wait_until: When to consider navigation complete. Options: "load", "domcontentloaded", "networkidle"

        Returns:
            A dictionary with navigation result including url and title.
        """
        page = await self.get_page()

        try:
            await page.goto(url, wait_until=wait_until)

            # Wait a bit for any additional dynamic content
            await page.wait_for_timeout(1000)

            title = await page.title()
            current_url = page.url

            return {"url": current_url, "title": title, "status": "success"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def screenshot(self, fullpage: bool = False) -> bytes:
        """
        Take a screenshot of the current page.

        Args:
            fullpage: If True, capture the full scrollable page. If False, capture only the viewport.

        Returns:
            Bytes containing the screenshot in PNG format.

        Raises:
            RuntimeError: If page is not available.
        """
        page = await self.get_page()

        try:
            return await page.screenshot(full_page=fullpage)
        except Exception as e:
            raise RuntimeError(f"Failed to take screenshot: {str(e)}")

    async def raw(self, code: str) -> Any:
        """
        Execute raw code on the persistent page object with full chaining support.

        Allows direct access to the Playwright Page API for fine-grained control.
        Supports method chaining with automatic awaiting of coroutines.

        Args:
            code: A string representing an expression using the page object.
              Examples: "title()", ".url", "content()", "screenshot(fullpage=True)",
                        "wait_for_timeout(5000)", "query_selector('h1').text_content()",
                        "query_selector('form').fill('input', 'value')"

        Returns:
            The result of the evaluated expression.

        Raises:
            ValueError: If the code is invalid or attempts to access disallowed attributes/methods.
        """
        page = await self.get_page()

        # Normalize: allow optional leading dot
        normalized = code
        if code.startswith("."):
            normalized = code[1:]

        full_expr = f"page.{normalized}"

        # Parse the expression
        try:
            tree = ast.parse(full_expr, mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Invalid syntax: {e}")

        # Evaluate AST with automatic awaiting
        try:
            result = await self._eval_ast(tree.body, page)
            return result
        except Exception as e:
            raise ValueError(f"Error executing code: {str(e)}")

    async def _eval_ast(self, node: ast.AST, page: Page) -> Any:
        """
        Recursively evaluate an AST node, automatically awaiting coroutines.

        Args:
            node: AST node to evaluate
            page: The Playwright Page object

        Returns:
            The evaluated result, with all coroutines awaited.
        """
        if isinstance(node, ast.Name):
            if node.id == "page":
                return page
            else:
                raise ValueError(f"Only 'page' variable is allowed, got '{node.id}'")

        elif isinstance(node, ast.Attribute):
            # Evaluate the base object
            base = await self._eval_ast(node.value, page)
            # If it's a coroutine, await it
            if inspect.iscoroutine(base):
                base = await base
            # Disallow private attributes
            if node.attr.startswith("_"):
                raise ValueError(
                    f"Access to private attribute '{node.attr}' is not allowed"
                )
            # Get the attribute
            try:
                return getattr(base, node.attr)
            except AttributeError:
                raise ValueError(f"Object has no attribute '{node.attr}'")

        elif isinstance(node, ast.Call):
            # Evaluate the function (could be page.method or result.attr.method)
            func = await self._eval_ast(node.func, page)
            if inspect.iscoroutine(func):
                func = await func
            if not callable(func):
                raise ValueError("Object is not callable")

            # Evaluate arguments
            args = []
            for arg in node.args:
                val = await self._eval_ast(arg, page)
                if inspect.iscoroutine(val):
                    val = await val
                args.append(val)

            kwargs = {}
            for kw in node.keywords:
                val = await self._eval_ast(kw.value, page)
                if inspect.iscoroutine(val):
                    val = await val
                kwargs[kw.arg] = val

            # Call the function
            result = func(*args, **kwargs)
            if inspect.iscoroutine(result):
                result = await result
            return result

        elif isinstance(node, ast.Constant):
            return node.value

        # For Python 3.7 compatibility (if needed)
        elif hasattr(ast, "Str") and isinstance(node, ast.Str):
            return node.s
        elif hasattr(ast, "Num") and isinstance(node, ast.Num):
            return node.n

        elif isinstance(node, ast.List):
            elts = []
            for elt in node.elts:
                val = await self._eval_ast(elt, page)
                if inspect.iscoroutine(val):
                    val = await val
                elts.append(val)
            return elts

        elif isinstance(node, ast.Tuple):
            elts = []
            for elt in node.elts:
                val = await self._eval_ast(elt, page)
                if inspect.iscoroutine(val):
                    val = await val
                elts.append(val)
            return tuple(elts)

        elif isinstance(node, ast.Dict):
            keys = []
            values = []
            for key, value in zip(node.keys, node.values):
                k = await self._eval_ast(key, page) if key else None
                if inspect.iscoroutine(k):
                    k = await k
                v = await self._eval_ast(value, page)
                if inspect.iscoroutine(v):
                    v = await v
                keys.append(k)
                values.append(v)
            return dict(zip(keys, values))

        else:
            raise ValueError(f"Unsupported expression type: {type(node).__name__}")

    async def close(self):
        """Close the browser and cleanup resources."""
        if self._page:
            await self._page.close()
            self._page = None
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    @property
    def is_initialized(self) -> bool:
        """Check if the browser is initialized."""
        return self._browser is not None


# Global singleton instance
browser_manager = BrowserManager()
