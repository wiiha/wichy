from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from wichy.tools.base import BaseTool


def get_tool_definitions(tools: Iterable[BaseTool]) -> list[dict[str, Any]]:
    """Convert a list of tools to function definitions."""
    return [tool.to_function_definition() for tool in tools]
