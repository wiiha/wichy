"""CLI parser - extracts argument parsing from __main__.py."""

import argparse
from dataclasses import dataclass
from typing import Optional


@dataclass
class CliConfig:
    """Typed configuration from CLI arguments."""

    # Global flags
    show_log: bool = False
    list_tools: bool = False
    log_tools: bool = False
    log_agents: bool = False
    model_str: str = ""
    tools: str = ""
    not_tools: str = ""
    root_agent_description: str = "root-agent-code-advanced"
    load_ctx: Optional[str] = None
    last_ctx: bool = False
    no_server: bool = False
    user_first: bool = False
    auto_compact_threshold: Optional[int] = None

    # Subcommands
    command: Optional[str] = None

    # Subcommand: ls
    ls_command: Optional[str] = None

    # Subcommand: new (skill)
    new_command: Optional[str] = None
    new_skill_name: Optional[str] = None
    new_skill_with_script: bool = False

    # Subcommand: ra
    ra_template: bool = False


class CliParser:
    """Standalone CLI argument parser."""

    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description="Agentic LLM - An interactive command-line interface for an agentic LLM that can perform tasks using available tools."
        )
        self._add_global_arguments()
        self._add_subcommands()

    def _add_global_arguments(self):
        """Add global CLI arguments."""
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
        self.parser.add_argument(
            "-m",
            "--model-str",
            default="",
            help="Specify the model string. Format: <backend>/<model> for ollama/llama_cpp/open_router, or generic/<host>[:<port>]##<model> for OpenAI-compatible backends.",
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
                + " This filtering happens after --tools, i.e. --tools read_file, bash --not-tools bash -> tools = [read_file]. "
                + "See --list-tools for all tools. Omitting this flag will give the agent access to all tools. Unless --tools is specified."
            ),
        )
        self.parser.add_argument(
            "-r",
            "--root-agent-description",
            default="root-agent-basic",
            help="Specify which root agent description to use.",
        )
        self.parser.add_argument(
            "--load-ctx",
            type=str,
            help="Path to a context JSON file to resume a previous conversation.",
        )
        self.parser.add_argument(
            "--last-ctx",
            action="store_true",
            help="Load the most recently saved conversation context.",
        )
        self.parser.add_argument(
            "--no-server",
            action="store_true",
            help="Do not start the web server (graph editor, etc.)",
        )
        self.parser.add_argument(
            "--first",
            action="store_true",
            help='Let the user have the first turn. Default is that agent receives a "wake up" message, thereby getting first initiative.',
        )
        self.parser.add_argument(
            "--auto-compact",
            type=int,
            dest="auto_compact_threshold",
            help="Auto-compact context when it exceeds this number of tokens",
        )

    def _add_subcommands(self):
        """Add subcommands."""
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
        ls_subparsers.add_parser("ra", help="List available root agent descriptions")
        ls_subparsers.add_parser("tools", help="List available tools")
        ls_subparsers.add_parser(
            "ctx", help="List previous contexts in closest .wichy folder"
        )
        ls_subparsers.add_parser(
            "skills", help="List available skills in ~/.wichy/skills/"
        )

    def parse(self, args=None) -> CliConfig:
        """
        Parse command line arguments.

        Args:
            args: Optional list of arguments (for testing). Defaults to sys.argv.

        Returns:
            CliConfig object with parsed configuration.
        """
        parsed = self.parser.parse_args(args)

        config = CliConfig(
            show_log=parsed.show_log,
            list_tools=parsed.list_tools,
            log_tools=parsed.log_tools,
            log_agents=parsed.log_agents,
            model_str=parsed.model_str,
            tools=parsed.tools,
            not_tools=parsed.not_tools,
            root_agent_description=parsed.root_agent_description,
            load_ctx=parsed.load_ctx,
            last_ctx=parsed.last_ctx,
            no_server=parsed.no_server,
            command=parsed.command,
            ls_command=getattr(parsed, "ls_command", None),
            new_command=getattr(parsed, "new_command", None),
            ra_template=getattr(parsed, "template", False),
            user_first=parsed.first,
            auto_compact_threshold=getattr(parsed, "auto_compact_threshold", None),
        )

        # Extract new skill details if applicable
        if parsed.command == "new" and parsed.new_command == "skill":
            config.new_skill_name = parsed.name
            config.new_skill_with_script = parsed.with_script

        return config

    def print_usage(self):
        """Print usage information."""
        self.parser.print_usage()
