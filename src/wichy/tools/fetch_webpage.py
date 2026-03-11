import asyncio
import random

import markdownify
from pydantic import Field

from wichy.helpers.browser import browser_manager
from wichy.tools.base import BaseTool, ParametersModel


class FetchWebPageParameters(ParametersModel):
    url: str = Field(..., description="The URL to visit.")

    def info(self):
        return 'url="' + self.url + '"'


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

    def execute(self, url: str) -> str:
        """
        Execute the fetch webpage tool.

        Args:
            url: The URL to fetch

        Returns:
            The text content of the page as markdown.
        """
        try:
            content = asyncio.run(self._fetch_webpage(url))
            content_md = markdownify.markdownify(html=content, heading_style="ATX")
            return content_md
        except Exception as e:
            return f"error: {str(e)}"
