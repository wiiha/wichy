from wichy.config import settings
from wichy.console import user_console
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import (
    LLMBackendContextLimitReached,
    LLMBackendRateLimitExceeded,
    LLMBackendServerOverloaded,
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
from typing import Optional
from queue import Queue, Empty


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

    @property
    def input_queue(self) -> Queue[str]:
        return self._input_queue

    def run(self) -> None:
        """Run the loop that reads from a queue and passes to root agent"""
        if self.root_agent.agent_has_first_initiative:
            # Execute the "wake up" message for the agent to go first
            self._print_separator()
            result = self.root_agent.process(settings.wake_up_message)
            result = strip_thinking_content(result)
            self._print_assistant_response(result)

        while not self._stop_event.is_set():
            try:
                line = self.input_queue.get(timeout=1.0)
                # user_console.print(line)
                possible_cmd = self.cmd_checker.check_command(line)
                if possible_cmd is not None:
                    user_console.print(possible_cmd)
                    continue
                # Skip empty or whitespace-only input
                if not line.strip():
                    continue
                self._print_separator()
                result = self.root_agent.process(line)
                result = strip_thinking_content(result)
                self._print_assistant_response(result)
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

    def start(self) -> threading.Thread:
        if self._thread is not None and self._thread.is_alive():
            # raise RuntimeError("ChatSession already started")
            return self._thread
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout)

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
