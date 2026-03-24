from prompt_toolkit.completion import NestedCompleter

from wichy.helpers.console import console
from wichy.root_agent.root_agent import ContextResetStrategies
from wichy.tools.base import console_tool_result
from wichy.tools.task import console_task_agents


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


slash_completer = NestedCompleter.from_nested_dict(
    {
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


class SlashCommandChecker:
    def __init__(self, root_agent):
        self.root_agent = root_agent

    def check_command(self, line: str):
        if line.startswith("/"):
            command = line.strip().lower()
            if command == "/exit":
                raise EOFError
            if command == "/logging on":
                console.quiet = False
                console_tool_result.quiet = False
                console_task_agents.quiet = False
                return "logging on"
            if command == "/logging off":
                console.quiet = True
                console_tool_result.quiet = True
                console_task_agents.quiet = True
                return "logging off"
            if command == "/logging":
                return f"{console.quiet=} {console_tool_result.quiet=} {console_task_agents.quiet=}"
            if command == "/reset":
                raise ContextResetException(strategy=ContextResetStrategies.NUKE)
            if command == "/compact":
                raise ContextResetException(strategy=ContextResetStrategies.SUMMARY)
            if command == "/drop":
                raise ContextDropException()
            if command == "/status":
                tokens = self.root_agent.current_prompt_tokens
                threshold = self.root_agent.auto_compact_threshold
                threshold_str = str(threshold) if threshold is not None else "off"
                return f"[Status] tokens: {tokens} | auto-compact: {threshold_str}"

            return f"Unknown command: {command}"
        return None
