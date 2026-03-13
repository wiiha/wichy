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

from wichy.helpers.console import console
from wichy.helpers.context import context_from_file, new_context
from wichy.helpers.environment_info import environment_information
from wichy.helpers.prompt import preprocess_prompt
from wichy.helpers.string import strip_thinking_content, truncate_to_len
from wichy.llm_backend import LLMBackendContextLimitReached
from wichy.root_agent import ALL_ROOT_AGENT_DESC
from wichy.root_agent.helpers import ParsedRootAgentDesc, parse_root_agent_markdown_desc
from wichy.root_agent.root_agent import RootAgent
from wichy.root_agent.root_agent_desc_template import root_agent_desc_template
from wichy.skills import SkillLoader
from wichy.skills.skill_template import skill_template
from wichy.skills.skills_info import skills_information
from wichy.slash_commands import (
    ContextDropException,
    ContextResetException,
    SlashCommandChecker,
    slash_completer,
)
from wichy.tools import ALL_TOOLS_NOT_INSTANTIATED
from wichy.tools.base import BaseTool, console_tool_result
from wichy.tools.task import console_task_agents


class ArgumentParserWrapper:
    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description="Agentic LLM - An interactive command-line interface for an agentic LLM that can perform tasks using available tools."
        )
        self.parser.add_argument(
            "--show-log", action="store_true", help="Show logs during execution"
        )
        self.parser.add_argument(
            "--list-tools",
            action="store_true",
            help="Prints a list of all available tools.",
        )
        self.parser.add_argument(
            "--log-tools",
            action="store_true",
            help="Show tool results during execution, requires --show-log",
        )
        self.parser.add_argument(
            "--log-agents",
            action="store_true",
            help="Show agent results during execution, requires --show-log",
        )
        # self.parser.add_argument(
        #     "--bash-allow-all",
        #     action="store_true",
        #     help="Allow direct execution of bash commands without human authorization.",
        # )
        self.parser.add_argument(
            "-m",
            "--model-str",
            # default="ollama/ministral-3:3b",
            default="",
            help="Specify the model string (format: <backend>/<model>)",
        )
        self.parser.add_argument(
            "--tools",
            default="",
            help=(
                "Specify which tools the root agent should have available. Comma separated list of tool names. "
                + "See --list-tools for all tools. Omitting this flag will give the agent access to all tools. Unless --not-tools is specified."
            ),
        )
        self.parser.add_argument(
            "--not-tools",
            default="",
            help=(
                "Specify which tools the root agent should not have available. Comma separated list of tool names."
                + " This filtering happens after --tools, i.e. --tools cat, bash --not-tools bash -> tools = [cat]. "
                + "See --list-tools for all tools. Omitting this flag will give the agent access to all tools. Unless --tools is specified."
            ),
        )
        self.parser.add_argument(
            "-r",
            "--root-agent-description",
            default="root-agent-code-advanced",
            help="Specify which root agent description to use.",
        )
        self.parser.add_argument(
            "--load-ctx",
            type=str,
            help="Path to a context JSON file to resume a previous conversation.",
        )
        self.parser.add_argument(
            "--no-server",
            action="store_true",
            help="Do not start the web server (graph editor, etc.)",
        )
        # Add subcommands
        subparsers = self.parser.add_subparsers(
            dest="command", help="Available sub commands"
        )

        # root agent command
        ra_parser = subparsers.add_parser("ra", help="Root Agent related commands")
        ra_parser.add_argument(
            "-t",
            "--template",
            action="store_true",
            help="Print the root agent description template to stdout. Can be piped to file. Your own root agents live in (~/).wichy/root_agent_defs",
        )
        # new command
        new_parser = subparsers.add_parser("new", help="Create new resources")
        new_subparsers = new_parser.add_subparsers(
            dest="new_command", help="new subcommands"
        )
        new_skill_parser = new_subparsers.add_parser(
            "skill", help="Create a new skill in ~/.wichy/skills/"
        )
        new_skill_parser.add_argument(
            "-n",
            "--name",
            type=str,
            required=True,
            help="Name of the skill (will be used as directory name)",
        )
        new_skill_parser.add_argument(
            "--with-script",
            action="store_true",
            help="Also create a placeholder script in the skill's scripts/ directory",
        )
        # ls command
        ls_parser = subparsers.add_parser("ls", help="List things related to Wichy")
        ls_subparsers = ls_parser.add_subparsers(
            dest="ls_command", help="ls subcommands"
        )

        # TODO: Things to list
        # available root agent descriptions (ls ra or ls root agents)
        # available tools (ls tools)
        # previous contexts in closest .wichy folder (ls ctx or ls contexts)

        ls_subparsers.add_parser("ra", help="List available root agent descriptions")
        ls_subparsers.add_parser("tools", help="List available tools")
        ls_subparsers.add_parser(
            "ctx", help="List previous contexts in closest .wichy folder"
        )
        ls_subparsers.add_parser(
            "skills", help="List available skills in ~/.wichy/skills/"
        )
        self.args = None

    def parse_args(self):
        self.args = self.parser.parse_args()
        return self.args


def main():

    parser = ArgumentParserWrapper()
    args = parser.parse_args()
    cmd_checker = SlashCommandChecker()

    home_dir = Path.home()
    prompt_session = PromptSession(
        history=FileHistory(home_dir / Path(".wichy_history")),
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

    # Example of checking if a specific subcommand was called
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

        context_dir = ".wichy/contexts/"
        try:
            files = previous_conversations()
        except FileNotFoundError:
            print("[yellow]No conversation contexts found.[/yellow]")
            exit(0)

        if len(files) == 0:
            print("[yellow]No conversation contexts found.[/yellow]")
            exit(0)

        msg = "# Conversation Contexts\n\n"
        for f in sorted(files):

            # Count messages in the file
            try:
                with open(context_dir + f, "r") as file:
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
        skill_name = args.name
        skills_dir = Path.home() / ".wichy" / "skills"
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
        if args.with_script:
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
        if args.with_script:
            msg += f"\n[dim]Add executable scripts to scripts/ directory. Mark safe scripts in skill.md frontmatter.[/dim]"

        print(msg)
        exit(0)

    if args.command == "ra" and args.template:
        sys.stdout.write(root_agent_desc_template)
        sys.stdout.flush()
        exit(0)

    if args.command in ("ls", "ra", "new"):
        # At this point if ls/ra/new is specified without a subcommand, something is wrong.
        parser.parser.print_usage()
        exit(1)

    # load root agent description

    root_agents: Dict[str, ParsedRootAgentDesc] = {}
    for rad in ALL_ROOT_AGENT_DESC:
        ra = parse_root_agent_markdown_desc(rad)
        root_agents[ra.props["name"]] = ra

    selected_root_agent = root_agents.get(args.root_agent_description, None)

    if not selected_root_agent:
        print(
            f"[red]error:[/red] Specified root agent [bold]{args.root_agent_description}[/bold] does not exist",
            file=sys.stderr,
        )
        exit(1)

    root_agent_props = selected_root_agent.props
    system_prompt = selected_root_agent.system_prompt

    if system_prompt.strip() == "":
        print(
            "[red]error:[/red] Loaded root agent description did not contain a system prompt. It is required.",
            file=sys.stderr,
        )
        exit(1)

    model_str = root_agent_props.get("model") or root_agent_props.get("model_str") or ""

    if args.model_str != "":
        # Model string passed as flag overwrite model spec.
        model_str = args.model_str

    if model_str.strip() == "":
        print(
            "[red]error:[/red] No model specified, either specify in frontmatter or using --model-str",
            file=sys.stderr,
        )
        exit(1)

    tools_for_agent = in_tools

    if args.tools.strip() != "" or root_agent_props.get("tools") != None:
        # only subset of tools allowed
        allowed_tools_str = root_agent_props.get("tools", "")

        if args.tools.strip() != "":
            # CLI flag takes precedence over loaded description.
            allowed_tools_str = args.tools.strip()

        allowed_tools = allowed_tools_str.lower().split(",")
        allowed_tools: list[str] = [t.strip() for t in allowed_tools]

        new_tools = []
        for tool in in_tools:
            if tool.name in allowed_tools:
                new_tools.append(tool)

        tools_for_agent = new_tools

    if args.not_tools.strip() != "":
        drop_tools = args.not_tools.lower().split(",")
        drop_tools: list[str] = [t.strip() for t in drop_tools]

        new_tools = []
        for tool in tools_for_agent:
            if tool.name in drop_tools:
                continue
            new_tools.append(tool)

        tools_for_agent = new_tools

    # Load context from file if specified
    loaded_context = None
    if args.load_ctx:
        try:
            loaded_context = context_from_file(args.load_ctx)
            print(f"[green]✓ Loaded conversation context from:[/green] {args.load_ctx}")
        except Exception as e:
            print(f"[red]✗ Failed to load context file:[/red] {e}")
            exit(1)

    root_agent = RootAgent(
        model_str=model_str,
        tools=tools_for_agent,
        name=root_agent_props.get("name"),
        context=loaded_context,
        skills=loaded_skills,
    )

    # Only add system prompt if we didn't load a context (fresh conversation)
    if loaded_context is None:
        verify_against = {"tools": [x.name for x in tools_for_agent]}

        system_prompt = preprocess_prompt(
            prompt=system_prompt, verify_against=verify_against
        )

        # Add skills information before environment (if not disabled)
        include_skills = (
            root_agent_props.get("include_skills", "true").lower() != "false"
        )
        if include_skills:
            skills_info = skills_information()
            if skills_info:
                system_prompt += (
                    "\n\nYou have access to the following skills:\n"
                    + skills_info
                    + "\n"
                )

        if root_agent_props.get("include_env_info", "").lower() != "false":
            system_prompt += (
                "\n\nHere is useful information about the environment you are running in:\n"
                + environment_information()
                + "\n\n"
            )

        root_agent.context.append(
            {
                "role": "system",
                "content": system_prompt,
            }
        )

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
    if not args.no_server:
        from wichy.server import start_server_in_background

        actual_port = start_server_in_background(port=7891)
        print(f"[dim]Web server started on http://127.0.0.1:{actual_port}[/dim]")
        print(f"[dim]Graph editor: http://127.0.0.1:{actual_port}/tools/graph/[/dim]")
        print("[dim]Use --no-server to disable.[/dim]")

    while True:
        try:
            print(Markdown("\n\n---\n\n### User"))
            line = prompt_session.prompt("> ")
            possible_cmd = cmd_checker.check_command(line)
            if possible_cmd != None:
                print(possible_cmd)
                continue
            print(Markdown("---"))
            result = root_agent.process(line)
            result = strip_thinking_content(result)
            print(Markdown("\n---\n\n### Assistant\n"))
            markdown = Markdown(result)
            print(markdown)
        except ContextResetException as e:
            root_agent.reset_context(strategy=e.strategy)
            continue
        except ContextDropException:
            root_agent.drop_last_context_entry()
            continue
        except LLMBackendContextLimitReached as e:
            print(
                "[red bold]Error:[/red bold] "
                + str(e)
                + "\n[green bold]Tip:[/green bold] Try dropping some messages or summarizing using slash commands."
            )
            continue

        except KeyboardInterrupt:
            continue
        except EOFError:
            print("\nexiting...")
            exit(0)


if __name__ == "__main__":
    main()
