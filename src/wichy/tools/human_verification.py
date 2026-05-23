import functools
import threading
from typing import Any, Callable, Optional

from prompt_toolkit import PromptSession
from rich.console import Console

from wichy.config.settings import settings
from wichy.console import user_console
from wichy.helpers.needs_user_attention import needs_user_attention
from wichy.helpers.verification_provider import get_verification_provider

PIPELINE_MODE = False

prompt_session: PromptSession[str] = PromptSession()
special_console: Console = Console(quiet=False)

_user_interaction_lock = threading.Lock()


def in_pipeline_mode() -> bool:
    """Returns True when wichy is running in pipeline mode (--prompt)."""
    return PIPELINE_MODE


def set_pipeline_mode(active: bool) -> None:
    """Enable or disable pipeline mode. Call this before any agent/tool runs."""
    global PIPELINE_MODE
    PIPELINE_MODE = active


def require_human_verification(func: Callable) -> Callable:
    """
    Decorator that conditionally requires human y/n verification before executing
    a function.

    To provide a custom action/message for a specific function, set attributes:
        func._action_label = "Short label"
        func._action_message = "Longer explanation"

    To conditionally skip verification, set a predicate:
        func._should_verify = lambda *args, **kwargs: True  # Always verify (default)
        func._should_verify = lambda *args, **kwargs: False  # Never verify
        func._should_verify = custom_verification_logic     # Custom callable

    The predicate receives the same args/kwargs as the decorated function and should
    return True if verification is needed, False otherwise.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        # Check if verification should be skipped based on tool's predicate
        # IMPORTANT: Access attributes from 'wrapper', not 'func'
        # When attributes are set after decoration, they're set on the wrapper,
        # not on the original func that's captured in the closure
        should_verify_predicate = getattr(wrapper, "_should_verify", None)

        # Default to always verifying if no predicate is set (backwards compatible)
        if should_verify_predicate is not None:
            try:
                needs_verification = should_verify_predicate(*args, **kwargs)
            except Exception as e:
                # If predicate fails, err on the side of caution and verify
                user_console.print(
                    f"[yellow]Warning: Verification predicate failed: {e}[/yellow]"
                )
                needs_verification = True
        else:
            # Backwards compatible: always verify if no predicate set
            needs_verification = True

        # Skip verification entirely if predicate says no and global flag allows
        if not needs_verification:
            return func(*args, **kwargs)

        # Build argument string for display
        args_str = ", ".join(getattr(a, "name", repr(a)) for a in args)
        kwargs_str = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
        all_args = ", ".join(filter(None, [args_str, kwargs_str]))

        # Resolve label/message: explicit attributes -> docstring first line ->
        # function name
        # IMPORTANT: Access attributes from 'wrapper', not 'func'
        label: Optional[str] = getattr(wrapper, "_action_label", None)
        if not label and wrapper.__doc__:
            label = wrapper.__doc__.strip().splitlines()[0] or None
        if not label:
            label = wrapper.__name__

        if settings.skip_human_verification:
            return func(*args, **kwargs)

        if in_pipeline_mode():
            tool_name = getattr(args[0], "name", None) or label if args else label
            raise PermissionError(
                f"This tool requires human verification and cannot run in pipeline mode. "
                f"Tool: {tool_name}, args: {all_args}"
            )

        message: Optional[str] = getattr(wrapper, "_action_message", None)

        current_vp = get_verification_provider()

        if current_vp:
            res = current_vp.verify(label, message, all_args)
            if res.ok:
                return func(*args, **kwargs)

            # not ok
            msg = f"User denied your suggested execution of: {all_args}"
            if res.reason != "":
                msg += "\nReason for denied execution: " + res.reason
            raise PermissionError(msg)

        with _user_interaction_lock:

            # Pause for the prompt - buffers output from other threads
            with user_console.paused():

                special_console.print(f"\n[bold yellow]ACTION:[/bold yellow] {label}")
                if message:
                    special_console.print(message)
                if all_args:
                    special_console.print(all_args)

                needs_user_attention()
                while True:
                    line = prompt_session.prompt("Proceed? (y/n): ")
                    response = str(line).strip().lower()
                    if response.startswith("y"):
                        return func(*args, **kwargs)
                    if response.startswith("n"):
                        # check if user also added reason
                        x = (
                            response.removeprefix("no")
                            .removeprefix("n")
                            .removeprefix(",")
                            .strip()
                        )
                        msg = f"User denied your suggested execution of: {all_args}"
                        if x != "":
                            msg += "\nReason for denied execution: " + x
                        raise PermissionError(msg)
                    special_console.print("Please enter 'y' or 'n <optional reason>'")

    return wrapper


def block_on(decision_func: Callable) -> Callable:
    """
    Decorator that conditionally blocks execution based on a decision function.

    The decision function should have the same signature as the decorated function
    (including 'self' if applicable) and return a tuple:
        (should_block: bool, reason: Optional[str])

    - If should_block is True, raises PermissionError with the provided reason.
    - If should_block is False, executes the function normally.

    Example:
        def should_block_dangerous_command(self, command: str, timeout: int) ->
        Tuple[bool, Optional[str]]:
            if "rm -rf" in command:
                return True, "Destructive command 'rm -rf' is not allowed"
            return False, None

        @block_on(should_block_dangerous_command)
        def execute(self, command: str, timeout: int = 30) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            # Call the decision function with the same arguments
            should_block, reason = decision_func(*args, **kwargs)

            if should_block:
                message = reason or f"Execution blocked by {decision_func.__name__}"
                raise PermissionError(message)

            return func(*args, **kwargs)

        return wrapper

    return decorator


@require_human_verification
def reboot_server(hostname: str):
    """Reboot remote server"""
    user_console.print(f"Rebooting {hostname}...")
    return f"Reboot command issued to {hostname}"


# Usage
if __name__ == "__main__":
    try:
        user_console.print(reboot_server("db-01.example.com"))
    except PermissionError as e:
        user_console.print("Denied:", e)


@require_human_verification
def delete_file(path: str):
    """Delete a file permanently"""
    user_console.print(f"Deleting: {path}")
    return f"Deleted {path}"


# Set human-friendly label & message for the decorator to display
delete_file._action_label = "Delete file"  # type: ignore[attr-defined]
delete_file._action_message = "This will permanently remove the file from disk. Make sure you have backups."  # type: ignore[attr-defined]

# Usage
if __name__ == "__main__":
    try:
        result = delete_file("/tmp/test.txt")
        user_console.print("Result:", result)
    except PermissionError as e:
        user_console.print("Denied:", e)
