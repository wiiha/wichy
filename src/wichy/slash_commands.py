from collections.abc import Callable
from typing import ClassVar, TypeAlias

from prompt_toolkit.completion import DynamicCompleter, NestedCompleter
from rich.table import Table

from wichy.config.backend_resolver import resolve_config_backend
from wichy.helpers.console import console
from wichy.hooks.executor import HookExecutor
from wichy.hooks.loader import hook_loader
from wichy.hooks.registry import get_slash_commands, hook_registry
from wichy.hooks.slash_exceptions import SlashBtwException as BtwException
from wichy.hooks.slash_exceptions import (
    SlashContextDropException as ContextDropException,
)
from wichy.hooks.slash_exceptions import (
    SlashContextResetException as ContextResetException,
)
from wichy.hooks.types import HookType
from wichy.llm_backend import backend_and_model_from_model_str, parse_generic_backend
from wichy.root_agent.root_agent import ContextResetStrategies
from wichy.tools.base import console_tool_result
from wichy.tools.task import console_task_agents

CommandHandler: TypeAlias = Callable[[str], str | None | Table]


class SlashCommandChecker:
    #: Tools available to the BTW sandbox agent. Override or extend this list
    #: to give /btw commands access to specific tools.
    BTW_TOOLS: ClassVar[list] = []

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
            agent = self.root_agent
            tokens = agent.current_prompt_tokens
            threshold = agent.auto_compact_threshold
            if threshold:
                threshold_str = str(threshold)
                if tokens > 0:
                    pct = tokens * 100 // threshold
                    threshold_str = f"{threshold} ({pct}%)"
            else:
                threshold_str = "off"
            lines = [
                "[Status] Session summary",
                f"Model: {agent.model_str}",
                f"Name: {agent.display_name}",
                f"Messages: {len(agent.context.context)}",
                f"Tokens (last request): {tokens}",
                f"Auto-compact: {threshold_str}",
            ]
            return "\n".join(lines)

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

                is_lifecycle = hook_type not in (
                    HookType.PRE_TOOL,
                    HookType.POST_TOOL,
                    HookType.SLASH_COMMAND,
                    HookType.PRE_SLASH_COMMAND,
                    HookType.POST_SLASH_COMMAND,
                )

                for tool_name in sorted_tools:
                    hooks_list = tool_hooks[tool_name]
                    for hook in hooks_list:
                        if is_lifecycle:
                            tool_display = "-"
                        else:
                            tool_display = "all" if tool_name is None else tool_name
                        # Slash command hooks show their description (what
                        # /help would print) so the table documents them.
                        name_display = hook.name
                        if hook_type == HookType.SLASH_COMMAND and hook.metadata.get(
                            "description"
                        ):
                            name_display = (
                                f"{hook.name} ({hook.metadata['description']})"
                            )
                        enabled_display = "✓" if hook.enabled else "✗"
                        table.add_row(
                            hook_type.value,
                            tool_display,
                            name_display,
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
                # Specific command help: /help /reset. Live merge so
                # hook-registered commands document themselves.
                cmd = target[1].lower()
                if cmd in self._descriptions:
                    desc = self._descriptions[cmd]
                else:
                    hook = get_slash_commands(HookType.SLASH_COMMAND).get(cmd)
                    desc = (
                        hook.metadata.get("description", "")
                        if hook is not None
                        else "No description available."
                    )
                return f"[bold]{cmd}[/bold]: {desc}"

            table = Table(
                title="Wichy Commands", show_header=True, header_style="bold cyan"
            )
            table.add_column("Command", style="cyan")
            table.add_column("Description")

            for cmd, entry in sorted(self._merged_descriptions().items()):
                table.add_row(cmd, entry)

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

            KNOWN_BACKENDS = {"ollama", "llama_cpp", "open_router", "generic", "config"}

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
                if backend == "config":
                    resolve_config_backend(
                        new_model, validate_only=True
                    )  # validates alias/filepath exists
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
            "/status": "Show session summary: model, name, message count, tokens, auto-compact",
            "/hooks": "Reload and list all registered hooks",
            "/help": "Show this help message",
            "/name": "Set or show the agent display name",
            "/model": "Swap the LLM model mid-session (format: <backend>/<model>)",
        }

        self._builtin_completions: dict = {
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

    def completion_dict(self) -> dict:
        """Completion options for this checker: built-ins + live hooks.

        Hook commands are read from the registry on every call so a
        /hooks reload is reflected immediately.
        """
        options = dict(self._builtin_completions)
        for name, hook in get_slash_commands(HookType.SLASH_COMMAND).items():
            if name not in options:
                options[name] = hook.metadata.get("args")
        return options

    def _merged_descriptions(self) -> dict[str, str]:
        """Built-in descriptions overlaid with live hook-registered ones.

        Built-ins win on collision, so a hook registering a built-in's
        name cannot change what /help shows for it.
        """
        merged = dict(self._descriptions)
        for name, hook in get_slash_commands(HookType.SLASH_COMMAND).items():
            if name not in merged:
                merged[name] = hook.metadata.get("description", "")
        return merged

    def list_commands(self) -> list[dict[str, str]]:
        """Return all slash commands: built-ins plus hook-registered ones.

        Hook-registered commands are looked up live on every call so a
        /hooks reload is reflected immediately.
        """
        commands: list[dict[str, str]] = [
            {"name": name, "description": description}
            for name, description in self._descriptions.items()
        ]
        for name, hook in sorted(get_slash_commands(HookType.SLASH_COMMAND).items()):
            if name not in self._descriptions:
                commands.append(
                    {
                        "name": name,
                        "description": hook.metadata.get("description", ""),
                    }
                )
        return commands

    def _dispatch(self, command: str, args: str, line: str):
        """Run the built-in handler or the hook-registered command.

        Returns a (source, result) pair. Source is "builtin" or "hook".
        Built-in handlers may raise control exceptions (reset/drop/btw/EOF);
        those propagate untouched. Hook commands run through the executor,
        which also propagates control exceptions and isolates all others.
        """
        handler = self._handlers.get(command)
        if handler is not None:
            return "builtin", handler(line)
        hook_result = HookExecutor.run_slash_hooks(
            hook_type=HookType.SLASH_COMMAND,
            root_agent=self.root_agent,
            command=command,
            args=args,
            line=line,
        )
        if not hook_result.approved:
            return (
                "hook",
                f"[red]Blocked by hook {hook_result.hooks_denied[0]}: "
                f"{hook_result.error_message}[/red]",
            )
        if hook_result.modified_output is not None:
            return "hook", hook_result.modified_output
        # No hook modified the output: the command is still consumed. A
        # custom command must never fall through to the agent, so the
        # empty string (printed as nothing) is returned rather than None.
        return "hook", ""

    def check_command(self, line: str):
        """Dispatch a slash line, or return None for a normal message.

        Order:
            1. PRE_SLASH_COMMAND hooks (deny blocks everything after).
            2. Built-in handler if one exists (built-ins always win).
            3. Hook-registered command (SLASH_COMMAND).
            4. Unknown command message.
            5. POST_SLASH_COMMAND hooks may replace the result.
        """
        if not line.startswith("/"):
            return None
        # Split command from args so the dict lookup hits the bare "/cmd".
        command = line.strip().split(maxsplit=1)[0].lower()
        remainder = line.strip()[len(command) :].strip()

        # 1. PRE hooks see every slash line, known or unknown.
        pre = HookExecutor.run_slash_hooks(
            hook_type=HookType.PRE_SLASH_COMMAND,
            root_agent=self.root_agent,
            command=command,
            args=remainder,
            line=line.strip(),
            source=(
                "builtin"
                if command in self._handlers
                else (
                    "hook"
                    if command in get_slash_commands(HookType.SLASH_COMMAND)
                    else "unknown"
                )
            ),
        )
        if not pre.approved:
            # Denied: no dispatch, no POST hooks.
            return (
                f"[red]Blocked by hook {pre.hooks_denied[0]}: "
                f"{pre.error_message}[/red]"
            )
        if pre.modified_input is not None and "args" in pre.modified_input:
            # Rebuild the line with rewritten args; the command token is
            # untouchable by contract (the executor only honors "args").
            remainder = pre.modified_input["args"]
            line = command + (" " + remainder if remainder else "")

        # 2 + 3. Dispatch: built-in first, then hook-registered commands.
        if command in self._handlers or command in get_slash_commands(
            HookType.SLASH_COMMAND
        ):
            source, result = self._dispatch(command, remainder, line.strip())
        else:
            source, result = "unknown", f"Unknown command: {command}"

        # 4. POST hooks fire only after normal completion. Control
        # exceptions from dispatch propagate before this point, so a
        # handler raising (built-in reset/drop/btw/exit, or a hook raising
        # one) means POST does not run.
        post = HookExecutor.run_slash_hooks(
            hook_type=HookType.POST_SLASH_COMMAND,
            root_agent=self.root_agent,
            command=command,
            args=remainder,
            line=line.strip(),
            source=source,
            result=result,
        )
        if not post.approved:
            return (
                f"[red]Blocked by hook {post.hooks_denied[0]}: "
                f"{post.error_message}[/red]"
            )
        if post.modified_output is not None:
            return post.modified_output
        return result

    @property
    def completer(self) -> NestedCompleter:
        """Build the completer live on every access.

        Each access merges the built-in completions with the current
        registry state, so commands registered (or wiped by a /hooks
        reload) after this checker was constructed appear and disappear
        without rebuilding anything.
        """
        return NestedCompleter.from_nested_dict(self.completion_dict())


def completion_dict() -> dict:
    """Completion options: built-ins plus hook-registered commands.

    Hook commands contribute their "args" metadata as nested completions
    when provided (NestedCompleter semantics: None = free text, dict =
    nested). Built-ins win on a name collision, as everywhere else.
    """
    checker = SlashCommandChecker(root_agent=None)
    options = dict(checker._builtin_completions)
    for name, hook in get_slash_commands(HookType.SLASH_COMMAND).items():
        if name not in options:
            options[name] = hook.metadata.get("args")
    return options


# Evaluated on every completion request so commands registered after the
# session starts (hooks load late; /hooks can reload mid-session) are offered.
slash_completer = DynamicCompleter(
    lambda: NestedCompleter.from_nested_dict(completion_dict())
)
