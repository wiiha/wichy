import asyncio
import random

import markdownify
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

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
        # Set up random user agent and other headers
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
        ]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            # Create a new context with custom settings
            context = await browser.new_context(
                user_agent=random.choice(user_agents),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )

            page = await context.new_page()

            # Add random delays to mimic human behavior
            await page.wait_for_timeout(random.randint(1000, 3000))

            # Navigate to the URL
            await page.goto(url, wait_until="domcontentloaded")

            # Wait for content to load
            try:
                await page.wait_for_selector("body", timeout=30000)
            except:
                pass

            content = await page.content()

            await context.close()
            await browser.close()

            return content

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
