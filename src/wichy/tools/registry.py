"""
Simple tool registry for auto-registering and looking up tools.

This module provides a lightweight registry pattern where tools automatically
register themselves when their classes are defined (via a metaclass on BaseTool).
"""

from __future__ import annotations

from abc import ABCMeta
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from wichy.tools.base import BaseTool

# Module-level registry storing tool classes keyed by their name attribute
_registry: dict[str, type[BaseTool]] = {}


def register_tool(tool_class: type[BaseTool]) -> None:
    """
    Register a tool class in the registry.

    Called automatically by the ToolMeta metaclass when a BaseTool subclass
    is defined. Can also be called manually if needed.

    Args:
        tool_class: The tool class to register. Must have a 'name' attribute.
    """
    if hasattr(tool_class, "name") and tool_class.name:
        _registry[tool_class.name] = tool_class


def get_all_tools() -> list[type[BaseTool]]:
    """
    Get all registered tool classes.

    Returns:
        A list of all registered tool classes.
    """
    return list(_registry.values())


def get_tool_by_name(name: str) -> type[BaseTool] | None:
    """
    Look up a tool class by its name.

    Args:
        name: The tool name to look up (e.g., "read_file").

    Returns:
        The tool class if found, None otherwise.
    """
    return _registry.get(name)


def get_tools_by_names(names: list[str]) -> list[type[BaseTool]]:
    """
    Look up multiple tool classes by their names.

    Args:
        names: A list of tool names to look up.

    Returns:
        A list of found tool classes. Tools not found are omitted from the list.
    """
    tools: list[type[BaseTool]] = []
    for name in names:
        tool = _registry.get(name)
        if tool is not None:
            tools.append(tool)
    return tools


def clear_registry() -> None:
    """
    Clear all tools from the registry.

    This is intended for testing purposes to isolate test tools from
    production tools. Use with caution.
    """
    _registry.clear()


def get_registry_copy() -> dict[str, type[BaseTool]]:
    """
    Get a copy of the current registry state.

    Useful for saving registry state in tests to restore later.

    Returns:
        A copy of the registry dictionary.
    """
    return _registry.copy()


def restore_registry(saved_state: dict[str, type[BaseTool]]) -> None:
    """
    Restore the registry to a previously saved state.

    Useful for restoring registry state in tests after modifications.

    Args:
        saved_state: A dictionary previously returned by get_registry_copy().
    """
    _registry.clear()
    _registry.update(saved_state)


class ToolMeta(ABCMeta):
    """
    Metaclass that automatically registers BaseTool subclasses.

    When a class using this metaclass is defined, it automatically calls
    register_tool() to add itself to the registry.
    """

    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> type:
        cls = super().__new__(mcs, name, bases, namespace)
        # Register the class if it has a 'name' attribute (i.e., not BaseTool itself)
        if hasattr(cls, "name") and cls.name:
            register_tool(cast("type[BaseTool]", cls))
        return cls
