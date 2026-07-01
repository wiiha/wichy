"""Allow hooks to read/modify the active conversation context."""

from wichy.console import user_console

_active_context = None  # Will be ContextHandler


def set_active_context(ctx):
    """Set the currently active context handler (called after RootAgent construction)."""
    global _active_context
    _active_context = ctx


def get_active_context():
    """Return the currently active context handler, or None."""
    return _active_context


def context_add(role: str, content: str) -> bool:
    """Add a message to the active conversation context.

    Returns True if successful, False if no active context.
    If no context is set, prints a warning and returns False (no-op).
    """
    if _active_context is None:
        user_console.print(
            "[yellow]Warning: context_add() called but no active context is set. "
            "Message not added.[/yellow]"
        )
        return False
    _active_context.add(role=role, content=content)
    return True
