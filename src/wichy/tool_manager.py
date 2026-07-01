"""Tool manager - handles tool instantiation and filtering."""

import fnmatch
from typing import Iterable, List, Optional

from wichy.tools.base import BaseTool
from wichy.tools.registry import get_all_tools


def _matches_tool_patterns(name: str, patterns: Iterable[str]) -> bool:
    """Check if a tool name matches any of the given glob patterns (case-insensitive)."""
    lowered = name.lower()
    return any(
        fnmatch.fnmatch(lowered, pat.strip().lower()) for pat in patterns if pat.strip()
    )


class ToolManager:
    """Manages tool instantiation and filtering."""

    def __init__(self, all_tools: Optional[List[type[BaseTool]]] = None):
        """
        Initialize ToolManager.

        Args:
            all_tools: List of tool classes (not instances). Defaults to all registered tools.
        """
        if all_tools is not None:
            self.all_tools = all_tools
        else:
            self.all_tools = get_all_tools()

    def instantiate_all(self) -> List[BaseTool]:
        """
        Instantiate all available tools and sort by name.

        Returns:
            List of instantiated tools sorted alphabetically by name.
        """
        tools = [tool() for tool in self.all_tools]
        tools.sort(key=lambda t: t.name)
        return tools

    @staticmethod
    def filter_tools(
        tools: List[BaseTool], allowed: str = "", excluded: str = ""
    ) -> List[BaseTool]:
        """
        Filter tools based on allowed and exclusion lists.

        Args:
            tools: List of instantiated tools to filter
            allowed: Comma-separated list of glob patterns for tool names to include
                     (empty means all)
            excluded: Comma-separated list of glob patterns for tool names to exclude

        Returns:
            Filtered list of tools
        """
        if not allowed and not excluded:
            return tools

        # Start with all tools or filter by allowed
        if allowed:
            patterns = [p for p in allowed.split(",")]
            filtered = [
                tool for tool in tools if _matches_tool_patterns(tool.name, patterns)
            ]
        else:
            filtered = tools.copy()

        # Apply exclusions
        if excluded:
            patterns = [p for p in excluded.split(",")]
            filtered = [
                tool
                for tool in filtered
                if not _matches_tool_patterns(tool.name, patterns)
            ]

        return filtered

    def create_tools(self, allowed: str = "", excluded: str = "") -> List[BaseTool]:
        """
        Convenience method to instantiate and filter tools in one call.

        Args:
            allowed: Comma-separated list of glob patterns for tool names to include
                     (empty means all)
            excluded: Comma-separated list of glob patterns for tool names to exclude

        Returns:
            Final list of tools ready for use.
        """
        tools = self.instantiate_all()
        return self.filter_tools(tools, allowed, excluded)
