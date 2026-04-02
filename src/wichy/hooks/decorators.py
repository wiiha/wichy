"""
Decorator functions for the Wichy hooks system.

This module provides convenient decorators for registering hooks.
Use these decorators to define pre-tool and post-tool hooks with
a clean, declarative syntax.

Usage:
    from wichy.hooks.decorators import pre_tool, post_tool

    @pre_tool("bash")
    def check_bash_command(ctx: HookContext) -> HookResult:
        if "rm -rf" in ctx.input_args.get("command", ""):
            return HookResult.deny("Destructive command not allowed")
        return HookResult.approve()

    @post_tool("read_file")
    def log_file_reads(ctx: HookContext) -> HookResult:
        print(f"Read file, {len(ctx.output)} characters")
        return HookResult.approve()
"""

from typing import Callable, Optional

from .registry import hook_registry
from .types import HookType


def pre_tool(
    tool_name: Optional[str] = None,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a pre-tool hook.

    Pre-tool hooks are executed before a tool runs. They can approve,
    deny, or modify the tool's input arguments.

    Args:
        tool_name: Tool to hook (e.g., "bash", "write_file"). None hooks all tools.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @pre_tool("bash")
        def check_bash_command(ctx: HookContext) -> HookResult:
            if "rm -rf" in ctx.input_args.get("command", ""):
                return HookResult.deny("Destructive command not allowed")
            return HookResult.approve()

        @pre_tool()  # All tools
        def log_all_calls(ctx: HookContext) -> HookResult:
            print(f"Tool called: {ctx.tool_name}")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a pre-tool hook."""
        hook_registry.register(
            hook_type=HookType.PRE_TOOL,
            tool_name=tool_name,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    return decorator


def post_tool(
    tool_name: Optional[str] = None,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a post-tool hook.

    Post-tool hooks are executed after a tool runs. They can approve,
    deny, or modify the tool's output.

    Args:
        tool_name: Tool to hook. None hooks all tools.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook.

    Returns:
        Decorator function.

    Example:
        @post_tool("read_file")
        def log_file_reads(ctx: HookContext) -> HookResult:
            print(f"Read file, {len(ctx.output)} characters")
            return HookResult.approve()

        @post_tool("bash")
        def sanitize_bash_output(ctx: HookContext) -> HookResult:
            import os
            output = ctx.output.replace(os.environ.get("API_KEY", ""), "[REDACTED]")
            return HookResult.modify_output(output)
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a post-tool hook."""
        hook_registry.register(
            hook_type=HookType.POST_TOOL,
            tool_name=tool_name,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    return decorator
