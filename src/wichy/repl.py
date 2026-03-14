"""REPL - interactive read-eval-print loop for wichy."""

from typing import Optional

from prompt_toolkit import PromptSession
from rich import print
from rich.markdown import Markdown

from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import LLMBackendContextLimitReached
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
        while True:
            try:
                self._print_user_prompt()
                line = self.prompt_session.prompt("> ")
                possible_cmd = self.cmd_checker.check_command(line)
                if possible_cmd is not None:
                    print(possible_cmd)
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
                print(
                    "[red bold]Error:[/red bold] "
                    + str(e)
                    + "\n[green bold]Tip:[/green bold] Try dropping some messages or summarizing using slash commands."
                )
                continue
            except KeyboardInterrupt:
                continue
            except EOFError:
                print("\nexiting...")
                exit(0)
    
    def _print_user_prompt(self) -> None:
        """Print the user prompt header."""
        print(Markdown("\n\n---\n\n### User"))
    
    def _print_separator(self) -> None:
        """Print separator after user input."""
        print(Markdown("---"))
    
    def _print_assistant_response(self, content: str) -> None:
        """Print the assistant's response as markdown."""
        print(Markdown("\n---\n\n### Assistant\n"))
        markdown = Markdown(content)
        print(markdown)
