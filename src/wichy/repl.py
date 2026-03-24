"""REPL - interactive read-eval-print loop for wichy."""

from prompt_toolkit import PromptSession
from rich.markdown import Markdown

from wichy.console import user_console
from wichy.config import settings
from wichy.helpers.needs_user_attention import needs_user_attention
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import LLMBackendContextLimitReached, LLMBackendRateLimitExceeded
from wichy.root_agent.root_agent import RootAgent
from wichy.slash_commands import (
    ContextDropException,
    ContextResetException,
    SlashCommandChecker,
)


class Repl:
    """Interactive REPL for wichy."""

    def __init__(
        self,
        root_agent: RootAgent,
        prompt_session: PromptSession,
        cmd_checker: SlashCommandChecker,
    ):
        """
        Initialize the REPL.

        Args:
            root_agent: The RootAgent instance to process user input.
            prompt_session: PromptSession for user input with history and completion.
            cmd_checker: SlashCommandChecker for handling slash commands.
        """
        self.root_agent = root_agent
        self.prompt_session = prompt_session
        self.cmd_checker = cmd_checker

    def run(self) -> None:
        """Run the interactive REPL loop."""
        if self.root_agent.agent_has_first_initiative:
            # Execute the "wake up" message for the agent to go first
            self._print_separator()
            result = self.root_agent.process(settings.wake_up_message)
            result = strip_thinking_content(result)
            self._print_assistant_response(result)

        while True:
            try:
                self._print_user_prompt()
                needs_user_attention()
                line = self.prompt_session.prompt("> ")
                possible_cmd = self.cmd_checker.check_command(line)
                if possible_cmd is not None:
                    user_console.print(possible_cmd)
                    continue
                self._print_separator()
                result = self.root_agent.process(line)
                result = strip_thinking_content(result)
                self._print_assistant_response(result)
            except ContextResetException as e:
                self.root_agent.reset_context(strategy=e.strategy)
                continue
            except ContextDropException:
                self.root_agent.drop_last_context_entry()
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
            except KeyboardInterrupt:
                continue
            except EOFError:
                user_console.print("\nexiting...")
                exit(0)

    def _print_user_prompt(self) -> None:
        """Print the user prompt header."""
        user_console.print(Markdown("\n\n---\n\n### User"))

    def _print_separator(self) -> None:
        """Print separator after user input."""
        user_console.print(Markdown("---"))

    def _print_assistant_response(self, content: str) -> None:
        """Print the assistant's response as markdown."""
        user_console.print(Markdown("\n---\n\n### Assistant\n"))
        markdown = Markdown(content)
        user_console.print(markdown)
