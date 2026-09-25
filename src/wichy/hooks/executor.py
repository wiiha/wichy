"""
Hook executor for the Wichy hooks system.

This module provides the HookExecutor class that runs hooks with proper error handling
and the HookExecutionResult dataclass for capturing execution results.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from wichy.console import user_console

from .context import HookContext
from .registry import hook_registry
from .result import HookAction, HookResult
from .types import HookType


@dataclass
class HookExecutionResult:
    """Result of running all hooks for a tool execution.

    This dataclass captures the outcome of executing a set of hooks,
    including whether execution was approved, any modifications made,
    and execution details.

    Attributes:
        approved: False if any hook denied execution
        modified_input: Merged modified arguments (for pre-tool hooks)
        modified_output: Final modified output (for post-tool hooks)
        error_message: Error message if execution was denied
        hooks_executed: Names of hooks that ran
        hooks_denied: Names of hooks that denied
        total_time_ms: Total execution time in milliseconds
    """

    approved: bool = True
    modified_input: Optional[Dict[str, Any]] = None
    # Post-tool hooks produce strings; POST_SLASH_COMMAND hooks may replace a
    # Rich Table result, so Any covers both without changing existing uses.
    modified_output: Optional[Any] = None
    error_message: Optional[str] = None
    hooks_executed: List[str] = field(default_factory=list)
    hooks_denied: List[str] = field(default_factory=list)
    total_time_ms: float = 0.0


class HookExecutor:
    """Executor for running hooks with proper error handling.

    This class provides static methods for building hook contexts and
    executing pre-tool and post-tool hooks.
    """

    @staticmethod
    def build_context(
        tool_instance: Any,
        tool_name: str,
        input_args: Dict[str, Any],
        output: Optional[str] = None,
        error: Optional[Exception] = None,
        killed: bool = False,
    ) -> HookContext:
        """Build a HookContext with all fields populated.

        Args:
            tool_instance: The tool instance being executed
            tool_name: Tool name (e.g., "bash")
            input_args: Validated input arguments
            output: Tool output/result (for post-tool hooks)
            error: Exception if tool failed
            killed: True if the call was force-stopped by the user

        Returns:
            A fully populated HookContext
        """
        return HookContext(
            tool_name=tool_name,
            tool_instance=tool_instance,
            input_args=input_args.copy(),
            raw_input_args=input_args.copy(),
            execution_id=hook_registry.generate_execution_id(),
            timestamp=datetime.now(),
            working_directory=Path(os.getcwd()),
            environment=dict(os.environ),
            output=output,
            error=error,
            killed=killed,
        )

    @staticmethod
    def run_pre_hooks(
        tool_instance: Any,
        tool_name: str,
        input_args: Dict[str, Any],
    ) -> HookExecutionResult:
        """Run all pre-tool hooks for a tool execution.

        Args:
            tool_instance: The tool instance being executed
            tool_name: Tool name (e.g., "bash")
            input_args: Validated input arguments

        Returns:
            HookExecutionResult with approval status and any modifications
        """
        result = HookExecutionResult()
        start_time = time.perf_counter()

        # Get hooks from registry (already sorted by priority)
        hooks = hook_registry.get_hooks(HookType.PRE_TOOL, tool_name)

        # Build context
        context = HookExecutor.build_context(
            tool_instance=tool_instance,
            tool_name=tool_name,
            input_args=input_args,
        )

        # Track modified input across hooks
        modified_input: Dict[str, Any] = input_args.copy()

        # Execute each hook
        for hook in hooks:
            # Skip disabled hooks
            if not hook.enabled:
                continue

            try:
                # Update context with current modified input
                context.input_args = modified_input.copy()

                # Execute hook
                hook_start = time.perf_counter()
                hook_result: HookResult = hook.function(context)
                hook_result.execution_time_ms = (
                    time.perf_counter() - hook_start
                ) * 1000

                # Track execution
                result.hooks_executed.append(hook.name)

                # Handle action
                if hook_result.action == HookAction.APPROVE:
                    continue
                elif hook_result.action == HookAction.DENY:
                    result.approved = False
                    result.error_message = hook_result.error_message
                    result.hooks_denied.append(hook.name)
                    break
                elif hook_result.action == HookAction.MODIFY_INPUT:
                    if hook_result.modified_input is not None:
                        modified_input.update(hook_result.modified_input)
                        result.modified_input = modified_input.copy()
                elif hook_result.action == HookAction.LOG:
                    # Log only, no effect on flow
                    pass

            except Exception as e:
                # Log exception and continue
                user_console.print(f"[red]Hook {hook.name} failed: {e}[/red]")
                continue

        # Calculate total time
        result.total_time_ms = (time.perf_counter() - start_time) * 1000

        return result

    @staticmethod
    def run_context_hooks(
        hook_type: HookType,
        root_agent: Any,
        context_handler: Any = None,
        summary: Optional[str] = None,
        is_auto_compact: bool = False,
        reset_strategy: Optional[str] = None,
        message: Optional[Any] = None,
        response_content: Optional[Any] = None,
        response_reasoning: Optional[str] = None,
        usage: Optional[Dict[str, Any]] = None,
    ) -> HookExecutionResult:
        """Run lifecycle hooks for session and context events.

        This method executes hooks for lifecycle events (session start/end,
        context reset/compact pre/post, pre-user-message, pre-response-to-user).
        Most lifecycle hooks are informational only - they cannot deny or modify
        the operation. PRE_RESPONSE_TO_USER is the exception: hooks of that type
        may return HookResult.modify_output(new_content) to replace the response
        sent to the user.

        Args:
            hook_type: The type of hook (SESSION_START, SESSION_END,
                        CONTEXT_RESET_PRE, CONTEXT_RESET_POST,
                        CONTEXT_COMPACT_PRE, CONTEXT_COMPACT_POST,
                        PRE_USER_MESSAGE, PRE_RESPONSE_TO_USER)
            root_agent: The root agent instance
            context_handler: The context handler instance (None for session hooks)
            summary: For CONTEXT_COMPACT_POST, the generated summary
            is_auto_compact: For compact hooks, whether this is auto-initiated
            reset_strategy: For reset hooks, the strategy being used ("nuke" or "summary")
            message: For PRE_USER_MESSAGE, the raw user message
            response_content: For PRE_RESPONSE_TO_USER, the assistant response content
            response_reasoning: For PRE_RESPONSE_TO_USER, optional reasoning content
            usage: For PRE_RESPONSE_TO_USER, LLM usage metadata

        Returns:
            HookExecutionResult with execution details. For PRE_RESPONSE_TO_USER,
            modified_output may contain the final modified response content.
        """
        result = HookExecutionResult()
        start_time = time.perf_counter()

        # Get hooks for this lifecycle event, use get_hooks_for_type since
        # lifecycle hooks are always registered with tool_name=None
        hooks = hook_registry.get_hooks_for_type(hook_type)

        if not hooks:
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # Build event_data with all relevant context for lifecycle hooks
        # Hooks receive data via event_data, not via tool_instance or input_args
        event_data: Dict[str, Any] = {
            "root_agent": root_agent,
        }
        if context_handler is not None:
            event_data["context_handler"] = context_handler

        # Add hook-type-specific data
        # SESSION_START/END need no additional data beyond base dict
        if hook_type == HookType.CONTEXT_COMPACT_POST:
            event_data["summary"] = summary
            event_data["is_auto_compact"] = is_auto_compact
        elif hook_type == HookType.CONTEXT_COMPACT_PRE:
            event_data["is_auto_compact"] = is_auto_compact
        elif hook_type in (HookType.CONTEXT_RESET_PRE, HookType.CONTEXT_RESET_POST):
            event_data["reset_strategy"] = reset_strategy
        elif hook_type == HookType.PRE_USER_MESSAGE:
            event_data["message"] = message
        elif hook_type == HookType.PRE_RESPONSE_TO_USER:
            event_data["response_content"] = response_content
            event_data["response_reasoning"] = response_reasoning
            event_data["usage"] = usage

        # Build context for lifecycle hook
        # - tool_name is None to indicate this is a lifecycle event, not a tool call
        # - tool_instance is None; lifecycle objects go in event_data
        # - input_args/raw_input_args are empty; lifecycle data goes in event_data
        # - output is set to response_content for PRE_RESPONSE_TO_USER so hooks can
        #   see and cumulatively modify it via ctx.output
        initial_output = None
        if hook_type == HookType.CONTEXT_COMPACT_POST:
            initial_output = summary
        elif hook_type == HookType.PRE_RESPONSE_TO_USER:
            initial_output = response_content

        hook_ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id=hook_registry.generate_execution_id(),
            timestamp=datetime.now(),
            working_directory=Path(os.getcwd()),
            environment={},
            output=initial_output,
            hook_type=hook_type,
            event_data=event_data,
        )

        # Execute each hook in priority order
        for hook in hooks:
            if not hook.enabled:
                continue

            try:
                # Execute the hook and capture the result
                hook_result = hook.function(hook_ctx)

                # Track execution
                result.hooks_executed.append(hook.name)

                # PRE_RESPONSE_TO_USER hooks may modify the response content.
                # All other lifecycle hooks are informational; their returns are ignored.
                if (
                    hook_type == HookType.PRE_RESPONSE_TO_USER
                    and hook_result is not None
                ):
                    if hook_result.action == HookAction.MODIFY_OUTPUT:
                        result.modified_output = hook_result.modified_output
                        hook_ctx.output = hook_result.modified_output

            except Exception as e:
                # Log exception and continue
                user_console.print(f"[red]Hook {hook.name} failed: {e}[/red]")
                continue

        # Calculate total time
        result.total_time_ms = (time.perf_counter() - start_time) * 1000

        return result

    @staticmethod
    def run_slash_hooks(
        hook_type: HookType,
        root_agent: Any,
        command: str,
        args: str,
        line: str,
        source: Optional[str] = None,
        result: Optional[Any] = None,
    ) -> HookExecutionResult:
        """Run slash command hooks (PRE, custom command, or POST).

        One executor path serves all three slash hook families; the
        differences are in how the caller consumes the HookExecutionResult:

        - PRE_SLASH_COMMAND: honors DENY (approved=False, error_message set,
          remaining hooks skipped) and MODIFY_INPUT restricted to the
          "args" key -- any other key is ignored, so the command token can
          never be rewritten this way.
        - SLASH_COMMAND (custom commands): chains HookResult.modify_output
          through ctx.output exactly like PRE_RESPONSE_TO_USER; a control
          exception (context reset/drop, btw, EOF) raised by a hook
          PROPAGATES OUT so the caller's existing except-clauses act on it;
          any other exception is isolated per hook and the run continues.
        - POST_SLASH_COMMAND: same output chaining starting from the
          command's own result (str, Rich Table, or None).

        Args:
            hook_type: PRE_SLASH_COMMAND, SLASH_COMMAND, or POST_SLASH_COMMAND.
            root_agent: The root agent (may be None in tests/tooling).
            command: The normalized command token (e.g. "/deploy").
            args: The argument text after the command token ("" if none).
            line: The full, possibly pre-hook-rewritten, slash line.
            source: For PRE/POST: "builtin" | "hook" | "unknown" (None for
                the SLASH_COMMAND family itself).
            result: For POST hooks: what the command produced.

        Returns:
            HookExecutionResult. For PRE: approved=False means a hook denied
            (error_message carries the reason); modified_input may hold
            {"args": new_args}. For command/POST: modified_output carries the
            final chained output when any hook modified it, else None.
        """
        from .slash_exceptions import (
            SlashBtwException,
            SlashContextDropException,
            SlashContextResetException,
        )

        execution_result = HookExecutionResult()
        start_time = time.perf_counter()

        # Command-specific hooks merge with wildcards (None), priority order.
        hooks = hook_registry.get_hooks(hook_type, command)

        event_data: Dict[str, Any] = {
            "root_agent": root_agent,
            "command": command,
            "args": args,
            "line": line,
        }
        if source is not None:
            event_data["source"] = source
        if hook_type == HookType.POST_SLASH_COMMAND:
            event_data["result"] = result

        hook_ctx = HookContext(
            tool_name=None,
            tool_instance=None,
            input_args={},
            raw_input_args={},
            execution_id=hook_registry.generate_execution_id(),
            timestamp=datetime.now(),
            working_directory=Path(os.getcwd()),
            environment={},
            output=result,
            hook_type=hook_type,
            event_data=event_data,
        )

        # POST hooks chain modifications starting from the command's own
        # result; PRE/custom hooks start from nothing.
        chained_output: Optional[Any] = (
            result if hook_type == HookType.POST_SLASH_COMMAND else None
        )

        for hook in hooks:
            if not hook.enabled:
                continue

            try:
                hook_start = time.perf_counter()
                hook_result: HookResult = hook.function(hook_ctx)
                hook_result.execution_time_ms = (
                    time.perf_counter() - hook_start
                ) * 1000

                execution_result.hooks_executed.append(hook.name)

                if hook_result is None:
                    continue

                if hook_result.action == HookAction.DENY:
                    # PRE: block dispatch. For custom/POST the same result
                    # records a blocked message for the caller to show.
                    execution_result.approved = False
                    execution_result.error_message = (
                        hook_result.error_message or f"Denied by hook {hook.name}"
                    )
                    execution_result.hooks_denied.append(hook.name)
                    break
                elif hook_result.action == HookAction.MODIFY_INPUT:
                    # Only the "args" key is honored; the command token is
                    # never rewritable through modify_input.
                    if (
                        hook_type == HookType.PRE_SLASH_COMMAND
                        and hook_result.modified_input is not None
                        and "args" in hook_result.modified_input
                    ):
                        new_args = hook_result.modified_input["args"]
                        if isinstance(new_args, str):
                            execution_result.modified_input = {"args": new_args}
                            hook_ctx.event_data["args"] = new_args
                elif hook_result.action == HookAction.MODIFY_OUTPUT:
                    if hook_result.modified_output is not None:
                        chained_output = hook_result.modified_output
                        execution_result.modified_output = chained_output
                        hook_ctx.output = chained_output

            except (
                SlashContextResetException,
                SlashContextDropException,
                SlashBtwException,
                EOFError,
            ):
                # Control exceptions propagate to the caller (REPL or web
                # chat) for handling, exactly like built-in commands'.
                raise
            except Exception as e:
                # Any other hook failure is isolated and non-fatal.
                user_console.print(f"[red]Hook {hook.name} failed: {e}[/red]")
                continue

        execution_result.total_time_ms = (time.perf_counter() - start_time) * 1000
        return execution_result

    @staticmethod
    def run_post_hooks(
        tool_instance: Any,
        tool_name: str,
        input_args: Dict[str, Any],
        output: str,
        error: Optional[Exception] = None,
        killed: bool = False,
    ) -> HookExecutionResult:
        """Run all post-tool hooks for a tool execution.

        Args:
            tool_instance: The tool instance being executed
            tool_name: Tool name (e.g., "bash")
            input_args: Validated input arguments
            output: Tool output/result
            error: Exception if tool failed
            killed: True if the call was force-stopped by the user
                (hooks see the kill notice as output and can tell it
                apart from a normal success)

        Returns:
            HookExecutionResult with approval status and any modifications
        """
        result = HookExecutionResult()
        start_time = time.perf_counter()

        # Get hooks from registry (already sorted by priority)
        hooks = hook_registry.get_hooks(HookType.POST_TOOL, tool_name)

        # Build context
        context = HookExecutor.build_context(
            tool_instance=tool_instance,
            tool_name=tool_name,
            input_args=input_args,
            output=output,
            error=error,
            killed=killed,
        )

        # Track modified output across hooks
        modified_output: str = output

        # Execute each hook
        for hook in hooks:
            # Skip disabled hooks
            if not hook.enabled:
                continue

            try:
                # Update context with current modified output
                context.output = modified_output

                # Execute hook
                hook_start = time.perf_counter()
                hook_result: HookResult = hook.function(context)
                hook_result.execution_time_ms = (
                    time.perf_counter() - hook_start
                ) * 1000

                # Track execution
                result.hooks_executed.append(hook.name)

                # Handle action
                if hook_result.action == HookAction.APPROVE:
                    continue
                elif hook_result.action == HookAction.DENY:
                    result.approved = False
                    result.error_message = hook_result.error_message
                    result.hooks_denied.append(hook.name)
                    # For post-tool, set modified_output to error message
                    result.modified_output = result.error_message
                    break
                elif hook_result.action == HookAction.MODIFY_OUTPUT:
                    if hook_result.modified_output is not None:
                        modified_output = hook_result.modified_output
                        result.modified_output = modified_output
                elif hook_result.action == HookAction.LOG:
                    # Log only, no effect on flow
                    pass

            except Exception as e:
                # Log exception and continue
                user_console.print(f"[red]Hook {hook.name} failed: {e}[/red]")
                continue

        # Calculate total time
        result.total_time_ms = (time.perf_counter() - start_time) * 1000

        return result
