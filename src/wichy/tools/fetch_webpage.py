import asyncio
import random

import markdownify
from pydantic import Field

from wichy.helpers.browser import browser_manager
from wichy.tools.base import BaseTool, ParametersModel

# Global event loop management
_loop = None


def get_event_loop():
    """Get or create a global asyncio event loop for this tool."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        # Set as current event loop for this thread
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

    def info(self):
        return f'url="{self.url}", limit={self.limit}'


class FetchWebPageTool(BaseTool):
    name = "web_fetch"
    description = "Fetch a webpage and return its text content as markdown."
    parameters_model = FetchWebPageParameters

    async def _fetch_webpage(self, url: str) -> str:
        """
        Execute the fetch webpage tool.

        Args:
            url: The URL to fetch

        Returns:
            The text content of the page.
        """
        try:
            # Get the page and add human-like delay before navigation
            page = await browser_manager.get_page()
            await page.wait_for_timeout(random.randint(1000, 3000))

            # Use the navigate method for consistent navigation
            nav_result = await browser_manager.navigate(url, wait_until="networkidle")

            if nav_result.get("status") != "success":
                return f"error: {nav_result.get('error', 'Navigation failed')}"

            # Get the page content
            content = await page.content()
            return content
        except Exception as e:
            return f"error: {str(e)}"

    def execute(self, url: str, limit: int = 20000) -> str:
        """
        Execute the fetch webpage tool.

        Args:
            url: The URL to fetch
            limit: Maximum characters to return (default: 20000)

        Returns:
            The text content of the page as markdown, or an overview of headings if content exceeds limit.
        """
        try:
            loop = get_event_loop()
            html_content = loop.run_until_complete(self._fetch_webpage(url))

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
