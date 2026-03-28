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
