"""
Hook types for the Wichy hooks system.

This module defines the hook type enum, priority levels, and the registered
hook dataclass used internally by the hook registry.

Usage:
    from wichy.hooks.types import HookType, HookPriority, RegisteredHook
"""

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .context import HookContext
    from .result import HookResult


class HookType(Enum):
    """Types of hooks available in the system.

    Each hook type is triggered at a different point in the tool execution
    lifecycle.

    Attributes:
        PRE_TOOL: Executed before a tool runs. Can approve, deny, or modify inputs.
        POST_TOOL: Executed after a tool runs. Can approve, deny, or modify outputs.
    """

    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"


class HookPriority(Enum):
    """Priority levels for hook execution order.

    Lower values execute earlier. Use these constants for common cases,
    or specify custom integers for fine-grained control.

    Attributes:
        EARLY: Execute early (value 10)
        NORMAL: Normal execution order (value 50, default)
        LATE: Execute late (value 90)
    """

    EARLY = 10
    NORMAL = 50
    LATE = 90


@dataclass
class RegisteredHook:
    """Internal representation of a registered hook.

    This dataclass stores all information about a hook that has been
    registered with the hook registry.

    Attributes:
        hook_type: The type of hook (PRE_TOOL or POST_TOOL)
        tool_name: Name of the tool to hook (None = wildcard for all tools)
        function: The hook function to execute
        priority: Execution priority (lower = earlier). Default is 50.
        name: Human-readable name for the hook (defaults to function name)
        source: Where the hook was registered from ("python", "yaml", or "shell")
        enabled: Whether the hook is currently active
    """

    hook_type: HookType
    tool_name: Optional[str]
    function: Callable[["HookContext"], "HookResult"]
    priority: int = 50
    name: str = ""
    source: str = "python"
    enabled: bool = True
