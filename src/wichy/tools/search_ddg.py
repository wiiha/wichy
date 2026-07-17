from typing import Any, Optional

from ddgs import DDGS  # ref: https://pypi.org/project/ddgs/
from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class WebSearchParameters(ParametersModel):
    query: str = Field(..., description="The search query string")
    max_results: Optional[int] = Field(
        5, description="Maximum number of results to return (default: 5)"
    )

    def info(self) -> str:
        return f'query="{self.query}" max_results={self.max_results}'


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search online for information using DuckDuckGo (DDG) API."
    description_long = """
- Allows to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks, including links
- Use this tool for accessing information beyond your knowledge cutoff
- Searches are performed automatically within a single API call

CRITICAL REQUIREMENT - You MUST follow this:

- After answering the user's question, you MUST include a "Sources:" section at the end of your response
- In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)
- This is MANDATORY - never skip including sources in your response
- Example format:

  [Your answer here]

  Sources:
  - [Source Title 1](https://example.com/1)
  - [Source Title 2](https://example.com/2)

IMPORTANT - Use the correct year in search queries:

- If today's date is 2026-01-02. You MUST use this year when searching for recent information, documentation, or current events.
- Example: If the user asks for "latest React docs", search for "React documentation 2026", NOT "React documentation 2025"
"""
    parameters_model = WebSearchParameters
    needs_verification_in_api: bool = False

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute DDG search with given parameters."""
        query: str = kwargs["query"]
        max_results: int = kwargs.get("max_results", 5)
        try:
            results = DDGS().text(query, max_results=max_results)

            # Format results
            output = ""
            for i, result in enumerate(results):
                if i > 0:
                    output += "\n"
                output += f"Title: {result['title']}\n"
                output += f"URL: {result['href']}\n"
                output += f"Snippet: {result['body']}\n"
                output += "\n"
                output += "-" * 10

            return output

        except Exception as e:
            return format_error(str(e))
