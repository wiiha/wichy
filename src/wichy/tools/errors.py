"""
Error handling utilities for wichy tools.

This module provides standardized error formatting for consistent tool outputs.
All tools should return error strings using these helpers, not raise exceptions.

Usage:
    from wichy.tools.errors import format_error, format_error_with_context

    # Simple error:
    def execute(self, path: str) -> str:
        if not os.path.exists(path):
            return format_error(f"file not found: {path}")
        return "success"

    # Error with context:
    def execute(self, path: str) -> str:
        try:
            return do_something(path)
        except PermissionError:
            return format_error_with_context(path, "permission denied")
"""

from typing import Optional


def format_error(message: str) -> str:
    """Format an error message for tool return values.

    Args:
        message: The error description

    Returns:
        Formatted error string: "error: {message}"

    Example:
        >>> format_error("file not found: /path/to/file")
        'error: file not found: /path/to/file'
    """
    return f"error: {message}"


def format_error_with_context(context: str, message: str) -> str:
    """Format an error message with additional context.

    Use this when you have a specific resource (file path, URL, etc.) that
    the error relates to.

    Args:
        context: Context like file path, URL, operation name, etc.

        message: The error description

    Returns:
        Formatted error string: "error: {context}: {message}"

    Example:
        >>> format_error_with_context("/path/to/file", "file not found")
        'error: /path/to/file: file not found'
    """
    return f"error: {context}: {message}"


def format_tool_killed(tool_name: str, reason: Optional[str] = None) -> str:
    """Format the result string returned for a force-killed tool call.

    A kill is not an error: the user stopped the execution mid-flight. The
    string follows the bracket-header convention (like ``[RESULT_OFFLOADED]``)
    so the LLM notices it and reconsiders its approach.

    Args:
        tool_name: Name of the tool that was killed.

        reason: Optional free-text reason given by the user.

    Returns:
        The crafted kill result string.
    """
    lines = [
        "[TOOL_KILLED]",
        f"Tool: {tool_name}",
        "The user force-stopped this tool execution while it was running.",
        "",
        "The user judged this tool call was not worth letting finish. Consider why that",
        "might be: is the action too broad (e.g. searching the whole filesystem), taking",
        "too long, redundant with information you already have, or otherwise not what",
        "the user wants? Before retrying or continuing, reconsider the approach.",
        "Narrow the scope, pick a more efficient path, or ask the user what they",
        "actually wanted.",
    ]
    if reason:
        lines.append(f"Reason given by user: {reason}")
    return "\n".join(lines)
