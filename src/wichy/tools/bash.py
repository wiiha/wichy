import re
import shlex
import subprocess
from typing import Optional

from pydantic import Field

from wichy.helpers.string import truncate_to_len
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.human_verification import require_human_verification

# Known read-only commands (safe to execute without verification)
READ_ONLY_COMMANDS = {
    # File system - read operations
    "ls",
    "dir",
    "ll",
    "la",
    "tree",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "grep",
    "rg",
    "find",
    "file",
    "stat",
    "wc",
    "md5sum",
    "sha256sum",
    # Git - read operations
    "git-status",
    "git-log",
    "git-diff",
    "git-show",
    "git-branch",
    "git-tag",
    "git-config",
    "git-remote",
    "git-reflog",
    # System - read operations
    "ps",
    "top",
    "htop",
    "df",
    "du",
    "free",
    "uname",
    "uptime",
    "env",
    "printenv",
    "which",
    "type",
    "whereis",
    # Network - read operations
    "ping",
    "traceroute",
    "nslookup",
    "dig",
    "host",
    # Development - read operations
    "python",
    "python3",
    "node",
    "npm",
    "pip",
    "pytest",
    # Other safe commands
    "echo",
    "pwd",
    "date",
    "whoami",
    "id",
}

# Known destructive/dangerous commands

DESTRUCTIVE_COMMANDS = {
    # File operations
    "rm",
    "rmdir",
    "mv",
    "cp",
    "touch",
    "mkdir",
    "mkfifo",
    "mknod",
    # Text editors (can modify files)
    "nano",
    "vim",
    "vi",
    "emacs",
    "ed",
    # Git - write operations
    "git-commit",
    "git-push",
    "git-pull",
    "git-merge",
    "git-rebase",
    "git-reset",
    "git-checkout",
    "git-clone",
    "git-add",
    "git-rm",
    # Package managers
    "apt-get",
    "apt",
    "yum",
    "dnf",
    "pacman",
    "brew",
    # System operations
    "reboot",
    "shutdown",
    "halt",
    "poweroff",
    "systemctl",
    "service",
    "initctl",
    # User management
    "useradd",
    "userdel",
    "usermod",
    "passwd",
    # Network operations
    "iptables",
    "nftables",
    "ufw",
    "firewall-cmd",
    "netplan",
    "ifconfig",
    "ip",
    "route",
    # Disk operations
    "fdisk",
    "parted",
    "mkfs",
    "mount",
    "umount",
    # Compression (can overwrite)
    "tar",
    "gzip",
    "zip",
    "unzip",
    "xz",
    # Process control
    "kill",
    "killall",
    "pkill",
    # Data modification
    "sed",
    "awk",
    # user preference
}

# Flags that make otherwise safe commands destructive
DESTRUCTIVE_FLAGS = {
    "-i": ["sed", "awk"],  # In-place editing
    "-f": ["rm"],  # Force
    "--force": ["rm", "git-push", "git-merge"],
    "--hard": ["git-reset"],
    "--clean": ["git-reset"],
    "-R": ["chmod", "chown"],  # Recursive
    "-r": ["rm", "chmod", "chown", "cp"],  # Recursive
    "--recursive": ["rm", "chmod", "chown", "cp"],
}


def is_destructive_command(*args, **kwargs) -> bool:
    """
    Determine if a bash command is destructive and requires verification.

    This function is designed to be used as a predicate for the
    @require_human_verification decorator. It accepts *args, **kwargs
    to match the decorator's calling convention, and extracts the
    'command' parameter from kwargs.

    Args:
        *args: Variable positional arguments (ignored, for compatibility)
        **kwargs: Variable keyword arguments, should contain 'command'

    Returns:
        True if the command is destructive and needs verification, False otherwise
    """
    # Extract the command from kwargs
    # The decorator calls the predicate with *args, **kwargs from the decorated
    # function
    # For BashTool.execute, this will include 'command' and 'timeout' in kwargs
    command = kwargs.get("command", "")

    command = command.strip()

    # Check for command chaining with destructive operators
    if any(op in command for op in ["&&", ";", "||", "|"]):
        # For chained commands, verify if ANY command in the chain is destructive
        parts = re.split(r"(&&|;|\|\||\|)", command)
        subcommands = []
        for i, part in enumerate(parts):
            if part.strip() and part not in ["&&", ";", "||", "|"]:
                subcommands.append(part.strip())

        for subcommand in subcommands:
            if is_destructive_command(command=subcommand):
                return True
        return False

    # Parse the command to get the base command and arguments
    try:
        tokens = shlex.split(command)
    except ValueError:
        # If parsing fails, assume destructive (safe default)
        return True

    if not tokens:
        return False

    # Get the base command (handle git subcommands like "git status" -> "git-status")
    base_cmd = tokens[0].lower()
    if base_cmd == "git" and len(tokens) > 1:
        base_cmd = f"git-{tokens[1].lower()}"

    # Check for known destructive commands
    if base_cmd in DESTRUCTIVE_COMMANDS:
        return True

    # Check for known safe commands
    if base_cmd in READ_ONLY_COMMANDS:
        # Still need to check for destructive flags
        for flag, cmd_list in DESTRUCTIVE_FLAGS.items():
            if base_cmd in cmd_list and flag in tokens:
                return True
        return False

    # Check for destructive redirects
    if ">" in command or ">>" in command:
        return True

    # Check for piped commands with destructive potential
    if "|" in command:
        # If the first command is destructive, verify
        first_cmd = command.split("|")[0].strip()
        return is_destructive_command(command=first_cmd)

    # Unknown command - verify for safety
    return True


class BashParameters(ParametersModel):
    command: str = Field(..., description="The command to execute")
    timeout: Optional[int] = Field(
        30, description="Timeout in seconds for the command execution"
    )
    description: Optional[str] = Field(
        None, description="Shortly describe purpose of execution"
    )

    def info(self):
        s = ""

        if self.description:
            s += f'description="{self.description}" '
        return f'{s}command="{truncate_to_len(self.command)}" timeout={self.timeout}'


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
    def execute(
        self, command: str, timeout: int = 30, description: Optional[str] = None
    ) -> str:
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


# Set the custom verification predicate on the execute method
# The decorator copies attributes, so we need to set on the wrapper
BashTool.execute._should_verify = is_destructive_command
