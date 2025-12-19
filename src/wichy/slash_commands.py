from wichy.helpers.console import console
from wichy.tools.base import console_tool_result
from wichy.agents.sub_agent import console_sub_agents
from prompt_toolkit import prompt
from prompt_toolkit.completion import NestedCompleter

slash_completer = NestedCompleter.from_nested_dict({
    "/logging": {
        "on": None,
        "off": None,
    },
    "/exit": None,
})

class SlashCommandChecker:
    def check_command(self, line):
        if line.startswith("/"):
            command = line.strip().lower()
            if command == "/exit":
                raise EOFError
            if command == "/logging on":
                console.quiet = False
                console_tool_result.quiet = False
                console_sub_agents.quiet = False
                return "logging on"
            if command == "/logging off":
                console.quiet = True
                console_tool_result.quiet = True
                console_sub_agents.quiet = True
                return "logging off"

            return f"Unknown command: {command}"
        return None
