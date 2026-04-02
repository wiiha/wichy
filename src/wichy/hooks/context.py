"""HookContext for the Wichy hooks system.

This module provides the context object passed to every hook during execution.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class HookContext:
    """Context passed to every hook with runtime information.

    This dataclass contains all the information a hook might need during execution,
    including tool details, input/output, execution metadata, and shared state.

    Attributes:
        tool_name: Name of the tool being executed (e.g., "bash", "write_file")
        tool_instance: The tool instance being executed
        input_args: Validated input arguments after validation
        raw_input_args: Raw input arguments before validation
        output: Tool output/result (None for pre_tool hooks)
        error: Exception if tool raised one (None if successful)
        execution_id: Unique ID for this tool execution (for tracing)
        timestamp: When the hook was invoked
        session_id: Session/conversation ID (if available)
        working_directory: Current working directory
        environment: Environment variables snapshot
        user_message: User message that triggered this tool call (if available)
        conversation_turn: Turn number in conversation
        state: Shared state between pre/post hooks (mutable)
    """

    tool_name: str
    tool_instance: Any
    input_args: Dict[str, Any]
    raw_input_args: Dict[str, Any]
    execution_id: str
    timestamp: datetime
    working_directory: Path
    environment: Dict[str, str]
    output: Optional[str] = None
    error: Optional[Exception] = None
    session_id: Optional[str] = None
    user_message: Optional[str] = None
    conversation_turn: Optional[int] = None
    state: Dict[str, Any] = field(default_factory=dict)
