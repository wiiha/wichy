# Default hooks template
DEFAULT_HOOKS_TEMPLATE = '''"""
Wichy Hooks - Customize tool execution.

This file is automatically loaded when Wichy starts. Define hooks
to intercept and modify tool execution.

Hooks run in priority order (lower = earlier):
    EARLY = 10, NORMAL = 50 (default), LATE = 90

Available decorators:
    @pre_tool(tool_name)  - Runs before tool execution
    @post_tool(tool_name) - Runs after tool execution

Use None or () as tool_name to apply to all tools.
"""

from wichy.hooks import pre_tool, post_tool, HookResult, HookContext, print

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
# YOUR HOOKS
# =============================================================================

# Add your custom hooks here
'''