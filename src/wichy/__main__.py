import sys

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

from wichy.agents.root_agent import RootAgent
from wichy.agents.root_agent_desc_basic import root_agent_desc
from wichy.agents.root_agent_desc_code_advanced import root_agent_desc_code_advanced
from wichy.agents.task_agent import TaskAgentTool
from wichy.helpers.console import console
from wichy.helpers.environment_info import environment_information
from wichy.helpers.markdown import read_markdown_with_frontmatter
from wichy.helpers.prompt import preprocess_prompt
from wichy.helpers.string import strip_thinking_content
from wichy.slash_commands import (
    ContextResetException,
    SlashCommandChecker,
    slash_completer,
)
from wichy.tools import ALL_TOOLS_UNINSTANTIATED
from wichy.tools.base import BaseTool, console_tool_result

ALL_TOOLS_UNINSTANTIATED.append(TaskAgentTool)


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
        self.parser.add_argument(
            "--bash-allow-all",
            action="store_true",
            help="Allow direct execution of bash commands without human authorization.",
        )
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
    for tool in ALL_TOOLS_UNINSTANTIATED:
        in_tools.append(tool())

    in_tools.sort(key=lambda t: t.name)

    if args.list_tools:
        msg = "# Tools Available\n"
        for tool in in_tools:
            msg += "- **" + tool.name + "**: " + tool.description + "\n"

        print(Markdown(msg))
        exit(0)

    # load root agent description

    root_agent_props, system_prompt = read_markdown_with_frontmatter(
        root_agent_desc_code_advanced
    )

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

    root_agent = RootAgent(
        model_str=model_str, tools=tools_for_agent, name=root_agent_props.get("name")
    )

    verify_against = {"tools": [x.name for x in tools_for_agent]}

    system_prompt = preprocess_prompt(
        prompt=system_prompt, verify_against=verify_against
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

    # Set console quiet mode based on --show-log flag
    if args.show_log:
        console.quiet = False
        if args.log_tools:
            console_tool_result.quiet = False
    else:
        console.quiet = True

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

        except KeyboardInterrupt:
            continue
        except EOFError:
            print("\nexiting...")
            exit(0)


if __name__ == "__main__":
    main()
