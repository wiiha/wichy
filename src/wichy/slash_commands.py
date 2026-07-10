from collections.abc import Callable
from typing import TypeAlias

from prompt_toolkit.completion import NestedCompleter
from rich.table import Table

from wichy.helpers.console import console
from wichy.hooks.loader import hook_loader
from wichy.hooks.registry import hook_registry
from wichy.hooks.types import HookType
from wichy.llm_backend import backend_and_model_from_model_str, parse_generic_backend
from wichy.root_agent.root_agent import ContextResetStrategies
from wichy.tools.base import console_tool_result
from wichy.tools.task import console_task_agents

CommandHandler: TypeAlias = Callable[[str], str | None | Table]


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

        def handle_hooks(_line: str) -> str | None | Table:
            """Handle /hooks - reload and list all registered hooks."""
            # Reload hooks from all hook files
            hook_loader.reload_hooks()

            # Get all registered hooks
            all_hooks = hook_registry.list_all()

            # Check if any hooks are registered
            total_hooks = sum(
                len(hooks_list)
                for tool_hooks in all_hooks.values()
                for hooks_list in tool_hooks.values()
            )

            if total_hooks == 0:
                return "[Hooks] No hooks registered"

            # Create a rich table for display
            table = Table(title="Registered Hooks", show_lines=False)
            table.add_column("Type", style="cyan", no_wrap=True)
            table.add_column("Tool", style="green")
            table.add_column("Name", style="white")
            table.add_column("Priority", justify="right", style="yellow")
            table.add_column("Source", style="dim")
            table.add_column("Enabled", style="magenta")

            # Sort hook types for consistent output
            for hook_type in sorted(HookType, key=lambda ht: ht.value):
                if hook_type not in all_hooks:
                    continue

                tool_hooks = all_hooks[hook_type]
                # Sort by tool name (None first for wildcards)
                sorted_tools = sorted(
                    tool_hooks.keys(), key=lambda x: (x is not None, x or "")
                )

                is_lifecycle = hook_type not in (HookType.PRE_TOOL, HookType.POST_TOOL)

                for tool_name in sorted_tools:
                    hooks_list = tool_hooks[tool_name]
                    for hook in hooks_list:
                        if is_lifecycle:
                            tool_display = "-"
                        else:
                            tool_display = "all" if tool_name is None else tool_name
                        enabled_display = "✓" if hook.enabled else "✗"
                        table.add_row(
                            hook_type.value,
                            tool_display,
                            hook.name,
                            str(hook.priority),
                            hook.source,
                            enabled_display,
                        )

            return table

        def handle_help(line: str) -> str | None | Table:
            """Handle /help - show available commands."""
            from rich.table import Table

            target = line.strip().split(maxsplit=1)
            if len(target) > 1 and target[1].startswith("/"):
                # Specific command help: /help /reset
                cmd = target[1].lower()
                desc = self._descriptions.get(cmd, "No description available.")
                return f"[bold]{cmd}[/bold]: {desc}"

            table = Table(
                title="Wichy Commands", show_header=True, header_style="bold cyan"
            )
            table.add_column("Command", style="cyan")
            table.add_column("Description")

            for cmd, desc in sorted(self._descriptions.items()):
                table.add_row(cmd, desc)

            return table

        def handle_name(line: str) -> str | None:
            """Handle /name - set or show the agent display name."""
            parts = line.strip().split(maxsplit=1)
            if len(parts) > 1:
                new_name = parts[1].strip()
                self.root_agent._display_name = new_name
                return f"[green]Display name set to:[/green] {new_name}"
            current = self.root_agent.display_name
            return f"Current display name: {current}"

        def handle_model(line: str) -> str | None:
            """Handle /model - swap the LLM model mid-session."""
            parts = line.strip().split(maxsplit=1)
            if len(parts) < 2:
                return f"Current model: {self.root_agent.model_str}"
            new_model = parts[1].strip()

            KNOWN_BACKENDS = {"ollama", "llama_cpp", "open_router", "generic"}

            try:
                backend, model_name = backend_and_model_from_model_str(new_model)
                if not backend:
                    return "[red]Invalid model format: backend is empty. Expected: <backend>/<model>[/red]"
                if not model_name:
                    return "[red]Invalid model format: model name is empty. Expected: <backend>/<model>[/red]"
                if backend not in KNOWN_BACKENDS:
                    return (
                        f"[red]Unknown backend '{backend}'. "
                        f"Known backends: {', '.join(sorted(KNOWN_BACKENDS))}[/red]"
                    )
                if backend == "generic":
                    parse_generic_backend(new_model)  # validates host##model format
            except ValueError as e:
                return f"[red]{e}[/red]"

            old_model = self.root_agent.model_str
            self.root_agent.model_str = new_model
            return f"[green]Model changed:[/green] {old_model} → {new_model}"

        self._handlers: dict[str, CommandHandler] = {
            "/btw": handle_btw,
            "/exit": handle_exit,
            "/logging": handle_logging,
            "/reset": handle_reset,
            "/compact": handle_compact,
            "/drop": handle_drop,
            "/status": handle_status,
            "/hooks": handle_hooks,
            "/help": handle_help,
            "/name": handle_name,
            "/model": handle_model,
        }

        self._descriptions: dict[str, str] = {
            "/btw": "One-shot sandboxed question (carries recent context)",
            "/exit": "Exit the REPL",
            "/logging": "Toggle logging on/off (or show current state)",
            "/reset": "Nuke the entire conversation context",
            "/compact": "Summarize and compact the conversation context",
            "/drop": "Drop the last context entry",
            "/status": "Show current token count and auto-compact threshold",
            "/hooks": "Reload and list all registered hooks",
            "/help": "Show this help message",
            "/name": "Set or show the agent display name",
            "/model": "Swap the LLM model mid-session (format: <backend>/<model>)",
        }

        self._completer: NestedCompleter = NestedCompleter.from_nested_dict(
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
                "/hooks": None,
                "/help": None,
                "/name": None,
                "/model": {
                    "ollama": None,
                    "llama_cpp": None,
                    "open_router": None,
                    "generic": None,
                },
            }
        )

    def list_commands(self) -> list[dict[str, str]]:
        """Return a list of slash commands with names and descriptions."""
        return [
            {"name": name, "description": description}
            for name, description in self._descriptions.items()
        ]

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
