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
