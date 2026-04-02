# Default hooks template
DEFAULT_HOOKS_TEMPLATE = '''"""
Wichy Hooks - Customize tool execution.

This file is automatically loaded when Wichy starts. Define hooks
to intercept and modify tool execution.

Hooks run in priority order (lower = earlier):
- EARLY = 10
- NORMAL = 50 (default)
- LATE = 90

Available decorators:
    @pre_tool(tool_name)  - Runs before tool execution
    @post_tool(tool_name) - Runs after tool execution

Use None or () as tool_name to apply to all tools.

Example:
    @pre_tool("bash")
    def check_dangerous_commands(ctx: HookContext) -> HookResult:
        if "rm -rf" in ctx.input_args.get("command", ""):
            return HookResult.deny("Destructive command blocked")
        return HookResult.approve()
"""

from wichy.hooks import pre_tool, post_tool, HookResult, HookContext, print

# =============================================================================
# DEFAULT HOOKS (uncomment to enable)
# =============================================================================
# Logs all tool calls to .wichy/logs/hooks.log for debugging and audit.
# Logs tool name, timestamp, and key parameters while filtering
# sensitive information (passwords, API keys, tokens, secrets).
#
# To enable: Remove the '#' before @pre_tool to activate this hook.

# @pre_tool(priority=90)  # LATE priority - runs after other pre-hooks
# def log_tool_usage(ctx: HookContext) -> HookResult:
#     """Log all tool calls to .wichy/logs/hooks.log for debugging and audit.
#
#     Logs tool name, timestamp, and key parameters while filtering
#     sensitive information (passwords, API keys, tokens, secrets).
#     """
#     from datetime import datetime
#     from pathlib import Path
#
#     # Get log file path
#     log_dir = Path(".wichy/logs")
#     log_file = log_dir / "hooks.log"
#
#     # Ensure log directory exists
#     log_dir.mkdir(parents=True, exist_ok=True)
#
#     # Get timestamp
#     timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#
#     # Get tool name
#     tool_name = ctx.tool_name or "unknown"
#
#     # Extract key parameters based on tool type
#     log_parts = []
#
#     # Parameters that should not be logged (sensitive)
#     sensitive_keys = {
#         "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
#         "authorization", "auth", "credential", "private_key", "privatekey",
#         "access_token", "refresh_token", "session_key", "session_id"
#     }
#
#     # Tool-specific logging
#     if tool_name == "bash":
#         command = ctx.input_args.get("command", "")
#         timeout = ctx.input_args.get("timeout")
#         log_parts.append(f'command="{command}"')
#         if timeout is not None:
#             log_parts.append(f"timeout={timeout}")
#
#     elif tool_name in ("read_file", "write_file", "insert_lines", "replace_text"):
#         path = ctx.input_args.get("path", "")
#         log_parts.append(f'path="{path}"')
#         limit = ctx.input_args.get("limit")
#         if limit is not None:
#             log_parts.append(f"limit={limit}")
#         offset = ctx.input_args.get("offset")
#         if offset is not None:
#             log_parts.append(f"offset={offset}")
#
#     elif tool_name in ("web_fetch", "web_search"):
#         url = ctx.input_args.get("url", "")
#         query = ctx.input_args.get("query", "")
#         if url:
#             log_parts.append(f'url="{url}"')
#         if query:
#             log_parts.append(f'query="{query}"')
#         max_results = ctx.input_args.get("max_results")
#         if max_results is not None:
#             log_parts.append(f"max_results={max_results}")
#
#     elif tool_name == "ask_user_question":
#         # Log that we're asking a question, but not the questions themselves (could have sensitive data)
#         num_questions = len(ctx.input_args.get("questions", []))
#         log_parts.append(f"questions_count={num_questions}")
#
#     elif tool_name in ("glob", "search_in_files"):
#         pattern = ctx.input_args.get("pattern", "")
#         path = ctx.input_args.get("path", "")
#         log_parts.append(f'pattern="{pattern}"')
#         if path:
#             log_parts.append(f'path="{path}"')
#         output_mode = ctx.input_args.get("output_mode")
#         if output_mode:
#             log_parts.append(f"output_mode={output_mode}")
#
#     elif tool_name == "list_files":
#         path = ctx.input_args.get("path", "")
#         if path:
#             log_parts.append(f'path="{path}"')
#
#     elif tool_name == "todo":
#         action = ctx.input_args.get("action", "")
#         log_parts.append(f"action={action}")
#         task_name = ctx.input_args.get("task_name")
#         if task_name:
#             # Truncate long task names
#             task_name_display = task_name[:50] + "..." if len(task_name) > 50 else task_name
#             log_parts.append(f'task_name="{task_name_display}"')
#
#     else:
#         # For other tools, log non-sensitive parameters
#         for key, value in ctx.input_args.items():
#             # Skip sensitive keys
#             if key.lower() in sensitive_keys:
#                 log_parts.append(f"{key}=[REDACTED]")
#                 continue
#             # Skip very long values
#             if isinstance(value, str) and len(value) > 200:
#                 log_parts.append(f'{key}="{value[:200]}..."')
#                 continue
#             # Skip None values
#             if value is None:
#                 continue
#             # Format value
#             if isinstance(value, str):
#                 log_parts.append(f'{key}="{value}"')
#             else:
#                 log_parts.append(f"{key}={value}")
#
#     # Build log line
#     params_str = " | ".join(log_parts)
#     log_line = f"{timestamp} | {tool_name} | {params_str}\\n"
#
#     # Write to log file
#     try:
#         with open(log_file, "a", encoding="utf-8") as f:
#             f.write(log_line)
#     except Exception as e:
#         # Don't fail the hook if logging fails
#         print(f"[yellow]Warning: Could not write to hook log: {e}[/yellow]")
#
#     return HookResult.approve()

# =============================================================================
# SAFETY HOOK (uncomment to enable)
# =============================================================================
# Blocks dangerous bash commands to prevent accidental system damage.
# This hook runs with priority 10 (EARLY) to intercept commands before other
# hooks can process them.
#
# To enable: Remove the '#' before @pre_tool to activate this hook.

# @pre_tool("bash", priority=10)  # Run early
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
