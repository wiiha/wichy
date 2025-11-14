
from helpers.console import console, console_tool_result


class SlashCommandChecker:
    def check_command(self, line):
        if line.startswith("/"):
            command = line.strip().lower()
            if command == "/exit":
                raise EOFError
            if command == "/log on":
                console.quiet = False
                console_tool_result.quiet = False
                return "logging on"
            if command == "/log off":
                console.quiet = True
                console_tool_result.quiet = True
                return "logging off"
            
            return f"Unknown command: {command}"
        return None
