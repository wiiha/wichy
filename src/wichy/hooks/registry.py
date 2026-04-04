"""
Hook registry for the Wichy hooks system.

This module provides a singleton registry for storing and managing hooks.
Hooks are registered with a type (PRE_TOOL, POST_TOOL, or lifecycle hooks),
an optional tool name (wildcard if None), and are executed in priority order.

Lifecycle hooks (SESSION_START, SESSION_END, CONTEXT_RESET_PRE, etc.) do not
have a tool context and are registered with tool_name=None.

Usage:
    from wichy.hooks.registry import hook_registry, register_hook

    # Register a wildcard hook (runs for all tools)
    register_hook(HookType.PRE_TOOL, my_function, priority=10)

    # Register a tool-specific hook
    register_hook(HookType.PRE_TOOL, my_function, tool_name="bash", priority=50)

    # Register a lifecycle hook
    register_hook(HookType.SESSION_START, my_function, priority=10)

    # Get hooks for a specific tool
    hooks = hook_registry.get_hooks(HookType.PRE_TOOL, "bash")

    # Get lifecycle hooks
    hooks = hook_registry.get_hooks_for_type(HookType.SESSION_START)
"""

from __future__ import annotations

import heapq
import threading
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from .result import HookResult
from .types import HookType, RegisteredHook

if TYPE_CHECKING:
    from .context import HookContext


class HookRegistry:
    """Singleton registry for managing hooks.

    This registry stores hooks organized by hook type and tool name.
    It supports wildcard hooks (tool_name=None) that apply to all tools,
    and tool-specific hooks that only apply to a particular tool.

    The registry is thread-safe and sorts hooks by priority on insertion.

    Attributes:
        _instance: The singleton instance
        _lock: Thread lock for thread-safe operations
        _hooks: Nested dict mapping hook_type -> tool_name -> list of RegisteredHook
        _execution_counter: Counter for generating unique execution IDs
    """

    _instance: Optional[HookRegistry] = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> HookRegistry:
        """Return the singleton instance, creating it if necessary."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize the registry's internal state."""
        self._hooks: Dict[HookType, Dict[Optional[str], List[RegisteredHook]]] = (
            defaultdict(lambda: defaultdict(list))
        )
        self._execution_counter: int = 0
        self._registry_lock: threading.Lock = threading.Lock()

    def register(
        self,
        hook_type: HookType,
        tool_name: Optional[str],
        function: Callable[[HookContext], HookResult],
        priority: int = 50,
        name: str = "",
        source: str = "python",
    ) -> None:
        """Register a hook in the registry.

        Args:
            hook_type: The type of hook (PRE_TOOL or POST_TOOL)
            tool_name: Name of the tool to hook (None = wildcard for all tools)
            function: The hook function to execute
            priority: Execution priority (lower = earlier). Default is 50.
            name: Human-readable name for the hook (defaults to function name)
            source: Where the hook was registered from ("python", "yaml", or "shell")
        """
        hook_name = name or function.__name__

        registered_hook = RegisteredHook(
            hook_type=hook_type,
            tool_name=tool_name,
            function=function,
            priority=priority,
            name=hook_name,
            source=source,
        )

        with self._registry_lock:
            self._hooks[hook_type][tool_name].append(registered_hook)
            self._hooks[hook_type][tool_name].sort(key=lambda h: h.priority)

    def unregister(self, hook_type: HookType, name: str) -> bool:
        """Remove a hook by name.

        Args:
            hook_type: The type of hook (PRE_TOOL or POST_TOOL)
            name: The name of the hook to remove

        Returns:
            True if a hook was found and removed, False otherwise
        """
        with self._registry_lock:
            if hook_type not in self._hooks:
                return False

            for tool_name in list(self._hooks[hook_type].keys()):
                hooks_list = self._hooks[hook_type][tool_name]
                for i, hook in enumerate(hooks_list):
                    if hook.name == name:
                        hooks_list.pop(i)
                        return True

            return False

    def get_hooks(self, hook_type: HookType, tool_name: str) -> List[RegisteredHook]:
        """Get all hooks for a tool, including wildcards, sorted by priority.

        This method merges wildcard hooks (tool_name=None) and tool-specific
        hooks, then returns them sorted by priority.

        Since both lists are already sorted by priority at registration time,
        we use heapq.merge for O(n) merging instead of O(n log n) sorting.

        Args:
            hook_type: The type of hook (PRE_TOOL or POST_TOOL)
            tool_name: Name of the tool to get hooks for

        Returns:
            List of RegisteredHook objects sorted by priority
        """
        with self._registry_lock:
            # Both lists are already sorted by priority at registration time
            wildcard = self._hooks[hook_type].get(None, [])
            specific = self._hooks[hook_type].get(tool_name, [])
            # heapq.merge is O(n) for already-sorted iterables
            return list(heapq.merge(wildcard, specific, key=lambda h: h.priority))

    def get_hooks_for_type(self, hook_type: HookType) -> List[RegisteredHook]:
        """Get all hooks for a hook type, sorted by priority.

        This method is used for lifecycle hooks (SESSION_START, SESSION_END,
        CONTEXT_RESET_PRE, CONTEXT_RESET_POST, CONTEXT_COMPACT_PRE,
        CONTEXT_COMPACT_POST) which don't have a tool context.

        Args:
            hook_type: The type of hook (lifecycle hook types)

        Returns:
            List of RegisteredHook objects sorted by priority
        """
        with self._registry_lock:
            hooks = self._hooks[hook_type].get(None, [])
            return list(hooks)

    def clear(self) -> None:
        """Clear all registered hooks.

        This is primarily intended for testing purposes.
        """
        with self._registry_lock:
            self._hooks.clear()
            self._execution_counter = 0

    def list_all(self) -> Dict[HookType, Dict[Optional[str], List[RegisteredHook]]]:
        """Get a copy of all registered hooks.

        Useful for debugging or introspection.

        Returns:
            A copy of the hooks dictionary
        """
        with self._registry_lock:
            result: Dict[HookType, Dict[Optional[str], List[RegisteredHook]]] = {}
            for hook_type in self._hooks:
                result[hook_type] = {}
                for tool_name in self._hooks[hook_type]:
                    result[hook_type][tool_name] = list(
                        self._hooks[hook_type][tool_name]
                    )
            return result

    def generate_execution_id(self) -> str:
        """Generate a unique execution ID.

        Returns:
            A unique identifier string for tracking hook executions
        """
        with self._registry_lock:
            self._execution_counter += 1
            return f"hook_{self._execution_counter}_{uuid.uuid4().hex[:8]}"


# Module-level singleton instance
hook_registry: HookRegistry = HookRegistry()


def register_hook(
    hook_type: HookType,
    function: Callable[[HookContext], HookResult],
    tool_name: Optional[str] = None,
    priority: int = 50,
    name: str = "",
    source: str = "python",
) -> None:
    """Convenience function to register a hook.

    Args:
        hook_type: The type of hook (PRE_TOOL, POST_TOOL, or lifecycle hooks)
        function: The hook function to execute
        tool_name: Name of the tool to hook (None = wildcard for all tools,
            or None for lifecycle hooks which don't have a tool context)
        priority: Execution priority (lower = earlier). Default is 50.
        name: Human-readable name for the hook (defaults to function name)
        source: Where the hook was registered from ("python", "yaml", or "shell")
    """
    hook_registry.register(
        hook_type=hook_type,
        tool_name=tool_name,
        function=function,
        priority=priority,
        name=name,
        source=source,
    )


def get_hooks_for_tool(hook_type: HookType, tool_name: str) -> List[RegisteredHook]:
    """Convenience function to get hooks for a specific tool.

    Args:
        hook_type: The type of hook (PRE_TOOL or POST_TOOL)
        tool_name: Name of the tool to get hooks for

    Returns:
        List of RegisteredHook objects sorted by priority
    """
    return hook_registry.get_hooks(hook_type, tool_name)


def get_hooks_for_type(hook_type: HookType) -> List[RegisteredHook]:
    """Convenience function to get hooks for a hook type.

    Used for lifecycle hooks which don't have a tool context.

    Args:
        hook_type: The type of hook (lifecycle hook types)

    Returns:
        List of RegisteredHook objects sorted by priority
    """
    return hook_registry.get_hooks_for_type(hook_type)


def clear_hooks() -> None:
    """Convenience function to clear all hooks.

    This is primarily intended for testing purposes.
    """
    hook_registry.clear()
