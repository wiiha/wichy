"""
Hook types for the Wichy hooks system.

This module defines the hook type enum, priority levels, and the registered
hook dataclass used internally by the hook registry.

Hook Categories:
    - Tool hooks (PRE_TOOL, POST_TOOL): Triggered before/after tool invocations.
      Registered with a specific tool_name or None (wildcard matching all tools).
    - Lifecycle hooks (SESSION_START, SESSION_END, CONTEXT_*): Triggered by session
      and context lifecycle events. Always registered with tool_name=None since there
      is no tool being invoked. The hook type itself identifies the event.

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

    Hook types are divided into two categories:

    **Tool Hooks** (registered with a specific tool_name or None for wildcard):
        PRE_TOOL: Executed before a tool runs. Can approve, deny, or modify inputs.
        POST_TOOL: Executed after a tool runs. Can approve, deny, or modify outputs.

    **Lifecycle Hooks** (always registered with tool_name=None, since no tool is
    being invoked; the hook type itself identifies the event):
        SESSION_START: Executed when a wichy session starts.
        SESSION_END: Executed when a wichy session ends.
        CONTEXT_RESET_PRE: Executed before context is reset.
        CONTEXT_RESET_POST: Executed after context is reset.
        CONTEXT_COMPACT_PRE: Executed before context compaction.
        CONTEXT_COMPACT_POST: Executed after context compaction.

    For lifecycle hooks, event-specific data is provided in the HookContext.event_data
    dictionary rather than through tool-related fields.
    """

    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    CONTEXT_RESET_PRE = "context_reset_pre"
    CONTEXT_RESET_POST = "context_reset_post"
    CONTEXT_COMPACT_PRE = "context_compact_pre"
    CONTEXT_COMPACT_POST = "context_compact_post"
    PRE_USER_MESSAGE = "pre_user_message"
    PRE_RESPONSE_TO_USER = "pre_response_to_user"


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
        hook_type: The type of hook (PRE_TOOL, POST_TOOL, or lifecycle hooks)
        tool_name: For tool hooks, the name of the tool to hook (e.g., "bash",
            "write_file"), or None to match all tools (wildcard). For lifecycle
            hooks (SESSION_START, SESSION_END, CONTEXT_*), this is always None
            since there is no tool being invoked.
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
