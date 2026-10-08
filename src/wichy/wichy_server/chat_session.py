from wichy.config import settings
from wichy.console import user_console
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import (
    LLMBackendContextLimitReached,
    LLMBackendRateLimitExceeded,
    LLMBackendServerOverloaded,
    LLMBackendUnhandledException,
)
from wichy.root_agent.root_agent import RootAgent
from wichy.helpers.shutdown import shutdown_requested
from wichy.slash_commands import (
    BtwException,
    ContextDropException,
    ContextResetException,
    SlashCommandChecker,
)
import threading
import time
import traceback
from typing import Optional
from queue import Queue, Empty

_REPEAT_COLLAPSE_AFTER = 10


class ChatSession:
    """
    ChatSession for Wichy is used in wichy server mode.
    An instance will own the RootAgent and be responsible
    for passing input to the RootAgent.
    """

    def __init__(
        self,
        root_agent: RootAgent,
        cmd_checker: SlashCommandChecker,
    ):
        """
        Initialize a ChatSession

        Args:
            root_agent: The RootAgent instance to process user input.
            cmd_checker: SlashCommandChecker for handling slash commands.
        """
        self.root_agent = root_agent
        self.cmd_checker = cmd_checker
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._input_queue: Queue[str] = Queue()
        self.last_line_seen: float = 0.0
        self._turn_started_at: Optional[float] = None
        self._last_error_type: Optional[str] = None
        self._repeat_count: int = 0

    @property
    def input_queue(self) -> Queue[str]:
        return self._input_queue

    def run(self) -> None:
        """Run the loop that reads from a queue and passes to root agent"""
        self.last_line_seen = time.monotonic()
        if self.root_agent.agent_has_first_initiative:
            # Same failure tolerance as the loop below: a wake-up error must not
            # kill the session before it reads a line.
            try:
                self._print_separator()
                self._turn_started_at = time.monotonic()
                try:
                    result = self.root_agent.process(settings.wake_up_message)
                finally:
                    self._turn_started_at = None
                result = strip_thinking_content(result)
                self._print_assistant_response(result)
            except BaseException as e:
                self._print_failure(e)

        while not self._stop_event.is_set():
            try:
                line = self.input_queue.get(timeout=1.0)
                self.last_line_seen = time.monotonic()
                possible_cmd = self.cmd_checker.check_command(line)
                if possible_cmd is not None:
                    user_console.print(possible_cmd)
                    continue
                # Skip empty or whitespace-only input
                if not line.strip():
                    continue
                self._print_separator()
                self._turn_started_at = time.monotonic()
                try:
                    result = self.root_agent.process(line)
                finally:
                    self._turn_started_at = None
                result = strip_thinking_content(result)
                self._print_assistant_response(result)
                self._last_error_type = None
                self._repeat_count = 0
            except Empty:
                continue
            except ContextResetException as e:
                self.root_agent.reset_context(strategy=e.strategy)
                continue
            except ContextDropException:
                self.root_agent.drop_last_context_entry()
                continue
            except BtwException:
                user_console.print(
                    "[red bold]Error:[/red bold] Server mode does not support /btw command."
                )
                continue
            except LLMBackendContextLimitReached as e:
                user_console.print(
                    "[red bold]Error:[/red bold] "
                    + str(e)
                    + "\n[green bold]Tip:[/green bold] Try dropping some messages or summarizing using slash commands."
                )
                continue
            except LLMBackendRateLimitExceeded as e:
                user_console.print(
                    "[red bold]Error:[/red bold] "
                    + str(e)
                    + "\n[green bold]Tip:[/green bold] Rate limit reached. Please wait a moment before sending more requests."
                )
                continue
            except LLMBackendUnhandledException as e:
                user_console.print("[red bold]Error:[/red bold] " + str(e))
                continue
            except LLMBackendServerOverloaded as e:
                user_console.print(
                    "[red bold]Error:[/red bold] "
                    + str(e)
                    + "\n[green bold]Tip:[/green bold] The server is overloaded. Please wait a moment before sending more requests."
                )
                continue
            except EOFError:
                user_console.print("\nexiting...")
                user_console.flush()
                shutdown_requested.set()
            except Exception as e:
                self._print_failure(e)
                continue
            except BaseException as e:
                self._print_failure(e)
                continue

    def _print_failure(self, e: BaseException) -> None:
        """Report an unexpected failure without ending the loop."""
        error_type = type(e).__name__
        if error_type == self._last_error_type:
            self._repeat_count += 1
        else:
            self._last_error_type = error_type
            self._repeat_count = 1
        if self._repeat_count > _REPEAT_COLLAPSE_AFTER:
            user_console.print(
                f"[red bold]Error:[/red bold] {error_type}: {str(e)[:120]} (repeated)"
            )
            return
        text = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        if str(e) == "":
            text = f"{error_type}: (no message)\n{text}"
        user_console.print(
            "[red bold]Error:[/red bold] unexpected failure in run loop:\n"
            + text[:4000]
        )

    def status(self) -> dict:
        """Report the loop's current state for health/UI polling.

        "running" means a turn is in flight, however long it takes; callers
        that care about slowness use turn_seconds with their own threshold.
        """
        thread = self._thread
        if thread is None:
            state = "none"
        elif not thread.is_alive():
            state = "stopped"
        elif self._stop_event.is_set():
            state = "stopping"
        elif self._turn_started_at is None:
            state = "idle"
        else:
            state = "running"
        turn_seconds = (
            time.monotonic() - self._turn_started_at
            if self._turn_started_at is not None
            else 0.0
        )
        return {
            "status": state,
            "last_line_seen": self.last_line_seen,
            "turn_seconds": turn_seconds,
        }

    def start(self) -> threading.Thread:
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout)
        if thread.is_alive():
            user_console.print(
                "[yellow]Warning: session run thread did not stop within "
                + str(timeout)
                + "s; carrying on.[/yellow]"
            )

    def _print_user_prompt(self) -> None:
        """Print the user prompt header."""
        user_console.print("\n\n---\n\n### User")

    def _print_separator(self) -> None:
        """Print separator after user input."""
        user_console.print("---")

    def _print_assistant_response(self, content: str) -> None:
        """Print the assistant's response as markdown."""
        user_console.print(f"\n---\n\n### {self.root_agent.display_name}\n")
        user_console.print(content)
