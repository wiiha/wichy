from typing import Literal

import base64
import os
import random
import tempfile

import markdownify
from pydantic import Field, field_validator, model_validator

from wichy.helpers.browser import browser_manager
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error

# Type aliases for parameter validation
WaitUntilType = Literal["load", "domcontentloaded", "networkidle"]
WaitUntilActType = Literal["navigation", "none"]
ActionType = Literal["click", "fill", "wait"]
DetailType = Literal["quick", "full"]


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
    wait_until: WaitUntilType = Field(
        "networkidle",
        description="When to consider navigation complete. Options: 'load', 'domcontentloaded', 'networkidle'.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        return v

    @field_validator("limit")
    @classmethod
    def validate_limit_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("limit must be positive")
        return v

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
                return format_error(nav_result.get("error", "Navigation failed"))

            # Get the page content
            content = await page.content()
            return content
        except Exception as e:
            return format_error(str(e))

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

        async def _operation():
            return await self._fetch_webpage(url, wait_until)

        try:
            html_content = browser_manager.execute_serialized(_operation)

            # If content is an error message, return it directly
            if html_content.startswith("error:"):
                return html_content

            content_md = markdownify.markdownify(html=html_content, heading_style="ATX")

            if len(content_md) <= limit:
                return content_md
            else:
                return build_content_overview(content_md, limit)
        except Exception as e:
            return format_error(str(e))


class NavigateParameters(ParametersModel):
    url: str = Field(..., description="The URL to navigate to.")
    wait_until: WaitUntilType = Field(
        "networkidle",
        description="When to consider navigation complete. Options: 'load', 'domcontentloaded', 'networkidle'.",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        return v

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

        async def _operation():
            return await browser_manager.navigate(url, wait_until=wait_until)

        try:
            result = browser_manager.execute_serialized(_operation)

            if result.get("status") == "success":
                return f"Successfully navigated to {result.get('url')}\nTitle: {result.get('title')}"
            else:
                return format_error(result.get("error", "Navigation failed"))
        except Exception as e:
            return format_error(str(e))


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

        async def _operation():
            return await browser_manager.status()

        try:
            result = browser_manager.execute_serialized(_operation)

            if "url" in result:
                return f"Current page: {result['url']}\nTitle: {result['title']}"
            else:
                return f"Browser status: {result.get('status', 'unknown')}"
        except Exception as e:
            return format_error(str(e))


class BrowserPageInfoParameters(ParametersModel):
    detail: DetailType = Field(
        "quick",
        description="Detail level: 'quick' returns URL and title only; 'full' returns structured page info including headings, links, buttons, inputs, tables.",
    )

    def info(self):
        return f'detail="{self.detail}"'


class BrowserPageInfoTool(BaseTool):
    name = "browser_page_info"
    description = "Get information about the current page. Use detail='quick' for URL/title only, or detail='full' for structured page insight including headings, links, buttons, inputs, and tables. Use this to understand what's on the page before taking actions."
    parameters_model = BrowserPageInfoParameters

    def execute(self, detail: str = "quick") -> str:
        """
        Get structured information about the current page.

        Args:
            detail: 'quick' for URL and title only, 'full' for detailed page structure.

        Returns:
            JSON string with page information.
        """
        import json

        async def _operation():
            return await browser_manager._get_page_info(detail)

        try:
            result = browser_manager.execute_serialized(_operation)

            if "error" in result:
                return format_error(result["error"])

            return json.dumps(result, indent=2)
        except Exception as e:
            return format_error(str(e))


class ScreenshotParameters(ParametersModel):
    filename: str = Field(
        ...,
        description="The filename or path where to save the screenshot. Use 'base64' to return raw base64 data instead of saving to file. If only a filename is provided (no directory), the file will be saved to the system temp directory.",
    )
    fullpage: bool = Field(
        False,
        description="If True, capture the full scrollable page. If False, capture only the viewport.",
    )

    @field_validator("filename")
    @classmethod
    def validate_filename_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("filename cannot be empty")
        return v

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

        async def _operation():
            return await browser_manager.screenshot(fullpage=fullpage)

        try:
            screenshot_bytes = browser_manager.execute_serialized(_operation)

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
            return format_error(str(e))


class BrowserRawParameters(ParametersModel):
    code: str = Field(
        ...,
        description="Playwright Page method expression to execute. Examples: 'title()', '.url', 'content()', \"query_selector('h1').text_content()\", \"query_selector_all('a')\", \"wait_for_selector('.item')\", \"evaluate('document.title')\"",
    )
    limit: int = Field(
        20000,
        description="Maximum number of characters to return. Default is 20000. If limit is exceeded the return data is truncated.",
    )

    @field_validator("limit")
    @classmethod
    def validate_limit_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("limit must be positive")
        return v

    def info(self):
        msg = f'code="{self.code}"'
        if self.limit:
            msg += f' limit="{self.limit}"'
        return msg


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

    def execute(self, code: str, limit: int = 20000) -> str:
        """
        Execute raw Playwright code on the browser page.

        Args:
            code: A Playwright Page method expression (without the 'page.' prefix).
                  Examples: 'title()', '.url', 'content()', "query_selector('h1').text_content()"

        Returns:
            The result of the expression as a string representation.
        """

        async def _operation():
            return await browser_manager.raw(code)

        try:
            result = browser_manager.execute_serialized(_operation)

            # Format result for display
            if result is None:
                return "null"
            elif isinstance(result, (list, tuple)):
                # # Format lists with indices for readability
                # if len(result) > 20:
                #     return f"[{len(result)} items] " + str(result[:20])[:-1] + ", ...]"
                return str(result)
            elif isinstance(result, str) and len(result) > limit:
                msg = (
                    result[:limit]
                    + "\n... [truncated]"
                    + f" {len(result) - limit} chars were truncated."
                )
                return msg
            else:
                return str(result)
        except ValueError as e:
            return format_error(str(e))
        except Exception as e:
            return format_error(str(e))


class BrowserActParameters(ParametersModel):
    action: ActionType = Field(
        ...,
        description="Action to perform: 'click' (click element), 'fill' (fill form input), or 'wait' (wait for element)",
    )
    target: str = Field(
        ...,
        description="Target element: text to click (for click), input name/placeholder/id (for fill), or CSS selector (for wait)",
    )
    value: str | None = Field(
        None, description="Value to type (required for 'fill' action)"
    )
    wait_until: WaitUntilActType = Field(
        "navigation",
        description="For click action: what to wait for after clicking. Options: 'navigation' (wait for page load), 'none' (no wait).",
    )
    timeout: int = Field(
        5000, description="Timeout in milliseconds for wait action. Default: 5000"
    )

    @model_validator(mode="after")
    def validate_fill_requires_value(self) -> "BrowserActParameters":
        if self.action == "fill" and self.value is None:
            raise ValueError("value is required when action is 'fill'")
        return self

    @field_validator("target")
    @classmethod
    def validate_target_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("target cannot be empty")
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("timeout must be non-negative")
        return v

    def info(self):
        info_str = f'action="{self.action}", target="{self.target}"'
        if self.value is not None:
            info_str += f', value="{self.value}"'
        if self.wait_until != "navigation":
            info_str += f', wait_until="{self.wait_until}"'
        if self.timeout != 5000:
            info_str += f", timeout={self.timeout}"
        return info_str


class BrowserActTool(BaseTool):
    name = "browser_act"
    description = "Perform browser actions declaratively. Use action='click' to click elements by text, action='fill' to fill form inputs by name/placeholder/id, or action='wait' to wait for elements to appear. Auto-waits for elements to be visible before acting."
    parameters_model = BrowserActParameters

    def execute(
        self,
        action: str,
        target: str,
        value: str | None = None,
        wait_until: str = "navigation",
        timeout: int = 5000,
    ) -> str:
        """
        Perform a declarative browser action.

        Args:
            action: Action to perform: 'click', 'fill', or 'wait'
            target: Target element identifier (text for click, name/placeholder/id for fill, CSS selector for wait)
            value: Value to type (required for 'fill' action)
            wait_until: For click: 'navigation' to wait for page load, 'none' for no wait
            timeout: Timeout in milliseconds for wait operations

        Returns:
            A human-readable message indicating the result.
        """

        async def _operation():
            return await browser_manager._act(
                action=action,
                target=target,
                value=value,
                wait_until=wait_until,
                timeout=timeout,
            )

        try:
            result = browser_manager.execute_serialized(_operation)

            if result.get("status") == "success":
                action_type = result.get("action", action)
                action_verb = {
                    "click": "Clicked",
                    "fill": "Filled",
                    "wait": "Waited for",
                }
                verb = action_verb.get(action_type, "Performed action on")
                return f"{verb} '{target}'"
            else:
                return format_error(result.get("error", "Unknown error"))

        except Exception as e:
            return format_error(str(e))
