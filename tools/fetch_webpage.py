from pydantic import BaseModel, Field
from .base import BaseTool
import asyncio
from playwright.async_api import async_playwright
import markdownify


class FetchWebPageParameters(BaseModel):
    url: str = Field(..., description="The URL to visit.")


class FetchWebPageTool(BaseTool):
    name = "fetch_webpage"
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
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            await page.goto(url)
            content = await page.content()
            
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