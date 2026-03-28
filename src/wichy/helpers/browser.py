"""
Playwright browser singleton manager for wichy tools.

Provides a single persistent browser instance for the entire session.
This avoids repeated browser launches and preserves session state (cookies, logins).
"""

import ast
import asyncio
import inspect
import random
import threading
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from wichy.config import settings
from wichy.helpers.console import console
from wichy.tools.errors import format_error

# Error patterns that indicate the browser process has crashed/disconnected
BROWSER_CRASH_ERRORS = [
    "pipe closed by peer",
    "Connection closed while reading from the driver",
    "Browser has been closed",
    "Target closed",
    "Execution context was destroyed",
    "Cannot find execution context",
    "browser disconnected",
]


def is_browser_crash_error(error: Exception) -> bool:
    """Check if an error indicates the browser process crashed/disconnected."""
    error_str = str(error).lower()
    return any(pattern.lower() in error_str for pattern in BROWSER_CRASH_ERRORS)


class BrowserManager:
    """
    Singleton manager for Playwright browser instance.

    Manages a single browser instance with a persistent page to improve
    performance and preserve session state across tool calls.
    """

    _instance: Optional["BrowserManager"] = None
    _singleton_lock = threading.Lock()

    def __new__(cls):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._playwright = None
            self._browser: Optional[Browser] = None
            self._context: Optional[BrowserContext] = None
            self._page: Optional[Page] = None
            self._async_lock: Optional[asyncio.Lock] = None
            self._loop: Optional[asyncio.AbstractEventLoop] = None
            self._initialized = True

    def get_event_loop(self) -> asyncio.AbstractEventLoop:
        """
        Get or create the global asyncio event loop.

        Creates a single persistent loop for the entire session to avoid
        invalidating browser objects tied to old loops.
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def _get_async_lock(self) -> asyncio.Lock:
        """Lazily create an asyncio.Lock (requires an event loop)."""
        if self._async_lock is None:
            with self._singleton_lock:
                if self._async_lock is None:
                    self._async_lock = asyncio.Lock()
        return self._async_lock

    async def _initialize_browser(self):
        """Internal method to actually initialize the browser. Caller must hold the lock."""
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

    async def initialize(self):
        """Initialize the browser if not already initialized."""
        if self._browser is None:
            async with self._get_async_lock():
                if self._browser is None:
                    await self._initialize_browser()

    async def _is_browser_alive(self) -> bool:
        """
        Check if the browser process is still alive and responsive.

        Returns:
            True if browser is alive and responsive, False otherwise.
        """
        if self._browser is None:
            return False

        try:
            # Try a simple operation to verify browser is responsive
            # If browser crashed, this will raise an error
            _ = self._browser.contexts
            return True
        except Exception as e:
            if is_browser_crash_error(e):
                console.log(
                    f"[yellow]Browser process appears to have crashed: {e}[/yellow]"
                )
                return False
            # Unexpected error, log and treat as alive (let it fail naturally)
            return True

    async def _recover_from_crash(self):
        """
        Recover from a browser crash by fully reinitializing.

        This resets all state and creates a fresh browser instance.
        The lock prevents concurrent recovery attempts, so no double-check is needed.
        """
        console.log("[yellow]Recovering from browser crash...[/yellow]")

        async with self._get_async_lock():
            # Clean up old instances before clearing references
            if self._page:
                try:
                    await self._page.close()
                except Exception:
                    pass  # Ignore errors during cleanup
            if self._context:
                try:
                    await self._context.close()
                except Exception:
                    pass  # Ignore errors during cleanup
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass  # Ignore errors during cleanup
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass  # Ignore errors during cleanup

            # Clear stale references
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            # Note: _async_lock is NOT reset - the same lock persists

            # Reinitialize from scratch
            await self._initialize_browser()
        console.log("[green]Browser crash recovery complete[/green]")

    async def get_page(self) -> Page:
        """
        Get the persistent page instance.

        Returns:
            The persistent Page instance.
        """
        # If browser not initialized, initialize it (with lock to prevent races)
        if self._browser is None:
            async with self._get_async_lock():
                if self._browser is None:
                    await self._initialize_browser()

        # Check if browser process is still alive (handles crash recovery)
        if not await self._is_browser_alive():
            await self._recover_from_crash()

        # If page is closed, recreate it
        if self._page is None or self._page.is_closed():
            try:
                if self._context is None:
                    raise RuntimeError("Context not initialized")
                self._page = await self._context.new_page()
            except Exception as e:
                if is_browser_crash_error(e):
                    # Browser crashed during page creation, recover and retry
                    await self._recover_from_crash()
                    # After recovery, _page should be ready
                else:
                    # Context may be closed or invalid, recreate it
                    if self._browser is None:
                        # Browser was lost, reinitialize everything
                        await self.initialize()
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
            if is_browser_crash_error(e):
                return {
                    "status": "browser disconnected - will recover on next operation"
                }
            return {"status": format_error(str(e))}

    async def _get_page_info(self, detail: str) -> dict:
        """
        Get structured information about the current page.

        Args:
            detail: 'quick' for URL and title only, 'full' for detailed page structure.

        Returns:
            A dictionary with page information.
        """
        page = await self.get_page()

        try:
            # Get basic info
            url = page.url
            title = await page.title()

            if detail == "quick":
                return {"url": url, "title": title}

            # Full detail - extract structured page info
            result = {"url": url, "title": title}

            # Extract headings
            headings = []
            heading_elements = await page.query_selector_all("h1, h2, h3, h4, h5, h6")
            for el in heading_elements[:20]:  # Limit to 20
                if await el.is_visible():
                    text = await el.text_content()
                    if text:
                        # Get heading level from tag name
                        tag = await el.evaluate("e => e.tagName.toLowerCase()")
                        level = int(tag[1]) if tag and tag[0] == "h" else 0
                        headings.append({"level": level, "text": text.strip()})
            result["headings"] = headings

            # Extract links
            links = []
            link_elements = await page.query_selector_all("a[href]")
            for el in link_elements[:20]:  # Limit to 20
                if await el.is_visible():
                    text = await el.text_content()
                    href = await el.get_attribute("href")
                    if href:
                        links.append(
                            {"text": text.strip() if text else "", "href": href}
                        )
            result["links"] = links

            # Extract buttons
            buttons = []
            button_elements = await page.query_selector_all(
                "button, input[type='submit'], input[type='button']"
            )
            for el in button_elements[:20]:  # Limit to 20
                if await el.is_visible():
                    text = await el.text_content()
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    input_type = None
                    if tag == "input":
                        input_type = await el.get_attribute("type")
                        input_type = input_type or "button"
                    else:
                        input_type = "button"
                    buttons.append(
                        {"text": text.strip() if text else "", "type": input_type}
                    )
            result["buttons"] = buttons

            # Extract inputs
            inputs = []
            input_elements = await page.query_selector_all("input, textarea, select")
            for el in input_elements[:20]:  # Limit to 20
                if await el.is_visible():
                    input_type = await el.get_attribute("type")
                    # Exclude hidden and submit/button inputs
                    if input_type in ("hidden", "submit", "button"):
                        continue
                    name = await el.get_attribute("name")
                    placeholder = await el.get_attribute("placeholder")
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    inputs.append(
                        {
                            "name": name or "",
                            "type": (
                                tag
                                if tag == "textarea" or tag == "select"
                                else (input_type or "text")
                            ),
                            "placeholder": placeholder or "",
                        }
                    )
            result["inputs"] = inputs

            # Extract tables
            tables = []
            table_elements = await page.query_selector_all("table")
            for el in table_elements[:20]:  # Limit to 20
                if await el.is_visible():
                    # Get headers from th elements
                    th_elements = await el.query_selector_all("th")
                    headers = []
                    for th in th_elements:
                        th_text = await th.text_content()
                        headers.append(th_text.strip() if th_text else "")
                    # Count rows (tr elements)
                    tr_elements = await el.query_selector_all("tr")
                    tables.append({"headers": headers, "row_count": len(tr_elements)})
            result["tables"] = tables

            return result

        except Exception as e:
            if is_browser_crash_error(e):
                await self._recover_from_crash()
                # Retry once after recovery
                page = await self.get_page()
                try:
                    return await self._get_page_info(detail)
                except Exception as retry_e:
                    return {"error": f"Failed after recovery: {str(retry_e)}"}
            return {"error": str(e)}

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
            if is_browser_crash_error(e):
                console.log(
                    f"[yellow]Browser crash during navigate, recovering: {e}[/yellow]"
                )
                await self._recover_from_crash()
                # Retry once after recovery
                page = await self.get_page()
                try:
                    await page.goto(url, wait_until=wait_until)
                    await page.wait_for_timeout(1000)
                    title = await page.title()
                    return {"url": page.url, "title": title, "status": "success"}
                except Exception as retry_e:
                    return {
                        "status": "error",
                        "error": f"Failed after recovery: {str(retry_e)}",
                    }
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
            if is_browser_crash_error(e):
                console.log(f"Browser crash during screenshot, recovering: {e}")
                await self._recover_from_crash()
                page = await self.get_page()
                try:
                    return await page.screenshot(full_page=fullpage)
                except Exception as retry_e:
                    raise RuntimeError(f"Failed after recovery: {str(retry_e)}")
            raise RuntimeError(f"Failed to take screenshot: {str(e)}")

    async def _act(
        self,
        action: str,
        target: str,
        value: Optional[str] = None,
        wait_until: str = "navigation",
        timeout: int = 5000,
    ) -> dict:
        """
        Perform a declarative browser action (click, fill, or wait).

        Args:
            action: Action to perform: 'click', 'fill', or 'wait'
            target: Target element identifier (text for click, name/placeholder/id for fill, CSS selector for wait)
            value: Value to type (required for 'fill' action)
            wait_until: For click: what to wait for after clicking. Options: 'navigation', 'none'
            timeout: Timeout in milliseconds for wait operations

        Returns:
            A dictionary with action result or error.
        """
        page = await self.get_page()

        try:
            if action == "click":
                return await self._act_click(page, target, wait_until, timeout)
            elif action == "fill":
                if value is None:
                    return {
                        "status": "error",
                        "error": "Value is required for 'fill' action",
                    }
                return await self._act_fill(page, target, value, timeout)
            elif action == "wait":
                return await self._act_wait(page, target, timeout)
            else:
                return {"status": "error", "error": f"Unknown action: {action}"}

        except Exception as e:
            if is_browser_crash_error(e):
                console.log(
                    f"[yellow]Browser crash during {action}, recovering: {e}[/yellow]"
                )
                await self._recover_from_crash()
                page = await self.get_page()
                try:
                    if action == "click":
                        return await self._act_click(page, target, wait_until, timeout)
                    elif action == "fill":
                        return await self._act_fill(page, target, value, timeout)
                    elif action == "wait":
                        return await self._act_wait(page, target, timeout)
                except Exception as retry_e:
                    return {
                        "status": "error",
                        "error": f"Failed after recovery: {str(retry_e)}",
                    }
            return {"status": "error", "error": str(e)}

    async def _act_click(
        self, page: Page, target: str, wait_until: str, timeout: int
    ) -> dict:
        """
        Click an element by text.

        Args:
            page: The Playwright Page object
            target: Text to click
            wait_until: 'navigation' to wait for page load, 'none' for no wait
            timeout: Timeout in milliseconds

        Returns:
            Result dictionary with status
        """
        # Try get_by_text first, then fallback to locator
        locator = page.get_by_text(target, exact=False)
        try:
            await locator.wait_for(state="visible", timeout=timeout)
        except Exception:
            # Fallback to text selector
            locator = page.locator(f"text={target}")
            try:
                await locator.wait_for(state="visible", timeout=timeout)
            except Exception:
                return {"status": "error", "error": f"Element not found: {target}"}

        await locator.click()

        if wait_until == "navigation":
            await page.wait_for_load_state("networkidle")

        return {"status": "success", "action": "click", "target": target}

    async def _act_fill(
        self, page: Page, target: str, value: str, timeout: int
    ) -> dict:
        """
        Fill a form input by name, placeholder, id, or label.

        Args:
            page: The Playwright Page object
            target: Input identifier (name, placeholder, id, or label)
            value: Value to type
            timeout: Timeout in milliseconds

        Returns:
            Result dictionary with status
        """
        input_element = None

        # Try finding input by various selectors in order
        selectors = [
            f'input[name="{target}"]',
            f'input[placeholder*="{target}"]',
            f"#{target}",
            f'textarea[name="{target}"]',
        ]

        for selector in selectors:
            try:
                input_element = await page.query_selector(selector)
                if input_element:
                    break
            except Exception:
                continue

        # Try get_by_label as last resort
        if not input_element:
            try:
                locator = page.get_by_label(target)
                await locator.wait_for(state="visible", timeout=timeout)
                input_element = locator
            except Exception:
                pass

        if not input_element:
            return {"status": "error", "error": f"Element not found: {target}"}

        # Wait for element to be visible
        try:
            await input_element.wait_for(state="visible", timeout=timeout)
        except Exception:
            return {"status": "error", "error": f"Element not found: {target}"}

        # Clear and fill
        await input_element.fill("")
        await input_element.fill(value)

        return {"status": "success", "action": "fill", "target": target}

    async def _act_wait(self, page: Page, target: str, timeout: int) -> dict:
        """
        Wait for an element to appear on the page.

        Args:
            page: The Playwright Page object
            target: CSS selector for the element
            timeout: Timeout in milliseconds

        Returns:
            Result dictionary with status
        """
        try:
            await page.wait_for_selector(target, state="visible", timeout=timeout)
            return {"status": "success", "action": "wait", "target": target}
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower():
                return {"status": "error", "error": f"Element not found: {target}"}
            return {"status": "error", "error": error_msg}

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
            if is_browser_crash_error(e):
                console.log(f"Browser crash during raw(), recovering: {e}")
                await self._recover_from_crash()
                page = await self.get_page()
                try:
                    result = await self._eval_ast(tree.body, page)
                    return result
                except Exception as retry_e:
                    raise ValueError(f"Failed after recovery: {str(retry_e)}")
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
        async with self._get_async_lock():
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
            # Reset lock so it's recreated fresh on next use
            self._async_lock = None
            # Reset event loop reference
            self._loop = None
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Check if the browser is initialized."""
        return self._browser is not None


# Global singleton instance
browser_manager = BrowserManager()
