import re
import sys
from typing import Dict

from dotenv import load_dotenv

load_dotenv()

import argparse
import datetime
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory
from rich import print
from rich.markdown import Markdown

from wichy.agent_builder import AgentBuilder, AgentBuilderError, build_agent_from_config
from wichy.cli_parser import CliParser
from wichy.config import settings
from wichy.helpers.console import console
from wichy.helpers.context import context_from_file, new_context
from wichy.helpers.environment_info import environment_information
from wichy.helpers.prompt import preprocess_prompt
from wichy.helpers.string import strip_thinking_content, truncate_to_len
from wichy.llm_backend import LLMBackendContextLimitReached
from wichy.repl import Repl
from wichy.root_agent import ALL_ROOT_AGENT_DESC
from wichy.root_agent.helpers import ParsedRootAgentDesc, parse_root_agent_markdown_desc
from wichy.root_agent.root_agent import RootAgent
from wichy.root_agent.root_agent_desc_template import root_agent_desc_template
from wichy.server_controller import ServerController
from wichy.skills import SkillLoader
from wichy.skills.skill_template import skill_template
from wichy.skills.skills_info import skills_information
from wichy.slash_commands import (
    ContextDropException,
    ContextResetException,
    SlashCommandChecker,
    slash_completer,
)
from wichy.tool_manager import ToolManager
from wichy.tools import ALL_TOOLS_NOT_INSTANTIATED
from wichy.tools.base import BaseTool, console_tool_result
from wichy.tools.task import console_task_agents


def main():
    parser = CliParser()
    args = parser.parse()
    cmd_checker = SlashCommandChecker()

    prompt_session = PromptSession(
        history=FileHistory(settings.history_file),
        auto_suggest=AutoSuggestFromHistory(),
        completer=slash_completer,
    )

    # instantiate tools
    in_tools: list[BaseTool] = []
    for tool in ALL_TOOLS_NOT_INSTANTIATED:
        in_tools.append(tool())

    in_tools.sort(key=lambda t: t.name)

    # Install default skills and load all skills
    skill_loader = SkillLoader()
    installed = skill_loader.install_default_skills()
    if installed > 0:
        print(f"[dim]Installed {installed} default skill(s)[/dim]")
    loaded_skills = skill_loader.load_all_skills()

    # Handle subcommands that exit early
    if args.command == "ls" and args.ls_command == "ra":
        # This means "ls root agents" was called
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

        print(Markdown(msg))
        exit(0)
    elif args.command == "ls" and (
        args.ls_command == "ctx" or str(args.ls_command).startswith("context")
    ):
        # This means "ls ctx" or "ls contexts" was called
        import json

        from wichy.helpers.context import previous_conversations

        context_dir = settings.contexts_dir
        try:
            files = previous_conversations()
        except FileNotFoundError:
            print("[yellow]No conversation contexts found.[/yellow]")
            exit(0)

        if len(files) == 0:
            print("[yellow]No conversation contexts found.[/yellow]")
            exit(0)

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
                            if data.get("role") == "user" and first_user is None:
                                first_user = truncate_to_len(
                                    data.get("content", ""), suffix="..."
                                )
                            if data.get("role") == "assistant":
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

        print(Markdown(msg))
        exit(0)

    if args.list_tools or args.command == "ls" and args.ls_command == "tools":
        msg = "# Tools Available\n"
        for tool in in_tools:
            msg += "- **" + tool.name + "**: " + tool.description + "\n"

        print(Markdown(msg))
        exit(0)

    if args.command == "ls" and args.ls_command == "skills":
        # Load skills and display them
        skill_loader = SkillLoader()
        skills = skill_loader.load_all_skills()

        if not skills:
            print("[yellow]No skills found in ~/.wichy/skills/[/yellow]")
            print(
                "[dim]Create a skill by adding a directory with a skill.md file[/dim]"
            )
            exit(0)

        msg = "# Skills Available\n\n"
        for skill_name, skill in skills.items():
            msg += f"- **{skill_name}**: {skill.description}\n"
            if skill.tags:
                msg += f"\t- Tags: {', '.join(skill.tags)}\n"
            if skill.scripts:
                msg += f"\t- Scripts: {len(skill.scripts)}\n"
                for s in skill.scripts:
                    msg += f"\t\t- {s.name}\n"

        print(Markdown(msg))
        exit(0)
    if args.command == "new" and args.new_command == "skill":
        # Create a new skill directory structure
        skill_name = args.new_skill_name
        skills_dir = settings.skills_dir
        skill_dir = skills_dir / skill_name

        # Validate skill name (kebab-case: lowercase letters, numbers, and hyphens)
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", skill_name):
            print(
                f"[red]error:[/red] Skill name must be kebab-case: lowercase letters, numbers, and hyphens (e.g., 'my-skill-name')",
                file=sys.stderr,
            )
            exit(1)

        # Check if skill already exists
        if skill_dir.exists():
            print(
                f"[red]error:[/red] Skill '{skill_name}' already exists at {skill_dir}",
                file=sys.stderr,
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
        msg += f"Files created:\n"
        msg += f"  - skill.md (skill knowledge and documentation)\n"
        msg += f"  - references/ (optional documentation)\n"
        msg += f"  - assets/ (optional templates, etc.)\n"

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
            msg += f"  - scripts/example.sh (placeholder executable script)\n"

        msg += f"\n[dim]Edit skill.md to add your knowledge and documentation.[/dim]"
        msg += f"\n[dim]Add reference docs to references/, templates to assets/.[/dim]"
        if args.new_skill_with_script:
            msg += f"\n[dim]Add executable scripts to scripts/ directory. Mark safe scripts in skill.md frontmatter.[/dim]"

        print(msg)
        exit(0)

    if args.command == "ra" and args.ra_template:
        sys.stdout.write(root_agent_desc_template)
        sys.stdout.flush()
        exit(0)

    if args.command in ("ls", "ra", "new"):
        # At this point if ls/ra/new is specified without a subcommand, something is wrong.
        parser.print_usage()
        exit(1)

    # Parse all root agent descriptions
    root_agent_descs = [
        parse_root_agent_markdown_desc(rad) for rad in ALL_ROOT_AGENT_DESC
    ]

    # Load context from file if specified (before building agent)
    loaded_context = None
    if args.load_ctx:
        try:
            loaded_context = context_from_file(args.load_ctx)
            print(f"[green]✓ Loaded conversation context from:[/green] {args.load_ctx}")
        except Exception as e:
            print(f"[red]✗ Failed to load context file:[/red] {e}")
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
        print(f"[red]error:[/red] {e}", file=sys.stderr)
        exit(1)

    # If we loaded a context, show a summary
    if loaded_context is not None:
        msg_count = len(loaded_context.context)
        roles = {}
        for msg in loaded_context.context:
            role = msg.get("role", "unknown")
            roles[role] = roles.get(role, 0) + 1
        role_summary = ", ".join([f"{count} {role}" for role, count in roles.items()])
        print(f"[blue]Context loaded:[/blue] {msg_count} messages ({role_summary})")
    # Set console quiet mode based on --show-log flag
    if args.show_log:
        console.quiet = False
        if args.log_tools:
            console_tool_result.quiet = False
        if args.log_agents:
            console_task_agents.quiet = False
    else:
        console.quiet = True

    # Start the web server in background (unless --no-server)
    server_controller = None
    if not args.no_server:
        server_controller = ServerController(port=7891)
        actual_port = server_controller.start()
        print(f"[dim]Web server started on http://127.0.0.1:{actual_port}[/dim]")
        print(f"[dim]Graph editor: http://127.0.0.1:{actual_port}/tools/graph/[/dim]")
        print("[dim]Use --no-server to disable.[/dim]")

    # Start the REPL
    repl = Repl(
        root_agent=root_agent,
        prompt_session=prompt_session,
        cmd_checker=cmd_checker,
    )
    repl.run()


if __name__ == "__main__":
    main()
