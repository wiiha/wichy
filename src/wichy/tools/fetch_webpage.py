import asyncio
import base64
import os
import random
import tempfile

import markdownify
from pydantic import Field

from wichy.helpers.browser import browser_manager
from wichy.tools.base import BaseTool, ParametersModel

# Global event loop - persists for entire session
_loop = None


def get_event_loop():
    """
    Get or create the global asyncio event loop.

    Creates a single persistent loop for the entire session to avoid
    invalidating browser objects tied to old loops.
    """
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop


def build_content_overview(content_md: str, limit: int) -> str:
    """
    Build an overview of content when it exceeds the limit by extracting headings.

    Args:
        content_md: The markdown content
        full_len: The full length of the original content
        limit: The character limit that was exceeded

    Returns:
        A message with overview containing all headings.
    """
    lines = content_md.split("\n")
    headings = []
    full_len = len(content_md)
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and stripped.lstrip("#").strip() != "":
            headings.append(stripped)

    base_msg = f"Content exceeds limit of {limit} characters (full length: {full_len} characters). "

    if headings:
        overview = "\n".join(headings)
        return (
            f"{base_msg}Headings:\n\n{overview}\n\n---\n\n"
            f"First {limit} of content:\n\n{content_md[:limit]}"
        )
    else:
        # Fallback: provide truncated content with indicator
        truncated_md = content_md[:limit] + "... [content truncated]"
        return f"{base_msg}and no headings were found. Showing first {limit} characters:\n\n{truncated_md}"


class FetchWebPageParameters(ParametersModel):
    url: str = Field(..., description="The URL to visit.")
    limit: int = Field(
        20000,
        description="Maximum number of characters to return. Default is 20000. If content exceeds this limit, an overview with headings is returned instead of full content.",
    )
    wait_until: str = Field(
        "networkidle",
        description="When to consider navigation complete. Options: 'load' (fires when load event is dispatched), 'domcontentloaded' (fires when DOMContentLoaded event is dispatched), 'networkidle' (fires when there are no more network connections for at least 500ms). Use 'load' or 'domcontentloaded' for faster fetching when you don't need JavaScript-rendered content.",
    )

    def info(self):
        return f'url="{self.url}", limit={self.limit}, wait_until="{self.wait_until}"'


class FetchWebPageTool(BaseTool):
    name = "web_fetch"
    description = "Fetch a webpage and return its text content as markdown."
    parameters_model = FetchWebPageParameters

    async def _fetch_webpage(self, url: str, wait_until: str = "networkidle") -> str:
        """
        Execute the fetch webpage tool.

        Args:
            url: The URL to fetch
            wait_until: When to consider navigation complete

        Returns:
            The text content of the page.
        """
        try:
            # Get the page and add human-like delay before navigation
            page = await browser_manager.get_page()
            await page.wait_for_timeout(random.randint(1000, 3000))

            # Use the navigate method for consistent navigation
            nav_result = await browser_manager.navigate(url, wait_until=wait_until)

            if nav_result.get("status") != "success":
                return f"error: {nav_result.get('error', 'Navigation failed')}"

            # Get the page content
            content = await page.content()
            return content
        except Exception as e:
            return f"error: {str(e)}"

    def execute(
        self, url: str, limit: int = 20000, wait_until: str = "networkidle"
    ) -> str:
        """
        Execute the fetch webpage tool.

        Args:
            url: The URL to fetch
            limit: Maximum characters to return (default: 20000)
            wait_until: When to consider navigation complete (default: "networkidle")

        Returns:
            The text content of the page as markdown, or an overview of headings if content exceeds limit.
        """
        try:
            loop = get_event_loop()
            html_content = loop.run_until_complete(self._fetch_webpage(url, wait_until))

            # If content is an error message, return it directly
            if html_content.startswith("error:"):
                return html_content

            content_md = markdownify.markdownify(html=html_content, heading_style="ATX")

            if len(content_md) <= limit:
                return content_md
            else:
                return build_content_overview(content_md, limit)
        except Exception as e:
            return f"error: {str(e)}"


class NavigateParameters(ParametersModel):
    url: str = Field(..., description="The URL to navigate to.")
    wait_until: str = Field(
        "networkidle",
        description="When to consider navigation complete. Options: 'load', 'domcontentloaded', 'networkidle'.",
    )

    def info(self):
        return f'url="{self.url}"'


class NavigateTool(BaseTool):
    name = "browser_navigate"
    description = "Navigate the browser to a URL."
    parameters_model = NavigateParameters

    def execute(self, url: str, wait_until: str = "networkidle") -> str:
        """
        Navigate the browser to a URL.

        Args:
            url: The URL to navigate to
            wait_until: When to consider navigation complete

        Returns:
            A message indicating the result of the navigation.
        """
        try:
            loop = get_event_loop()

            async def _navigate():
                result = await browser_manager.navigate(url, wait_until=wait_until)
                return result

            result = loop.run_until_complete(_navigate())

            if result.get("status") == "success":
                return f"Successfully navigated to {result.get('url')}\nTitle: {result.get('title')}"
            else:
                return f"error: {result.get('error', 'Navigation failed')}"
        except Exception as e:
            return f"error: {str(e)}"


class BrowserStatusParameters(ParametersModel):
    def info(self):
        return ""


class BrowserStatusTool(BaseTool):
    name = "browser_status"
    description = "Get the current status of the browser, including the current URL and page title."
    parameters_model = BrowserStatusParameters

    def execute(self) -> str:
        """
        Get the current status of the browser.

        Returns:
            A message with the current URL and page title, or status if unavailable.
        """
        try:
            loop = get_event_loop()

            async def _status():
                return await browser_manager.status()

            result = loop.run_until_complete(_status())

            if "url" in result:
                return f"Current page: {result['url']}\nTitle: {result['title']}"
            else:
                return f"Browser status: {result.get('status', 'unknown')}"
        except Exception as e:
            return f"error: {str(e)}"


class ScreenshotParameters(ParametersModel):
    filename: str = Field(
        ...,
        description="The filename or path where to save the screenshot. Use 'base64' to return raw base64 data instead of saving to file. If only a filename is provided (no directory), the file will be saved to the system temp directory.",
    )
    fullpage: bool = Field(
        False,
        description="If True, capture the full scrollable page. If False, capture only the viewport.",
    )

    def info(self):
        return f'filename="{self.filename}", fullpage={self.fullpage}'


class ScreenshotTool(BaseTool):
    name = "browser_screenshot"
    description = "Take a screenshot of the current browser page and save it to a file."
    parameters_model = ScreenshotParameters

    def execute(self, filename: str, fullpage: bool = False) -> str:
        """
        Take a screenshot of the current browser page.

        Args:
            filename: The filename or path where to save the screenshot. Use 'base64' to return
                      raw base64 data instead of saving. If only a filename is provided, saves
                      to system temp directory.
            fullpage: If True, capture the full scrollable page. If False, capture only the viewport.

        Returns:
            The file path where the screenshot was saved, or base64 data URI if filename is 'base64'.
        """
        try:

            loop = get_event_loop()

            async def _screenshot():
                return await browser_manager.screenshot(fullpage=fullpage)

            screenshot_bytes = loop.run_until_complete(_screenshot())

            # If filename is 'base64', return raw base64 data
            if filename == "base64":
                b64_data = base64.b64encode(screenshot_bytes).decode("utf-8")
                return f"data:image/png;base64,{b64_data}"

            # Determine the file path
            if os.path.isabs(filename) or os.path.dirname(filename):
                # Has a directory component - use as-is
                filepath = filename
                # Ensure directory exists
                dir_path = os.path.dirname(filepath)
                if dir_path and not os.path.exists(dir_path):
                    os.makedirs(dir_path)
            else:
                # Only filename provided - use temp directory
                temp_dir = tempfile.gettempdir()
                filepath = os.path.join(temp_dir, filename)

            # Ensure .png extension
            if not filepath.lower().endswith(".png"):
                filepath += ".png"

            # Write the screenshot to file
            with open(filepath, "wb") as f:
                f.write(screenshot_bytes)

            return filepath
        except Exception as e:
            return f"error: {str(e)}"


class BrowserRawParameters(ParametersModel):
    code: str = Field(
        ...,
        description="Playwright Page method expression to execute. Examples: 'title()', '.url', 'content()', \"query_selector('h1').text_content()\", \"query_selector_all('a')\", \"wait_for_selector('.item')\", \"evaluate('document.title')\"",
    )

    def info(self):
        return f'code="{self.code}"'


class BrowserRawTool(BaseTool):
    name = "browser_raw"
    description = (
        "Execute a raw Playwright Page API method on the current browser page. "
        "Allows querying elements, getting page info, waiting for selectors, and more. "
        "Examples: 'title()' returns page title, '.url' returns current URL, "
        "\"query_selector('h1').text_content()\" gets text from first h1 element, "
        "\"query_selector_all('a')\" returns all links, "
        "\"wait_for_selector('.item')\" waits for element to appear."
    )
    parameters_model = BrowserRawParameters

    def execute(self, code: str) -> str:
        """
        Execute raw Playwright code on the browser page.

        Args:
            code: A Playwright Page method expression (without the 'page.' prefix).
                  Examples: 'title()', '.url', 'content()', "query_selector('h1').text_content()"

        Returns:
            The result of the expression as a string representation.
        """
        try:
            loop = get_event_loop()

            async def _raw():
                return await browser_manager.raw(code)

            result = loop.run_until_complete(_raw())

            # Format result for display
            if result is None:
                return "null"
            elif isinstance(result, (list, tuple)):
                # Format lists with indices for readability
                if len(result) > 20:
                    return f"[{len(result)} items] " + str(result[:20])[:-1] + ", ...]"
                return str(result)
            elif isinstance(result, str) and len(result) > 5000:
                return result[:5000] + "\n... [truncated]"
            else:
                return str(result)
        except ValueError as e:
            return f"error: {str(e)}"
        except Exception as e:
            return f"error: {str(e)}"
