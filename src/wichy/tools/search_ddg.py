from typing import Optional

from ddgs import DDGS  # ref: https://pypi.org/project/ddgs/
from pydantic import BaseModel, Field

from .base import BaseTool


class SearchDDGParameters(BaseModel):
    query: str = Field(..., description="The search query string")
    max_results: Optional[int] = Field(
        5, description="Maximum number of results to return (default: 5)"
    )


class SearchDDGTool(BaseTool):
    name = "web_search"
    description = "Search online for information using DuckDuckGo (DDG) API."
    parameters_model = SearchDDGParameters

    def execute(self, query: str, max_results: int = 5) -> str:
        """Execute DDG search with given parameters."""
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
            return f"error: {str(e)}"
