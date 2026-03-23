import re
import sys

from dotenv import load_dotenv

load_dotenv()


import json

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich.markdown import Markdown

from wichy.agent_builder import AgentBuilderError, build_agent_from_config
from wichy.cli_parser import CliParser
from wichy.config import settings
from wichy.console import user_console
from wichy.constants import ROLE_ASSISTANT, ROLE_USER
from wichy.context.handler import context_from_file, latest_context_file, previous_conversations
from wichy.helpers.console import console
from wichy.helpers.string import truncate_to_len
from wichy.repl import Repl
from wichy.root_agent import ALL_ROOT_AGENT_DESC
from wichy.root_agent.helpers import parse_root_agent_markdown_desc
from wichy.root_agent.root_agent_desc_template import root_agent_desc_template
from wichy.server_controller import ServerController
from wichy.skills import SkillLoader
from wichy.skills.skill_template import skill_template
from wichy.slash_commands import SlashCommandChecker, slash_completer
from wichy.tool_manager import ToolManager
from wichy.tools.base import console_tool_result
from wichy.tools.task import console_task_agents


def handle_list_root_agents():
    """Handle 'ls ra' command - list available root agents."""
    msg = "# Root Agents Available\n"
    for rad in ALL_ROOT_AGENT_DESC:
        ra = parse_root_agent_markdown_desc(rad)
        msg += (
            "- **"
            + ra.props.get("name", "WARN missing name prop")
            + "**: "
            + ra.props.get("description", "No description")
            + "\n"
        )
        for prop in ra.props:
            v = ra.props[prop]
            if prop in ["name", "description"]:
                continue
            msg += "\t- **" + prop + "**: " + v + "\n"

    user_console.print(Markdown(msg))


def handle_list_contexts():
    """Handle 'ls ctx' command - list conversation contexts."""
    context_dir = settings.contexts_dir
    try:
        files = previous_conversations()
    except FileNotFoundError:
        user_console.print("[yellow]No conversation contexts found.[/yellow]")
        return

    if len(files) == 0:
        user_console.print("[yellow]No conversation contexts found.[/yellow]")
        return

    msg = "# Conversation Contexts\n\n"
    files = sorted(files)
    file_max_lim = 10
    if len(files) > file_max_lim:
        msg += f"Listing {file_max_lim} of {len(files)} contexts.\n\n"
    files = files[-file_max_lim:]
    for f in sorted(files):

        # Count messages in the file
        try:
            with open(context_dir / f, "r") as file:
                lines = [line.strip() for line in file if line.strip()]
                msg_count = len(lines)

                # Get first user message and last assistant message if available
                first_user = None
                last_assistant = None
                for line in lines:
                    try:
                        data = json.loads(line)
                        if data.get("role") == ROLE_USER and first_user is None:
                            first_user = truncate_to_len(
                                data.get("content", ""), suffix="..."
                            )
                        if data.get("role") == ROLE_ASSISTANT:
                            last_assistant = truncate_to_len(
                                data.get("content", ""), suffix="..."
                            )
                    except:
                        pass

                preview = ""
                if first_user:
                    preview += f"First: *{first_user}*"
                if last_assistant:
                    if preview:
                        preview += " | "
                    preview += f"Last: *{last_assistant}*"

                msg += f"- **{f}**\n\t- Messages: {msg_count}\n"
                if preview:
                    msg += f"\t- Preview: {preview}\n"
                msg += "\n\n"
        except Exception as e:
            msg += f"- **{f}**\n\t- Error reading file: {e}\n"

    user_console.print(Markdown(msg))


def handle_list_tools(tools):
    """Handle 'ls tools' command - list available tools."""
    msg = "# Tools Available\n"
    for tool in tools:
        msg += "- **" + tool.name + "**: " + tool.description + "\n"

    user_console.print(Markdown(msg))


def handle_list_skills():
    """Handle 'ls skills' command - list available skills."""
    skill_loader = SkillLoader()
    skills = skill_loader.load_all_skills()

    if not skills:
        user_console.print("[yellow]No skills found in ~/.wichy/skills/[/yellow]")
        user_console.print(
            "[dim]Create a skill by adding a directory with a skill.md file[/dim]"
        )
        return

    msg = "# Skills Available\n\n"
    for skill_name, skill in skills.items():
        msg += f"- **{skill_name}**: {skill.description}\n"
        if skill.tags:
            msg += f"\t- Tags: {', '.join(skill.tags)}\n"
        if skill.scripts:
            msg += f"\t- Scripts: {len(skill.scripts)}\n"
            for s in skill.scripts:
                msg += f"\t\t- {s.name}\n"

    user_console.print(Markdown(msg))


def handle_new_skill(args):
    """Handle 'new skill' command - create a new skill directory structure."""
    skill_name = args.new_skill_name
    skills_dir = settings.skills_dir
    skill_dir = skills_dir / skill_name

    # Validate skill name (kebab-case: lowercase letters, numbers, and hyphens)
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", skill_name):
        user_console.print(
            "[red]error:[/red] Skill name must be kebab-case: lowercase letters, numbers, and hyphens (e.g., 'my-skill-name')",
        )
        exit(1)

    # Check if skill already exists
    if skill_dir.exists():
        user_console.print(
            f"[red]error:[/red] Skill '{skill_name}' already exists at {skill_dir}",
        )
        exit(1)

    # Create skill directory
    skill_dir.mkdir(parents=True, exist_ok=False)

    # Create skill.md
    skill_md_content = skill_template.format(skill_name=skill_name)
    skill_md_path = skill_dir / "skill.md"
    with open(skill_md_path, "w", encoding="utf-8") as f:
        f.write(skill_md_content)

    # Create optional directories
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "assets").mkdir(exist_ok=True)

    msg = f"[green]Created skill:[/green] {skill_name}\n"
    msg += f"[dim]Location: {skill_dir}[/dim]\n\n"
    msg += "Files created:\n"
    msg += "  - skill.md (skill knowledge and documentation)\n"
    msg += "  - references/ (optional documentation)\n"
    msg += "  - assets/ (optional templates, etc.)\n"

    # Optionally create scripts directory with placeholder
    if args.new_skill_with_script:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=False)
        placeholder_script = scripts_dir / "example.sh"
        script_content = f"""#!/bin/bash

# Example script for the {skill_name} skill
# Modify or replace this with your own scripts

echo "Hello from {skill_name} skill!"
"""
        with open(placeholder_script, "w", encoding="utf-8") as f:
            f.write(script_content)
        placeholder_script.chmod(0o755)
        msg += "  - scripts/example.sh (placeholder executable script)\n"

    msg += "\n[dim]Edit skill.md to add your knowledge and documentation.[/dim]"
    msg += "\n[dim]Add reference docs to references/, templates to assets/.[/dim]"
    if args.new_skill_with_script:
        msg += "\n[dim]Add executable scripts to scripts/ directory. Mark safe scripts in skill.md frontmatter.[/dim]"

    user_console.print(msg)


def handle_ls_commands(args, tool_manager):
    """Handle all 'ls' subcommands. Returns True if handled, False otherwise."""
    if args.command != "ls":
        return False

    if args.ls_command == "ra":
        handle_list_root_agents()
        exit(0)

    if args.ls_command == "ctx" or str(args.ls_command).startswith("context"):
        handle_list_contexts()
        exit(0)

    if args.ls_command == "tools":
        tools = tool_manager.instantiate_all()
        handle_list_tools(tools)
        exit(0)

    if args.ls_command == "skills":
        handle_list_skills()
        exit(0)

    # Unknown ls subcommand
    return False


def handle_new_commands(args):
    """Handle all 'new' subcommands. Returns True if handled, False otherwise."""
    if args.command != "new":
        return False

    if args.new_command == "skill":
        handle_new_skill(args)
        exit(0)

    return False


def handle_ra_template(args):
    """Handle 'ra --template' command."""
    if args.command == "ra" and args.ra_template:
        sys.stdout.write(root_agent_desc_template)
        sys.stdout.flush()
        exit(0)
    return False


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


def start_server(root_agent):
    """Start the web server and return the controller."""
    server_controller = ServerController(port=7891)
    actual_port = server_controller.start()
    user_console.print(
        f"[dim]Web server started on http://127.0.0.1:{actual_port}[/dim]"
    )

    # Set active context for context editor if server is enabled
    try:
        from wichy.tools.context_editor import api as context_editor_api

        context_editor_api.set_active_context(root_agent.context)
    except Exception as e:
        user_console.print(
            f"[yellow]Warning: Could not set active context for web editor: {e}[/yellow]"
        )
    user_console.print("[dim]Use --no-server to disable.[/dim]")

    return server_controller


def main():
    parser = CliParser()
    args = parser.parse()
    cmd_checker = SlashCommandChecker()

    prompt_session = PromptSession(
        history=FileHistory(settings.history_file),
        auto_suggest=AutoSuggestFromHistory(),
        completer=slash_completer,
    )

    # Tools will be determined after root agent selection
    # (tools depend on agent-desc tools property + CLI flags)
    tool_manager = ToolManager()

    # Install default skills and load all skills
    loaded_skills = initialize_skills()

    # Handle subcommands that exit early
    handle_ra_template(args)

    # Handle ls commands
    if not handle_ls_commands(args, tool_manager):
        # Handle new commands
        if not handle_new_commands(args):
            # At this point if ls/ra/new is specified without a subcommand, something is wrong.
            if args.command in ("ls", "ra", "new"):
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

    # Load context from file if specified (before building agent)
    loaded_context = None
    if args.load_ctx and args.last_ctx:
        user_console.print("[red]error:[/red] --load-ctx and --last-ctx are mutually exclusive")
        exit(1)

    if args.load_ctx:
        try:
            loaded_context = context_from_file(args.load_ctx)
            user_console.print(
                f"[green]✓ Loaded conversation context from:[/green] {args.load_ctx}"
            )
        except Exception as e:
            user_console.print(f"[red]✗ Failed to load context file:[/red] {e}")
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
            exit(1)
        except Exception as e:
            user_console.print(f"[red]✗ Failed to load context file:[/red] {e}")
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

    # Start the web server in background (unless --no-server)
    if not args.no_server:
        start_server(root_agent)

    # Start the REPL
    repl = Repl(
        root_agent=root_agent,
        prompt_session=prompt_session,
        cmd_checker=cmd_checker,
    )
    repl.run()


if __name__ == "__main__":
    main()
