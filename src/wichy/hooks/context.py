"""HookContext for the Wichy hooks system.

This module provides the context object passed to every hook during execution.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .types import HookType


@dataclass
class HookContext:
    """Context passed to every hook with runtime information.

    This dataclass contains all the information a hook might need during execution,
    including tool details, input/output, execution metadata, and shared state.

    For tool hooks (PRE_TOOL, POST_TOOL), tool_name and input_args contain tool-specific data.
    For lifecycle hooks (SESSION_START, SESSION_END, CONTEXT_RESET_*, CONTEXT_COMPACT_*,
    PRE_USER_MESSAGE, PRE_RESPONSE_TO_USER), tool_name is None and lifecycle-specific data
    is passed via the event_data dict.

    event_data contents by hook type:
        - CONTEXT_RESET_PRE/POST: context_handler, root_agent, reset_strategy
        - CONTEXT_COMPACT_PRE: context_handler, root_agent, is_auto_compact
        - CONTEXT_COMPACT_POST: context_handler, root_agent, summary, is_auto_compact
        - PRE_USER_MESSAGE: context_handler, root_agent, message
        - PRE_RESPONSE_TO_USER: context_handler, root_agent, response_content, response_reasoning, usage

    Attributes:
        tool_name: Name of the tool being executed (e.g., "bash", "write_file").
            None for lifecycle hooks.
        tool_instance: The tool instance being executed. None for lifecycle hooks.
        input_args: Validated input arguments after validation. Empty for lifecycle hooks.
        raw_input_args: Raw input arguments before validation. Empty for lifecycle hooks.
        output: Tool output/result (None for pre_tool hooks, or for lifecycle hooks).
            For PRE_RESPONSE_TO_USER this carries the response_content and is updated
            between hooks to reflect cumulative modifications.
        error: Exception if tool raised one (None if successful)
        execution_id: Unique ID for this tool execution (for tracing)
        timestamp: When the hook was invoked
        session_id: Session/conversation ID (if available)
        working_directory: Current working directory
        environment: Environment variables snapshot
        user_message: User message that triggered this tool call (if available)
        conversation_turn: Turn number in conversation
        state: Shared state between pre/post hooks (mutable)
        hook_type: The type of hook being executed (e.g., PRE_TOOL, CONTEXT_RESET_PRE)
        lifecycle_event: For lifecycle hooks, the specific lifecycle event type
        event_data: For lifecycle hooks, contains event-specific data (context_handler,
            root_agent, and hook-specific fields like summary, reset_strategy, is_auto_compact)
    """

    tool_name: Optional[str]
    tool_instance: Any
    input_args: Dict[str, Any] = field(default_factory=dict)
    raw_input_args: Dict[str, Any] = field(default_factory=dict)
    execution_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    working_directory: Path = field(default_factory=lambda: Path.cwd())
    environment: Dict[str, str] = field(default_factory=dict)
    output: Optional[Any] = None
    error: Optional[Exception] = None
    session_id: Optional[str] = None
    user_message: Optional[str] = None
    conversation_turn: Optional[int] = None
    state: Dict[str, Any] = field(default_factory=dict)
    hook_type: Optional["HookType"] = None
    lifecycle_event: Optional[str] = None
    event_data: Dict[str, Any] = field(default_factory=dict)
