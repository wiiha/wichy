"""
Base class for agent implementations.

This module provides AgentCore, an abstract base class containing
shared functionality between RootAgent and TaskAgent.
"""

import json
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from wichy.config import settings
from wichy.constants import ROLE_ASSISTANT, ROLE_TOOL
from wichy.event_log.schema import preview_args
from wichy.helpers.multimodal import (
    build_multimodal_user_message,
    extract_multimodal_content,
    fix_multimodal_context,
)

if TYPE_CHECKING:
    from wichy.llm_backend import Message, called_tool
    from wichy.tools.base import BaseTool


class AgentCore(ABC):
    """
    Abstract base class providing shared agent functionality.

    This class contains common code for LLM-based agents that use tools
    and handle multimodal content. Both RootAgent and TaskAgent inherit
    from this base class.
    """

    def __init__(self):
        """Initialize base attributes. Subclasses must call super().__init__()."""
        # Set by subclasses
        self.model_str: str = ""
        self.tools: List = []  # List[BaseTool] at runtime

        # Set by subclasses via context handler
        self.context = None

    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Hook for subclasses to emit events. Default does nothing."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the agent name."""
        pass

    # -------------------------------------------------------------------------
    # Logging methods - subclasses override for different console behavior
    # -------------------------------------------------------------------------

    def _log(self, message: str) -> None:
        """
        Log a debug message.

        Subclasses override to use different console instances.
        Default implementation does nothing.
        """
        pass

    def _log_dict(self, data: Dict) -> None:
        """
        Log a dictionary.

        Subclasses override to use different console instances.
        Default implementation does nothing.
        """
        pass

    # -------------------------------------------------------------------------
    # Shared tool execution logic
    # -------------------------------------------------------------------------

    def _tool_call(
        self,
        tools: List["BaseTool"],
        item: "called_tool",
        inject_model_str: bool = False,
    ) -> Tuple[Dict, Optional[List[Dict[str, Any]]]]:
        """
        Execute a tool call and return the result message.

        Args:
            tools: List of available tools
            item: The tool call to execute
            inject_model_str: If True, add model_str to tool args (RootAgent behavior)

        Returns:
            Tuple of (tool_result_message, multimodal_content_parts or None)
        """
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)

        self._log_dict({"tool": name, "args": args})

        if inject_model_str:
            args["model_str"] = self.model_str

        self._emit_event(
            "tool_call_started",
            {
                "tool_name": name,
                "tool_call_id": item.id,
                "args_preview": preview_args(args),
            },
        )

        # Check if query_result is available to this agent
        can_query_results = any(t.name == "query_result" for t in tools)
        args["_can_query_results"] = can_query_results

        start_time = time.monotonic()
        try:
            for tool in tools:
                if name == tool.name:
                    result = tool.validate_and_execute(**args)
                    break

            if result is None:
                result = "There is no tool called " + item.function.name + "."

            # Check for multimodal content in tool result
            display_content, multimodal_parts = extract_multimodal_content(result)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            self._emit_event(
                "tool_call_completed",
                {
                    "tool_name": name,
                    "tool_call_id": item.id,
                    "execution_time_ms": duration_ms,
                    "result_char_count": len(str(display_content)),
                },
            )

            tool_message = {
                "role": ROLE_TOOL,
                "tool_call_id": item.id,
                "content": display_content,
            }
            return tool_message, multimodal_parts
        except Exception as e:
            self._emit_event(
                "tool_call_failed",
                {
                    "tool_name": name,
                    "tool_call_id": item.id,
                    "error_type": type(e).__name__,
                    "error_message": str(e)[:500],
                },
            )
            raise

    # -------------------------------------------------------------------------
    # Shared tool handling logic
    # -------------------------------------------------------------------------

    def _handle_tools_base(
        self,
        tools: List["BaseTool"],
        response: "Message",
        inject_model_str: bool = False,
        pre_append_hook: Optional[Callable[[Any], None]] = None,
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Base implementation for handling tool calls from LLM response.

        This method handles the common logic:
        - Checking if response contains tool calls
        - Appending assistant message with tool calls
        - Executing each tool call
        - Collecting multimodal content

        Args:
            tools: List of available tools
            response: The LLM response message
            inject_model_str: If True, inject model_str into tool args
            pre_append_hook: Optional callback called before processing tool calls

        Returns:
            Tuple of (context_was_modified, multimodal_parts_list)
        """
        if response.finish_reason != "tool_calls":
            return False, []

        assert response.tool_calls is not None
        if pre_append_hook:
            pre_append_hook(response)

        entry = {
            "role": ROLE_ASSISTANT,
            "content": response.content,
            "tool_calls": [t.model_dump() for t in response.tool_calls],
        }

        if response.reasoning:
            entry["reasoning"] = response.reasoning

        self.context.append(entry)

        self._emit_event(
            "tool_call_batch_started",
            {
                "tool_call_count": len(response.tool_calls),
                "parallel_enabled": len(response.tool_calls) > 1 and settings.parallel_exec,
            },
        )

        self._log(
            "[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]"
        )
        osz = len(self.context)

        tool_results: list[Optional[tuple[Dict, Optional[List[Dict[str, Any]]]]]] = [
            None
        ] * len(response.tool_calls)
        multimodal_parts: List[Dict[str, Any]] = []

        # Parallel execution when multiple tool calls exist
        if len(response.tool_calls) > 1 and settings.parallel_exec:
            # Each tool gets its own thread; results collected by future index
            def run_one(idx: int, item: "called_tool") -> tuple[
                int,
                Tuple[Dict, Optional[List[Dict[str, Any]]]],
            ]:
                return idx, self._tool_call(tools, item, inject_model_str)

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(run_one, i, item): i
                    for i, item in enumerate(response.tool_calls)
                }
                for future in as_completed(futures):
                    idx, (tool_message, mm_parts) = future.result()
                    tool_results[idx] = (tool_message, mm_parts)

            # Append results in original LLM order, grouping multimodal per-tool
            for item_result in tool_results:
                if item_result is not None:
                    tool_message, mm_parts = item_result
                    self.context.append(tool_message)
                    if mm_parts:
                        multimodal_parts.extend(mm_parts)
        else:
            # Sequential path: single tool or --seq-exec / WICHY_PARALLEL_EXEC=false
            for item in response.tool_calls:
                tool_message, mm_parts = self._tool_call(tools, item, inject_model_str)
                self.context.append(tool_message)
                if mm_parts:
                    multimodal_parts.extend(mm_parts)

        # If any tool returned multimodal content, inject a user message with it
        if multimodal_parts:
            multimodal_message = build_multimodal_user_message(multimodal_parts)
            self.context.append(multimodal_message)
            self._log("[italic]injected multimodal content into context[/italic]")

        return len(self.context) != osz, multimodal_parts

    # -------------------------------------------------------------------------
    # Shared multimodal context fixing
    # -------------------------------------------------------------------------

    def _fix_multimodal_context(self) -> bool:
        """
        Find and replace multimodal content in context with text placeholders.

        Returns:
            True if any multimodal content was found and replaced, False otherwise.
        """
        found = fix_multimodal_context(self.context)
        if found:
            self._log("[yellow]Fixed multimodal content in context[/yellow]")
        return found

    # -------------------------------------------------------------------------
    # Convenience methods
    # -------------------------------------------------------------------------

    def _get_tool_definitions(self) -> List[Dict]:
        """Get tool definitions for all tools."""
        from wichy.tools import get_tool_definitions

        definitions: List[Dict[str, Any]] = get_tool_definitions(self.tools)
        return definitions
