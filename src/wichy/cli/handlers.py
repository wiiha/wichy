"""CLI command handlers for listing, creating, and installing wichy resources."""

import json
import re
import sys
from pathlib import Path

from rich.markdown import Markdown

from wichy.config import settings
from wichy.console import user_console
from wichy.constants import ROLE_ASSISTANT, ROLE_USER
from wichy.context.handler import previous_conversations
from wichy.helpers.string import truncate_to_len
from wichy.hooks import DEFAULT_HOOKS_TEMPLATE
from wichy.root_agent import ALL_ROOT_AGENT_DESC
from wichy.root_agent.helpers import parse_root_agent_markdown_desc
from wichy.root_agent.root_agent_desc_template import root_agent_desc_template
from wichy.skills import SkillLoader
from wichy.skills.skill_template import skill_template


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
                    except Exception:
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
        inactive_marker = " [dim]\\[inactive][/dim]" if skill.inactive else ""
        msg += f"- **{skill_name}**{inactive_marker}: {skill.description}\n"
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
        user_console.flush()
        exit(1)

    # Check if skill already exists
    if skill_dir.exists():
        user_console.print(
            f"[red]error:[/red] Skill '{skill_name}' already exists at {skill_dir}",
        )
        user_console.flush()
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


def handle_install_hooks(args):
    """Handle 'install hooks' command - create default hooks file."""
    hooks_dir = Path(".wichy")
    hooks_file = hooks_dir / "hooks.py"

    # Check if file already exists
    if hooks_file.exists():
        if args.install_force:
            # Overwrite existing file
            hooks_file.write_text(DEFAULT_HOOKS_TEMPLATE)
            user_console.print(f"[green]Overwritten:[/green] {hooks_file}")
            return True
        else:
            user_console.print(
                f"[yellow]Hooks file already exists at {hooks_file}[/yellow]"
            )
            user_console.print(
                "[yellow]Use 'wichy install hooks --force' to overwrite[/yellow]"
            )
            return False

    # Create directory and file
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hooks_file.write_text(DEFAULT_HOOKS_TEMPLATE)
        user_console.print(f"[green]Created:[/green] {hooks_file}")
        user_console.print(
            "[dim]Edit this file to customize tool execution behavior.[/dim]"
        )
        return True
    except PermissionError as e:
        user_console.print(f"[red]Error:[/red] Permission denied: {e}")
        return False
    except Exception as e:
        user_console.print(f"[red]Error:[/red] {e}")
        return False


# Router functions that dispatch to the handlers above


def handle_ls_commands(args, tool_manager):
    """Handle all 'ls' subcommands. Returns True if handled, False otherwise."""
    if args.command != "ls":
        return False

    if args.ls_command == "ra":
        handle_list_root_agents()
        user_console.flush()
        exit(0)

    if args.ls_command == "ctx" or str(args.ls_command).startswith("context"):
        handle_list_contexts()
        user_console.flush()
        exit(0)

    if args.ls_command == "tools":
        tools = tool_manager.instantiate_all()
        handle_list_tools(tools)
        user_console.flush()
        exit(0)

    if args.ls_command == "skills":
        handle_list_skills()
        user_console.flush()
        exit(0)

    # Unknown ls subcommand
    return False


def handle_new_commands(args):
    """Handle all 'new' subcommands. Returns True if handled, False otherwise."""
    if args.command != "new":
        return False

    if args.new_command == "skill":
        handle_new_skill(args)
        user_console.flush()
        exit(0)

    return False


def handle_ra_template(args):
    """Handle 'ra --template' command."""
    if args.command == "ra" and args.ra_template:
        sys.stdout.write(root_agent_desc_template)
        sys.stdout.flush()
        exit(0)
    return False


def handle_install_commands(args):
    """Handle all 'install' subcommands. Returns True if handled, False otherwise."""
    if args.command != "install":
        return False

    if args.install_command == "hooks":
        handle_install_hooks(args)
        user_console.flush()
        exit(0)

    return False
