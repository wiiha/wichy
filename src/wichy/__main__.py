from typing import Any
import atexit
import os
import signal
import sys
import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

from wichy.agent_builder import AgentBuilderError, build_agent_from_config
from wichy.cli.handlers import (
    handle_install_commands,
    handle_ls_commands,
    handle_new_commands,
    handle_ra_template,
)
from wichy.cli_parser import CliParser
from wichy.wichy_server import (
    ChatSession,
    set_input_queue as set_server_input_queue,
    set_active_session as set_server_active_session,
)
from wichy.config import settings
from wichy.console import set_user_output_quiet, user_console, ServerConsole
from wichy.constants import ROLE_USER
from wichy.context.handler import context_from_file, latest_context_file
from wichy.helpers.console import console
from wichy.helpers.string import strip_thinking_content
from wichy.hooks import initialize_hooks
from wichy.hooks.executor import HookExecutor
from wichy.hooks.context_access import set_active_context as hooks_set_active_context
from wichy.hooks.types import HookType
from wichy.repl import Repl
from wichy.root_agent import ALL_ROOT_AGENT_DESC
from wichy.root_agent.helpers import parse_root_agent_markdown_desc
from wichy.server import run_server, set_server_port, start_server_in_background
from wichy.skills import SkillLoader
from wichy.slash_commands import SlashCommandChecker, slash_completer
from wichy.tool_manager import ToolManager, _matches_tool_patterns
from wichy.tools import human_verification
from wichy.tools.base import console_tool_result
from wichy.tools.notes import set_scratchpad_slug
from wichy.tools.task import console_task_agents
from wichy.helpers.shutdown import shutdown_requested
from wichy.helpers.verification_provider import set_verification_provider
from wichy.wichy_server.verification_provider import ServerVerificationProvider
from wichy.helpers.interaction_provider import set_interaction_provider
from wichy.wichy_server.interaction_provider import ServerInteractionProvider

# Module-level reference to root_agent for SESSION_END cleanup hook
_root_agent_for_cleanup: Any | None = None


def _cleanup():
    """Clean up resources before Python exit.

    This atexit handler ensures daemon threads are properly shut down
    before Python finalization, preventing deadlocks on exit.
    """
    # Fire SESSION_END hook before stopping threads
    if _root_agent_for_cleanup is not None:
        try:
            HookExecutor.run_context_hooks(
                HookType.SESSION_END,
                root_agent=_root_agent_for_cleanup,
            )
        except Exception:
            pass

    # Stop console output thread first
    try:
        from wichy.console.user import user_console

        user_console.shutdown()
    except Exception:
        pass

    # Stop browser and its event loop thread
    try:
        from wichy.helpers.browser import browser_manager

        browser_manager.shutdown()
    except Exception:
        pass

    # Context watching is per-context instance, not global.
    # Context instances are created fresh and context.watch may be called
    # but there's no global context_handler singleton to stop.


# Register cleanup before Python starts finalization
atexit.register(_cleanup)


def initialize_skills():
    """Install default skills and load all skills."""
    skill_loader = SkillLoader()
    installed = skill_loader.install_default_skills()
    if installed > 0:
        user_console.print(f"[dim]Installed {installed} default skill(s)[/dim]")
    return skill_loader.load_all_skills()


def initialize_tools(tool_manager, selected_ra, args):
    """Determine and create tools based on CLI args and agent description."""
    if args.tools:
        # CLI --tools flag takes highest priority
        return tool_manager.create_tools(allowed=args.tools, excluded=args.not_tools)
    elif "tools" in selected_ra.props and selected_ra.props["tools"]:
        # Agent-desc tools property (middle priority)
        agent_desc_tools = selected_ra.props["tools"]
        return tool_manager.create_tools(
            allowed=agent_desc_tools, excluded=args.not_tools
        )
    else:
        # All tools (default)
        return tool_manager.create_tools(allowed="", excluded=args.not_tools)


def setup_console_logging(args):
    """Configure console logging based on CLI args."""
    if args.show_log:
        console.quiet = False
        if args.log_tools:
            console_tool_result.quiet = False
        if args.log_agents:
            console_task_agents.quiet = False
    else:
        console.quiet = True


def setup_server(root_agent):
    """
    This function perform relevant configuration and setup of server before starting it.
    Such as connecting root agent context to the server context editor.
    The method should only contain setup things that are shared between all
    modes wichy can run in (REPL, Server ...), things relevant for only one mode
    should be configure in dedicated methods.
    """

    # Set active context and root agent for context editor if server is enabled
    try:
        from wichy.tools.context_editor import api as context_editor_api

        context_editor_api.set_active_context(root_agent.context)
        context_editor_api.set_active_root_agent(root_agent)
    except Exception as e:
        user_console.print(
            f"[yellow]Warning: Could not set active context for web editor: {e}[/yellow]"
        )


def main():
    # Reap zombie child processes — we run as PID 1 in Docker containers
    def _sigchld_handler(signum, frame):
        try:
            while True:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
        except ChildProcessError:
            pass

    signal.signal(signal.SIGCHLD, _sigchld_handler)

    parser = CliParser()
    args = parser.parse()

    if args.server_mode and args.no_server:
        print("error: server mode and --no-server are incompatible, choose one")
        exit(1)

    if args.server_mode:
        user_console.set_impl(ServerConsole())

    # Reset scratchpad selection on every CLI run so no scratchpad is active at start
    set_scratchpad_slug(None)

    # Set pipeline mode when --prompt is given (before any agent/tool runs)
    if args.prompt is not None:
        human_verification.set_pipeline_mode(True)
        set_user_output_quiet(True)

    settings.parallel_exec = not args.seq_exec
    settings.max_backend_connections = args.max_backend_connections

    cmd_checker = None  # will be set after root_agent is created

    kb = KeyBindings()

    @kb.add("c-o")
    def _(event):
        event.current_buffer.insert_text("\n")

    prompt_session = PromptSession(
        history=FileHistory(settings.history_file),
        auto_suggest=AutoSuggestFromHistory(),
        completer=slash_completer,
        key_bindings=kb,
    )

    # Tools will be determined after root agent selection
    # (tools depend on agent-desc tools property + CLI flags)
    tool_manager = ToolManager()

    # Install default skills and load all skills
    loaded_skills = initialize_skills()

    # Initialize hooks

    initialize_hooks()

    # Handle subcommands that exit early
    handle_ra_template(args)

    # Handle ls commands
    if not handle_ls_commands(args, tool_manager):
        # Handle new commands
        if not handle_new_commands(args):
            # Handle install commands
            if not handle_install_commands(args):
                # At this point if ls/ra/new/install is specified without a subcommand, something is wrong.
                if args.command in ("ls", "ra", "new", "install"):
                    parser.print_usage()
                    exit(1)

    # Parse all root agent descriptions
    root_agent_descs = [
        parse_root_agent_markdown_desc(rad) for rad in ALL_ROOT_AGENT_DESC
    ]

    # Select root agent to determine tools property
    root_agent_descs_by_name = {ra.props["name"]: ra for ra in root_agent_descs}
    selected_ra_name = args.root_agent_description
    selected_ra = root_agent_descs_by_name.get(selected_ra_name)
    if not selected_ra:
        user_console.print(
            f"[red]error:[/red] Root agent '{selected_ra_name}' not found",
        )
        exit(1)

    # Determine tools based on priority
    in_tools = initialize_tools(tool_manager, selected_ra, args)

    # MCP tool discovery
    if not args.no_mcp:
        try:
            from wichy.mcp_host import discover_mcp_tools, shutdown_mcp

            native_tool_names = {t.name for t in in_tools}
            mcp_tools = discover_mcp_tools(native_tool_names)

            if mcp_tools:
                user_console.print(
                    f"Discovered {len(mcp_tools)} MCP tool(s)",
                )

            # Apply same glob filtering as native tools
            if args.tools:
                patterns = [p.strip() for p in args.tools.split(",") if p.strip()]
                if patterns:
                    mcp_tools = [
                        t for t in mcp_tools if _matches_tool_patterns(t.name, patterns)
                    ]

            if args.not_tools:
                patterns = [p.strip() for p in args.not_tools.split(",") if p.strip()]
                if patterns:
                    mcp_tools = [
                        t
                        for t in mcp_tools
                        if not _matches_tool_patterns(t.name, patterns)
                    ]

            # Merge with native tools
            in_tools = in_tools + mcp_tools

            # Register cleanup on exit
            atexit.register(shutdown_mcp)
        except Exception as e:
            user_console.print(f"[red]MCP integration failed: {e}[/red]")

    # Load context from file if specified (before building agent)
    loaded_context = None
    if args.load_ctx and args.last_ctx:
        user_console.print(
            "[red]error:[/red] --load-ctx and --last-ctx are mutually exclusive"
        )
        user_console.flush()
        exit(1)

    if args.load_ctx:
        try:
            loaded_context = context_from_file(args.load_ctx)
            user_console.print(
                f"[green]✓ Loaded conversation context from:[/green] {args.load_ctx}"
            )
        except Exception as e:
            user_console.print(f"[red]✗ Failed to load context file:[/red] {e}")
            user_console.flush()
            exit(1)

    if args.last_ctx:
        try:
            path = latest_context_file()
            loaded_context = context_from_file(path)
            user_console.print(
                f"[green]✓ Loaded most recent context:[/green] {path.name}"
            )
        except FileNotFoundError as e:
            user_console.print(f"[red]✗ {e}[/red]")
            user_console.flush()
            exit(1)
        except Exception as e:
            user_console.print(f"[red]✗ Failed to load context file:[/red] {e}")
            user_console.flush()
            exit(1)

    # Build the agent using AgentBuilder
    try:
        root_agent = build_agent_from_config(
            cli_config=args,
            tools=in_tools,
            skills=loaded_skills,
            root_agent_descriptions=root_agent_descs,
            context=loaded_context,
        )
    except AgentBuilderError as e:
        user_console.print(f"[red]error:[/red] {e}")
        exit(1)

    # Set active context for hooks (works in all modes: REPL, pipeline, with/without server)
    hooks_set_active_context(root_agent.context)

    # Fire SESSION_START hook after root agent is built
    global _root_agent_for_cleanup
    _root_agent_for_cleanup = root_agent
    HookExecutor.run_context_hooks(
        HookType.SESSION_START,
        root_agent=root_agent,
    )

    # If we loaded a context, show a summary
    if loaded_context is not None:
        msg_count = len(loaded_context.context)
        roles = {}
        for msg in loaded_context.context:
            role = msg.get("role", "unknown")
            roles[role] = roles.get(role, 0) + 1
        role_summary = ", ".join([f"{count} {role}" for role, count in roles.items()])
        user_console.print(
            f"[blue]Context loaded:[/blue] {msg_count} messages ({role_summary})"
        )

    # Set console quiet mode based on --show-log flag
    setup_console_logging(args)

    # Create command checker now that root_agent is available
    cmd_checker = SlashCommandChecker(root_agent)

    if args.prompt is not None:
        # Pipeline mode — no REPL, single shot, print response and exit
        if root_agent.agent_has_first_initiative:
            root_agent.process(settings.wake_up_message)
        # Pipeline mode preamble — skip if context already has one (e.g. loaded via --load-ctx / --last-ctx)
        pipeline_note = "[System note: Running in pipeline mode"
        if not any(
            pipeline_note in msg.get("content", "") for msg in root_agent.context()
        ):
            pipeline_preamble = (
                "\n---\n"
                "[System note: Running in pipeline mode. Single invocation, non-interactive. "
                "Do not ask follow-up questions. Be concise and direct.]\n"
                "---\n"
            )
            root_agent.context.append({"role": ROLE_USER, "content": pipeline_preamble})
        # Now the user's actual prompt
        result = root_agent.process(args.prompt)
        result = strip_thinking_content(result)
        sys.stdout.write(result)
        sys.stdout.flush()
        exit(0)

    if args.server_mode:
        session = ChatSession(root_agent=root_agent, cmd_checker=cmd_checker)
        setup_server(root_agent=root_agent)
        server_port = (
            args.server_port if args.server_port is not None else settings.server_port
        )
        set_server_port(server_port)
        set_server_input_queue(session.input_queue)
        set_server_active_session(session)
        set_interaction_provider(ServerInteractionProvider())
        set_verification_provider(ServerVerificationProvider())
        flask_thread = threading.Thread(
            target=lambda: run_server(port=server_port, no_chat=args.no_chat), daemon=True
        )

        session.start()
        flask_thread.start()

        shutdown_requested.wait()
        session.stop()
        sys.exit(0)
        return

    # Start the web server in background (unless --no-server)
    if not args.no_server:
        setup_server(root_agent)
        actual_port = start_server_in_background()
        user_console.print(
            f"[dim]Web server started on http://{settings.server_host}:{actual_port}[/dim]"
        )
        user_console.print("[dim]Use --no-server to disable.[/dim]")

    # Start the REPL
    repl = Repl(
        root_agent=root_agent,
        prompt_session=prompt_session,
        cmd_checker=cmd_checker,
    )
    repl.run()


if __name__ == "__main__":
    main()
