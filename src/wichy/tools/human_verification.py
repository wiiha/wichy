import functools
from typing import Any, Callable, Optional, Tuple

from prompt_toolkit import PromptSession
from rich import print

SKIP_HUMAN_VERIFICATION = False

prompt_session = PromptSession()


def require_human_verification(func: Callable) -> Callable:
    """
    Decorator that requires human y/n verification before executing a function.
    To provide a custom action/message for a specific function, set attributes
      func._action_label = "Short label"
      func._action_message = "Longer explanation"
    before calling the function (or set them immediately after defining the function).
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        args_str = ", ".join(getattr(a, "name", repr(a)) for a in args)
        kwargs_str = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
        all_args = ", ".join(filter(None, [args_str, kwargs_str]))

        # Resolve label/message: explicit attributes -> docstring first line -> function name
        label: Optional[str] = getattr(wrapper, "_action_label", None)
        if not label and func.__doc__:
            label = func.__doc__.strip().splitlines()[0] or None
        if not label:
            label = func.__name__

        if SKIP_HUMAN_VERIFICATION:
            return func(*args, **kwargs)

        message: Optional[str] = getattr(wrapper, "_action_message", None)

        print(f"\n[bold yellow]ACTION:[/bold yellow] {label}")
        if message:
            print(message)
        if all_args:
            print(all_args)

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
            print("Please enter 'y' or 'n <optional reason>'")

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
      def should_block_dangerous_command(self, command: str, timeout: int) -> Tuple[bool, Optional[str]]:
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
    print(f"Rebooting {hostname}...")
    return f"Reboot command issued to {hostname}"


# Usage
if __name__ == "__main__":
    try:
        print(reboot_server("db-01.example.com"))
    except PermissionError as e:
        print("Denied:", e)


@require_human_verification
def delete_file(path: str):
    """Delete a file permanently"""
    print(f"Deleting: {path}")
    return f"Deleted {path}"


# Set human-friendly label & message for the decorator to display
delete_file._action_label = "Delete file"
delete_file._action_message = (
    "This will permanently remove the file from disk. Make sure you have backups."
)

# Usage
if __name__ == "__main__":
    try:
        result = delete_file("/tmp/test.txt")
        print("Result:", result)
    except PermissionError as e:
        print("Denied:", e)
