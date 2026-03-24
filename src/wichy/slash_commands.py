from collections.abc import Callable
from typing import TypeAlias

from prompt_toolkit.completion import NestedCompleter

from wichy.helpers.console import console
from wichy.root_agent.root_agent import ContextResetStrategies
from wichy.tools.base import console_tool_result
from wichy.tools.task import console_task_agents

CommandHandler: TypeAlias = Callable[[str], str | None]


class ContextResetException(Exception):
    def __init__(self, strategy: ContextResetStrategies, message="Reset context"):
        self.strategy = strategy
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.message}: strategy='{self.strategy}'"


class ContextDropException(Exception):
    def __init__(self, message="Drop last context entry"):
        self.message = message
        super().__init__(self.message)

    def __str__(self):
        return f"{self.message}"


class BtwException(Exception):
    """Raised when user invokes the /btw command with a question."""

    def __init__(self, question: str, model_str: str, btw_tools: list):
        self.question = question
        self.model_str = model_str
        self.btw_tools = btw_tools
        super().__init__(question)

    def __str__(self):
        return f"/btw: {self.question}"


class SlashCommandChecker:
    #: Tools available to the BTW sandbox agent. Override or extend this list
    #: to give /btw commands access to specific tools.
    BTW_TOOLS: list = []

    def __init__(self, root_agent):
        self.root_agent = root_agent

        def handle_btw(line: str) -> str | None:
            """Handle /btw <question> - one-shot sandboxed question."""
            parts = line.strip().split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return "[BTW] Usage: /btw <question>"
            question = parts[1].strip()
            raise BtwException(
                question=question,
                model_str=self.root_agent.model_str,
                btw_tools=list(self.BTW_TOOLS),
            )

        def handle_exit(_line: str) -> str | None:
            raise EOFError

        def handle_logging(line: str) -> str | None:
            arg = (
                line.strip().split(maxsplit=1)[1:] and line.strip().split(maxsplit=1)[1]
            )
            if arg == "on":
                console.quiet = False
                console_tool_result.quiet = False
                console_task_agents.quiet = False
                return "logging on"
            if arg == "off":
                console.quiet = True
                console_tool_result.quiet = True
                console_task_agents.quiet = True
                return "logging off"
            return f"{console.quiet=} {console_tool_result.quiet=} {console_task_agents.quiet=}"

        def handle_reset(_line: str) -> str | None:
            raise ContextResetException(strategy=ContextResetStrategies.NUKE)

        def handle_compact(_line: str) -> str | None:
            raise ContextResetException(strategy=ContextResetStrategies.SUMMARY)

        def handle_drop(_line: str) -> str | None:
            raise ContextDropException()

        def handle_status(_line: str) -> str | None:
            tokens = self.root_agent.current_prompt_tokens
            threshold = self.root_agent.auto_compact_threshold
            threshold_str = str(threshold) if threshold is not None else "off"
            return f"[Status] tokens: {tokens} | auto-compact: {threshold_str}"

        self._handlers: dict[str, CommandHandler] = {
            "/btw": handle_btw,
            "/exit": handle_exit,
            "/logging": handle_logging,
            "/reset": handle_reset,
            "/compact": handle_compact,
            "/drop": handle_drop,
            "/status": handle_status,
        }

        self._completer = NestedCompleter.from_nested_dict(
            {
                "/btw": None,
                "/logging": {
                    "on": None,
                    "off": None,
                },
                "/reset": None,
                "/compact": None,
                "/drop": None,
                "/status": None,
                "/exit": None,
            }
        )

    def check_command(self, line: str):
        if line.startswith("/"):
            # Split command from args so the dict lookup hits the bare "/cmd".
            command = line.strip().split(maxsplit=1)[0].lower()
            handler = self._handlers.get(command)
            if handler is not None:
                return handler(line.strip())
            return f"Unknown command: {command}"
        return None

    @property
    def completer(self) -> NestedCompleter:
        return self._completer


slash_completer = SlashCommandChecker(root_agent=None).completer
