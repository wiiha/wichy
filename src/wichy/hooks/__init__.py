"""
Wichy hooks system.

This package provides hook functionality for intercepting and modifying
tool execution in the Wichy agent system.

Example usage:

    from wichy.hooks import pre_tool, post_tool, HookResult, HookContext

    @pre_tool("bash")
    def block_dangerous(ctx: HookContext) -> HookResult:
        if "rm -rf" in ctx.input_args.get("command", ""):
            return HookResult.deny("Destructive command not allowed")
        return HookResult.approve()

    @post_tool("read_file")
    def redact_secrets(ctx: HookContext) -> HookResult:
        output = ctx.output.replace("secret", "[REDACTED]")
        return HookResult.modify_output(output)

Lifecycle hooks for session and context events:

    from wichy.hooks import session_start, session_end, context_reset_pre

    @session_start
    def on_session_start(ctx: HookContext) -> HookResult:
        print("Session started")
        return HookResult.approve()

    @context_compact_pre
    def before_compact(ctx: HookContext) -> HookResult:
        print("Context will be compacted")
        return HookResult.approve()
"""

# Console
from wichy.console import user_console
from wichy.hooks.context import HookContext

# Context access
from wichy.hooks.context_access import context_add, set_active_context

# Decorators (main user-facing API)
from wichy.hooks.decorators import (
    context_compact_post,
    context_compact_pre,
    context_reset_post,
    context_reset_pre,
    post_tool,
    pre_response_to_user,
    pre_tool,
    pre_user_message,
    session_end,
    session_start,
)

# Default hook template
from wichy.hooks.default import DEFAULT_HOOKS_TEMPLATE

# Executor
from wichy.hooks.executor import HookExecutionResult, HookExecutor

# Loader
from wichy.hooks.loader import HookLoader, hook_loader, initialize_hooks

# Registry
from wichy.hooks.registry import (
    HookRegistry,
    clear_hooks,
    get_hooks_for_tool,
    get_hooks_for_type,
    hook_registry,
    register_hook,
)

# Data classes
from wichy.hooks.result import HookAction, HookResult

# Types
from wichy.hooks.types import HookPriority, HookType, RegisteredHook


def print(message: str = "", **kwargs) -> None:
    """Print a message to the user console with Rich formatting support.

    This is a convenience wrapper around user_console.print() for use in hooks.
    Supports all Rich markup tags like [yellow], [bold], [green], etc.

    Args:
        message: The message to print (can include Rich markup)
        **kwargs: Additional arguments passed to Rich's print (style, highlight, etc.)

    Example:
        from wichy.hooks import print
        print("[yellow]Warning: something wrong[/yellow]")
        print("[bold green]Success![/bold green]")
    """
    user_console.print(message, **kwargs)


__all__ = [
    # Decorators
    "pre_tool",
    "post_tool",
    "session_start",
    "session_end",
    "context_reset_pre",
    "context_reset_post",
    "context_compact_pre",
    "context_compact_post",
    "pre_user_message",
    "pre_response_to_user",
    # Data classes
    "HookAction",
    "HookResult",
    "HookContext",
    "HookExecutionResult",
    # Types
    "HookType",
    "HookPriority",
    "RegisteredHook",
    # Registry
    "HookRegistry",
    "hook_registry",
    "register_hook",
    "get_hooks_for_tool",
    "get_hooks_for_type",
    "clear_hooks",
    # Executor
    "HookExecutor",
    # Loader
    "HookLoader",
    "initialize_hooks",
    "hook_loader",
    # Console
    "print",
    # Context access
    "context_add",
    "set_active_context",
    # Default hook template
    "DEFAULT_HOOKS_TEMPLATE",
]
