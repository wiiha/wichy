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
    modified_output: Optional[str] = None
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
    ) -> HookContext:
        """Build a HookContext with all fields populated.

        Args:
            tool_instance: The tool instance being executed
            tool_name: Tool name (e.g., "bash")
            input_args: Validated input arguments
            output: Tool output/result (for post-tool hooks)
            error: Exception if tool failed

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
                    if hook_result.modified_input:
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
                if hook_type == HookType.PRE_RESPONSE_TO_USER and hook_result is not None:
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
    def run_post_hooks(
        tool_instance: Any,
        tool_name: str,
        input_args: Dict[str, Any],
        output: str,
        error: Optional[Exception] = None,
    ) -> HookExecutionResult:
        """Run all post-tool hooks for a tool execution.

        Args:
            tool_instance: The tool instance being executed
            tool_name: Tool name (e.g., "bash")
            input_args: Validated input arguments
            output: Tool output/result
            error: Exception if tool failed

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
