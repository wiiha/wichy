"""REPL - interactive read-eval-print loop for wichy."""

from prompt_toolkit import PromptSession
from rich.markdown import Markdown

from wichy.config import settings
from wichy.console import user_console
from wichy.constants import ROLE_SYSTEM
from wichy.context.handler import new_context
from wichy.helpers.needs_user_attention import needs_user_attention
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import LLMBackendContextLimitReached, LLMBackendRateLimitExceeded
from wichy.root_agent.root_agent import RootAgent
from wichy.slash_commands import (
    BtwException,
    ContextDropException,
    ContextResetException,
    SlashCommandChecker,
)

# How many messages (from the end of the main conversation) to include in a
# /btw one-shot agent call.
# Set to None to include the full conversation history.
# Set to 0 in order to not include any of the previous ctx,
# besides the initial system prompt.
BTW_CONTEXT_MESSAGES = 10


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
            except BtwException as e:
                self._print_separator()
                self._run_btw(e.question, e.model_str, e.btw_tools)
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

    def _print_btw_prompt(self) -> None:
        """Print the btw prompt header."""
        user_console.print(Markdown("\n\n---\n\n### BTW"))

    def _print_separator(self) -> None:
        """Print separator after user input."""
        user_console.print(Markdown("---"))

    def _print_assistant_response(self, content: str) -> None:
        """Print the assistant's response as markdown."""
        user_console.print(Markdown("\n---\n\n### Assistant\n"))
        markdown = Markdown(content)
        user_console.print(markdown)

    # ------------------------------------------------------------------
    # /btw
    # ------------------------------------------------------------------

    def _run_btw(self, question: str, model_str: str, tools: list) -> None:
        """
        Run a one-shot /btw question via a temporary RootAgent.

        The agent:
        - Uses a fresh context persisted under ``btw/`` (not the main context).
        - Receives a slice of the main context (system prompt + recent messages).
        - Is discarded after the call.

        Args:
            question: The user's question after "/btw ".
            model_str: Model string for the LLM call.
            tools: List of tool instances (ignored for now).
        """
        # Create a fresh persisted context under btw/ subdir.
        btw_ctx = new_context(sub_dir="btw")

        # Build the message list: system prompt + last N messages.
        main_messages = self.root_agent.context()
        if len(main_messages) > 0:
            btw_ctx.append(main_messages[0])
            del main_messages[0]
        else:
            btw_ctx.add(
                role=ROLE_SYSTEM,
                content="You are a helpful assistant answering the users questions.",
            )

        # Append the most recent messages (from the end).
        # BTW_CONTEXT_MESSAGES controls how many; set to None for full history.
        if BTW_CONTEXT_MESSAGES is None:
            for msg in main_messages[1:]:
                btw_ctx.append(msg)
        elif BTW_CONTEXT_MESSAGES > 0:
            for msg in main_messages[-BTW_CONTEXT_MESSAGES:]:
                btw_ctx.append(msg)
        else:
            pass

        # Create a one-shot agent (no tools, no first-initiative).
        btw_agent = RootAgent(
            model_str=model_str,
            tools=tools,
            name="btw",
            context=btw_ctx,
            agent_has_first_initiative=False,
            print_info_lines=False,
        )

        # process() appends the user message, calls the LLM, and returns the
        # response content. Tools are possibly available and will be handled
        # as expected by the root agent process method.
        response = btw_agent.process(question)
        response = strip_thinking_content(response)

        self._print_btw_prompt()
        user_console.print(Markdown(response))
