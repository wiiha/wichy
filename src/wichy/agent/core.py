"""
Base class for agent implementations.

This module provides AgentCore, an abstract base class containing
shared functionality between RootAgent and TaskAgent.
"""

import json
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generator,
    List,
    Optional,
    Tuple,
)

from wichy.agent.loop_detector import LoopDetector, compute_signature
from wichy.config import settings
from wichy.constants import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER
from wichy.event_log.schema import preview_args
from wichy.helpers.multimodal import (
    build_multimodal_user_message,
    extract_multimodal_content,
    fix_multimodal_context,
)

if TYPE_CHECKING:
    from wichy.llm_backend import Message, called_tool
    from wichy.tools.base import BaseTool


#: Observers notified when an agent turn starts and ends. A turn observer is a
#: pair of callables ``(on_start, on_end)``. Both take the agent instance.
#: Features subscribe here rather than editing agent code, so adding a feature
#: never means touching this module.
_TurnObserver = Tuple[Callable[["AgentCore"], None], Callable[["AgentCore"], None]]

#: Process-wide observer list. Registration happens at import/setup time, well
#: before any turn runs. Mutations take _observers_lock and rebind the list
#: rather than mutating in place, so a concurrent reader always sees a
#: consistent list without needing the lock.
_turn_observers: List[_TurnObserver] = []
_observers_lock = threading.Lock()


def _snapshot_observers() -> List[_TurnObserver]:
    """Return the current observer list.

    Registration rebinds rather than appends, so this read is atomic without a
    lock and cannot observe a half-built list.

    Returns:
        The registered observer pairs.
    """
    return _turn_observers


def on_turn_started(observer: Callable[["AgentCore"], None]) -> None:
    """Register a callback fired when any agent turn begins.

    Args:
        observer: Called with the agent instance, before the turn body runs.
    """
    global _turn_observers
    with _observers_lock:
        _turn_observers = _turn_observers + [(observer, lambda agent: None)]


def on_turn_ended(observer: Callable[["AgentCore"], None]) -> None:
    """Register a callback fired when any agent turn ends, however it ended.

    The callback runs from a ``finally``, so it fires on success, on a raised
    exception, and on cancellation. Use it for anything that must be released
    or reported when a turn is over.

    Args:
        observer: Called with the agent instance, after the turn body exits.
    """
    global _turn_observers
    with _observers_lock:
        _turn_observers = _turn_observers + [(lambda agent: None, observer)]


def observe_turns(
    on_start: Callable[["AgentCore"], None],
    on_end: Callable[["AgentCore"], None],
) -> None:
    """Register a matched pair of turn callbacks in one call.

    Prefer this over separate :func:`on_turn_started` / :func:`on_turn_ended`
    calls, so a start can never be registered without its matching end.

    Args:
        on_start: Called before the turn body runs.
        on_end: Called after the turn body exits, successfully or not.
    """
    global _turn_observers
    with _observers_lock:
        _turn_observers = _turn_observers + [(on_start, on_end)]


def clear_turn_observers() -> None:
    """Remove every turn observer. Used for test isolation and re-setup."""
    global _turn_observers
    with _observers_lock:
        _turn_observers = []


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

        # Loop detection — each agent instance gets its own detector
        self.loop_detector = LoopDetector()

    @contextmanager
    def turn_scope(self) -> Generator[None, None, None]:
        """Frame one turn: notify turn observers, then guarantee an end notice.

        Wrap the body of a turn in this. The end notification is in a
        ``finally``, so a caller that needs "is this agent working" gets a
        correct answer even when the turn raises.

        Observer callbacks are isolated: one raising cannot break the turn or
        stop the other observers from running. They are for observation only,
        so a broken observer must never be able to break the agent.

        Yields:
            None. The wrapped body's return value is unaffected.
        """
        observers = _snapshot_observers()
        # The start notification is INSIDE the try. Outside it, a BaseException
        # from a start callback left the end notification un-run, so the turn
        # count never came back down and "agent is working" stuck true forever.
        # `_notify_turn_observers` catches `Exception` per callback, so only a
        # BaseException (KeyboardInterrupt, SystemExit) reaches here -- and it is
        # exactly the case that has to still close the turn.
        try:
            self._notify_turn_observers(observers, index=0)
            yield
        finally:
            self._notify_turn_observers(observers, index=1)

    def _notify_turn_observers(
        self, observers: List[_TurnObserver], index: int
    ) -> None:
        """Fire the *index*-th callable of each observer pair, ignoring errors.

        Args:
            observers: The snapshot taken when the turn started, so a turn
                observes a stable set even if registration happens mid-turn.
            index: 0 for the start callback, 1 for the end callback.
        """
        try:
            for pair in observers:
                callback = pair[index]
                try:
                    callback(self)
                except Exception as e:
                    # Deliberately swallowed and reported, never raised: a broken
                    # observer must not take down a turn in progress.
                    print(f"[wichy] turn observer failed: {e}")
        except BaseException:
            # A BaseException cannot be swallowed, but it must not stop the
            # REMAINING observers from being notified either. Letting it break the
            # loop meant that if this was an END notification, every later
            # observer's end callback never ran -- and an observer whose start ran
            # but whose end did not is one that counts a turn in and never out,
            # which is how "the agent is working" stays true forever.
            #
            # The rest are notified first, then the exception continues outward.
            for pair in observers[observers.index(pair) + 1 :]:
                callback = pair[index]
                try:
                    callback(self)
                except Exception as e:
                    print(f"[wichy] turn observer failed: {e}")
            raise

    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Hook for subclasses to emit events. Default does nothing."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the agent name."""
        pass

    @property
    def agent_id(self) -> str:
        """Stable id used by the kill registry and events.

        Root agents report "root"; task agents report their context
        custom_suffix (<name>-<hex>). The kill registry keys cascading
        kills on this value so inner calls of a task agent can be
        killed together.
        """
        context = getattr(self, "context", None)
        custom_suffix = getattr(context, "custom_suffix", None)
        if custom_suffix:
            return str(custom_suffix)
        return "root"

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
        # Sentinel: distinguishes "no matching tool found" from a tool that
        # legitimately returns None. A tool returning None is a valid result
        # and must not be overwritten with a "not found" error message.
        _NOT_FOUND = object()
        result = _NOT_FOUND
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

        # Hidden kill-registry kwargs, same convention as _can_query_results.
        # Lazy import: wichy.tools pulls task tools which import agent.core.
        from wichy.tools.kill_registry import generate_tool_call_id

        tool_call_id = item.id or generate_tool_call_id()
        args["_tool_call_id"] = tool_call_id
        args["_agent_id"] = self.agent_id

        start_time = time.monotonic()
        try:
            for tool in tools:
                if name == tool.name:
                    result = tool.validate_and_execute(**args)
                    break

            # Kill event from the executing thread's finalize handoff. The
            # record is already popped, so this cannot be an id lookup.
            from wichy.tools.kill_registry import (
                last_call_kill_reason,
                last_call_was_killed,
            )

            if last_call_was_killed():
                self._emit_event(
                    "tool_call_killed",
                    {
                        "tool_name": name,
                        "tool_call_id": tool_call_id,
                        "reason": last_call_kill_reason(),
                        "agent_id": self.agent_id,
                    },
                )

            if result is _NOT_FOUND:
                result = "There is no tool called " + item.function.name + "."

            # A tool may legitimately return None; coerce to empty string so
            # the downstream LLM API (which requires string content) is happy.
            if result is None:
                result = ""

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
                "parallel_enabled": len(response.tool_calls) > 1
                and settings.parallel_exec,
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
            for i, item in enumerate(response.tool_calls):
                tool_results[i] = self._tool_call(tools, item, inject_model_str)
                tool_message, mm_parts = tool_results[i]
                self.context.append(tool_message)
                if mm_parts:
                    multimodal_parts.extend(mm_parts)

        # Loop detection: record each tool-call signature and check for loops.
        # Signatures are computed from the original tool_calls and their
        # results (available in tool_results, populated by both paths above).
        loop_triggered = False
        if self.loop_detector.enabled:
            for idx, item in enumerate(response.tool_calls):
                result_str = ""
                if tool_results[idx] is not None:
                    result_str = str(tool_results[idx][0].get("content", ""))
                sig = compute_signature(
                    item.function.name, item.function.arguments, result_str
                )
                if self.loop_detector.record(sig):
                    loop_triggered = True
                    break

        if loop_triggered:
            warning = (
                "You appear to be in a loop - you've called a tool with "
                "the same arguments and received the same result too many "
                "times. Try a different approach."
            )
            self.context.append({"role": ROLE_USER, "content": warning})
            self._log("[yellow]Loop detected — stopping turn[/yellow]")
            return len(self.context) != osz, []

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
