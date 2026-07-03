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

Lifecycle hooks for session and context events:

    from wichy.hooks.decorators import session_start, session_end

    @session_start
    def on_session_start(ctx: HookContext) -> HookResult:
        print("Session started")
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


# =============================================================================
# Lifecycle Hook Decorators
# =============================================================================


def session_start(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a session start hook.

    Session start hooks are executed when a wichy session starts.
    They can be used for initialization, logging, or setup tasks.

    Can be used as a bare decorator or with arguments:
        @session_start
        def my_hook(ctx): ...

        @session_start(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @session_start
        def on_session_start(ctx: HookContext) -> HookResult:
            print("Session started")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a session start hook."""
        hook_registry.register(
            hook_type=HookType.SESSION_START,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @session_start
        return decorator(func_or_priority)
    return decorator


def session_end(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a session end hook.

    Session end hooks are executed when a wichy session ends.
    They can be used for cleanup, logging, or finalization tasks.

    Can be used as a bare decorator or with arguments:
        @session_end
        def my_hook(ctx): ...

        @session_end(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @session_end
        def on_session_end(ctx: HookContext) -> HookResult:
            print("Session ended")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a session end hook."""
        hook_registry.register(
            hook_type=HookType.SESSION_END,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @session_end
        return decorator(func_or_priority)
    return decorator


def context_reset_pre(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a pre-context reset hook.

    Pre-context reset hooks are executed before context is reset.
    They can be used for logging, state preservation, or validation.

    Can be used as a bare decorator or with arguments:
        @context_reset_pre
        def my_hook(ctx): ...

        @context_reset_pre(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @context_reset_pre
        def before_context_reset(ctx: HookContext) -> HookResult:
            print("Context will be reset")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a pre-context reset hook."""
        hook_registry.register(
            hook_type=HookType.CONTEXT_RESET_PRE,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @context_reset_pre
        return decorator(func_or_priority)
    return decorator


def context_reset_post(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a post-context reset hook.

    Post-context reset hooks are executed after context is reset.
    They can be used for reinitialization or logging.

    Can be used as a bare decorator or with arguments:
        @context_reset_post
        def my_hook(ctx): ...

        @context_reset_post(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @context_reset_post
        def after_context_reset(ctx: HookContext) -> HookResult:
            print("Context has been reset")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a post-context reset hook."""
        hook_registry.register(
            hook_type=HookType.CONTEXT_RESET_POST,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @context_reset_post
        return decorator(func_or_priority)
    return decorator


def context_compact_pre(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a pre-context compact hook.

    Pre-context compact hooks are executed before context compaction.
    They can be used for logging or state preservation before memory
    optimization.

    Can be used as a bare decorator or with arguments:
        @context_compact_pre
        def my_hook(ctx): ...

        @context_compact_pre(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @context_compact_pre
        def before_compact(ctx: HookContext) -> HookResult:
            print("Context will be compacted")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a pre-context compact hook."""
        hook_registry.register(
            hook_type=HookType.CONTEXT_COMPACT_PRE,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @context_compact_pre
        return decorator(func_or_priority)
    return decorator


def context_compact_post(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a post-context compact hook.

    Post-context compact hooks are executed after context compaction.
    They can be used for logging or validation after memory optimization.

    Can be used as a bare decorator or with arguments:
        @context_compact_post
        def my_hook(ctx): ...

        @context_compact_post(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @context_compact_post
        def after_compact(ctx: HookContext) -> HookResult:
            print("Context has been compacted")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a post-context compact hook."""
        hook_registry.register(
            hook_type=HookType.CONTEXT_COMPACT_POST,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @context_compact_post
        return decorator(func_or_priority)
    return decorator


def pre_user_message(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a pre-user-message hook.

    Pre-user-message hooks run after a user message is submitted but before
    it is appended to the root agent's context. They are informational only:
    return values are ignored.

    Can be used as a bare decorator or with arguments:
        @pre_user_message
        def my_hook(ctx): ...

        @pre_user_message(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @pre_user_message
        def on_pre_user_message(ctx: HookContext) -> HookResult:
            print(f"User said: {ctx.event_data.get('message')}")
            return HookResult.approve()
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a pre-user-message hook."""
        hook_registry.register(
            hook_type=HookType.PRE_USER_MESSAGE,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @pre_user_message
        return decorator(func_or_priority)
    return decorator


def pre_response_to_user(
    func_or_priority: Optional[Callable] = None,
    *,
    priority: int = 50,
    name: Optional[str] = None,
) -> Callable:
    """Decorator to register a pre-response-to-user hook.

    Pre-response-to-user hooks run just before the root agent's final
    assistant response is returned to the user. They may return
    ``HookResult.modify_output(new_content)`` to replace the response content.

    Can be used as a bare decorator or with arguments:
        @pre_response_to_user
        def my_hook(ctx): ...

        @pre_response_to_user(priority=10)
        def my_hook(ctx): ...

    Args:
        func_or_priority: Used internally for bare decorator support.
        priority: Execution order (lower = earlier). Default 50.
        name: Optional name for the hook (defaults to function name).

    Returns:
        Decorator function.

    Example:
        @pre_response_to_user
        def on_pre_response(ctx: HookContext) -> HookResult:
            content = ctx.event_data.get("response_content", "")
            return HookResult.modify_output(f"[processed] {content}")
    """

    def decorator(func: Callable) -> Callable:
        """Register the function as a pre-response-to-user hook."""
        hook_registry.register(
            hook_type=HookType.PRE_RESPONSE_TO_USER,
            tool_name=None,
            function=func,
            priority=priority,
            name=name or "",
        )
        return func

    if func_or_priority is not None:
        # Used as bare decorator: @pre_response_to_user
        return decorator(func_or_priority)
    return decorator
