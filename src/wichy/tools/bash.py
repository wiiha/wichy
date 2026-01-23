import subprocess
from typing import Optional

from pydantic import BaseModel, Field

from wichy.helpers.string import truncate_to_len

from .base import BaseTool, ParametersModel
from .human_verification import require_human_verification


class BashParameters(ParametersModel):
    command: str = Field(..., description="The command to execute")
    timeout: Optional[int] = Field(
        30, description="Timeout in seconds for the command execution"
    )

    def info(self):
        return f'command="{truncate_to_len(self.command)}" timeout={self.timeout}'


class BashTool(BaseTool):
    name = "bash"
    description = "Execute an arbitrary command using subprocess, imagine it being bash. Calls to this tool will be audited before execution."
    parameters_model = BashParameters
    description_long = """
Executes a given bash command with optional timeout. Working directory persists between commands; shell state (everything else) does not. The shell environment is initialized from the user's profile (bash or zsh).

IMPORTANT: This tool is for terminal operations like git, npm, docker, etc. DO NOT use it for file operations (reading, writing, editing, searching, finding files) - use the specialized tools for this instead.

Before executing the command, please follow these steps:

1. Directory Verification:
   - If the command will create new directories or files, first use `ls` to verify the parent directory exists and is the correct location
   - For example, before running "mkdir foo/bar", first use `ls foo` to check that "foo" exists and is the intended parent directory

2. Command Execution:
   - Always quote file paths that contain spaces with double quotes (e.g., cd "path with spaces/file.txt")
   - Examples of proper quoting:
     - cd "/Users/name/My Documents" (correct)
     - cd /Users/name/My Documents (incorrect - will fail)
     - python "/path/with spaces/script.py" (correct)
     - python /path/with spaces/script.py (incorrect - will fail)
   - After ensuring proper quoting, execute the command.
   - Capture the output of the command.

Usage notes:

- The command argument is required.
- It is very helpful if you write a clear, concise description of what this command does. For simple commands, keep it brief (5-10 words). For complex commands (piped commands, obscure flags, or anything hard to understand at a glance), add enough context to clarify what it does.

- When issuing multiple commands:
  - If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message. For example, if you need to run "git status" and "git diff", send a single message with two Bash tool calls in parallel.
  - If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together (e.g., `git add . && git commit -m "message" && git push`). For instance, if one operation must complete before another starts (like mkdir before cp, Write before Bash for git operations, or git add before git commit), run these operations sequentially instead.
  - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail
  - DO NOT use newlines to separate commands (newlines are ok in quoted strings)
- Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.
  <good-example>
  pytest /foo/bar/tests
  </good-example>
  <bad-example>
  cd /foo/bar && pytest tests
  </bad-example>"""

    @require_human_verification
    def execute(self, command: str, timeout: int = 30) -> str:
        """Execute the given command."""
        try:
            result = subprocess.run(
                command,  # Pass as string, not split
                shell=True,  # Enable shell processing
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
                timeout=timeout,
            )
            return result.stdout
        except Exception as e:
            return f"error: {e}"
