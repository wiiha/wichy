"""User-facing console output, routed through rich."""

from rich.console import Console

user_console = Console(quiet=False)


def set_user_output_quiet(quiet: bool) -> None:
    """Set whether user_console suppresses output."""
    user_console.quiet = quiet
