# Default hooks template
DEFAULT_HOOKS_TEMPLATE = '''"""
Wichy Hooks - Customize tool execution and observe lifecycle events.

This file is automatically loaded when Wichy starts. Define hooks
to intercept and modify tool execution, or observe session lifecycle.

Hooks run in priority order (lower = earlier):
    EARLY = 10, NORMAL = 50 (default), LATE = 90

Tool Hooks (intercept tool execution):
    @pre_tool(tool_name)  - Runs before tool execution
    @post_tool(tool_name) - Runs after tool execution

    Use None or () as tool_name to apply to all tools.
    HookResult can be: approve(), deny(), modify_input(), modify_output()

Lifecycle Hooks (observe session and context events):
    @session_start        - Fires when a wichy session starts
    @session_end          - Fires when a wichy session ends
    @context_reset_pre    - Fires before context is reset
    @context_reset_post   - Fires after context is reset
    @context_compact_pre  - Fires before context compaction
    @context_compact_post - Fires after context compaction

    Lifecycle hooks are informational - they observe events but cannot
    block or modify them. Data is available in ctx.event_data dict.

    event_data contains:
        - session_start/end: {"root_agent": ...}
        - context_reset_pre/post: {"root_agent", "context_handler", "reset_strategy"}
        - context_compact_pre: {"root_agent", "context_handler", "is_auto_compact"}
        - context_compact_post: {"root_agent", "context_handler", "is_auto_compact", "summary"}
"""

from wichy.hooks import (
    # Tool hooks
    pre_tool, post_tool,
    # Lifecycle hooks
    session_start, session_end,
    context_reset_pre, context_reset_post,
    context_compact_pre, context_compact_post,
    # Types
    HookResult, HookContext, print
)

# =============================================================================
# SAFETY HOOK (uncomment to enable)
# =============================================================================
# Blocks dangerous bash commands to prevent accidental system damage.
# This hook runs with priority 10 (EARLY) to intercept commands before other
# hooks can process them.

# @pre_tool("bash", priority=10)
# def block_dangerous_commands(ctx: HookContext) -> HookResult:
#     """Block dangerous bash commands that could cause system damage."""
#     command = ctx.input_args.get("command", "")
#
#     dangerous_patterns = [
#         ("rm -rf /", "root deletion"),
#         ("rm -rf /*", "root deletion"),
#         ("mkfs", "filesystem format"),
#         ("dd if=/dev/zero of=/dev/", "disk overwrite"),
#         ("> /dev/sda", "disk overwrite"),
#         ("> /dev/hda", "disk overwrite"),
#         (":(){ :|:& };:", "fork bomb"),
#         ("chmod -R 777 /", "dangerous permissions"),
#         ("chown -R", "recursive ownership change"),
#     ]
#
#     for pattern, description in dangerous_patterns:
#         if pattern in command:
#             print(f"[red][SAFETY] Blocked dangerous command ({description})[/red]")
#             return HookResult.deny(f"Blocked dangerous command: {description}")
#
#     return HookResult.approve()

# =============================================================================
# EXAMPLE HOOKS (uncomment to use)
# =============================================================================

# @pre_tool("bash")
# def log_bash_commands(ctx: HookContext) -> HookResult:
#     """Log all bash commands."""
#     print(f"[dim]Bash: {ctx.input_args.get(\'command\', \'\')[:100]}[/dim]")
#     return HookResult.approve()

# @post_tool("read_file")
# def truncate_large_files(ctx: HookContext) -> HookResult:
#     """Truncate large file outputs."""
#     max_chars = 50000
#     if ctx.output and len(ctx.output) > max_chars:
#         print(f"[yellow]Output truncated to {max_chars} chars[/yellow]")
#         return HookResult.modify_output(ctx.output[:max_chars] + "\\n...[truncated]")
#     return HookResult.approve()

# =============================================================================
# LIFECYCLE HOOK EXAMPLES (uncomment to use)
# =============================================================================

# @session_start
# def on_session_start(ctx: HookContext) -> HookResult:
#     """Log when a wichy session starts."""
#     print(f"[green]Session started in: {ctx.working_directory}[/green]")
#     return HookResult.approve()

# @context_compact_pre
# def before_compact(ctx: HookContext) -> HookResult:
#     """Log before context compaction."""
#     is_auto = ctx.event_data.get("is_auto_compact", False)
#     print(f"[yellow]Context compacting... (auto={is_auto})[/yellow]")
#     return HookResult.approve()

# @context_compact_post
# def after_compact(ctx: HookContext) -> HookResult:
#     """Log after context compaction, show summary."""
#     summary = ctx.event_data.get("summary", "")[:200]
#     print(f"[dim]Compaction complete. Summary: {summary}...[/dim]")
#     return HookResult.approve()

# =============================================================================
# YOUR HOOKS
# =============================================================================

# Add your custom hooks here
'''
