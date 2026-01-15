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

from wichy.agents import SUB_AGENTS
from wichy.agents.root_agent import RootAgent
from wichy.agents.sub_agent import console_sub_agents
from wichy.artifact import console as console_artifacts
from wichy.artifact import instantiate_artifact_tools_with_current_session_id
from wichy.helpers.console import console
from wichy.helpers.string import strip_thinking_content
from wichy.slash_commands import (
    ContextResetException,
    SlashCommandChecker,
    slash_completer,
)
from wichy.tools import ALL_TOOLS_UNINSTANTIATED
from wichy.tools.base import BaseTool, console_tool_result


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
            "--log-artifacts",
            action="store_true",
            help="Show activity related to artifacts during execution, requires --show-log",
        )
        self.parser.add_argument(
            "--bash-allow-all",
            action="store_true",
            help="Allow direct execution of bash commands without human authorization.",
        )
        self.parser.add_argument(
            "--model-str",
            # default="ollama/ministral-3:3b",
            default="ollama/hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
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

    in_tools.extend(instantiate_artifact_tools_with_current_session_id())
    in_tools.extend(SUB_AGENTS)

    in_tools.sort(key=lambda t: t.name)

    if args.list_tools:
        msg = "# Tools Available\n"
        for tool in in_tools:
            msg += "- **" + tool.name + "**: " + tool.description + "\n"

        print(Markdown(msg))
        exit(0)

    if args.tools.strip() != "":
        # only subset of tools allowed
        allowed_tools = args.tools.lower().split(",")
        allowed_tools: list[str] = [t.strip() for t in allowed_tools]

        new_tools = []
        for tool in in_tools:
            if tool.name in allowed_tools:
                new_tools.append(tool)

        in_tools = new_tools

    if args.not_tools.strip() != "":
        drop_tools = args.not_tools.lower().split(",")
        drop_tools: list[str] = [t.strip() for t in drop_tools]

        new_tools = []
        for tool in in_tools:
            if tool.name in drop_tools:
                continue
            new_tools.append(tool)

        in_tools = new_tools

    # print(f"{args.bash_allow_all=}")

    root_agent = RootAgent(model_str=args.model_str, tools=in_tools)

    root_agent.context.append(
        {
            "role": "system",
            "content": "You are a helpful assistant. Whenever possible, defer tasks to available agents. Agents DO NOT retain memory between requests to them, even if you call the same agent twice. Current year is "
            + str(datetime.date.today().year)
            + ". /think",
        }
    )

    # Set console quiet mode based on --show-log flag
    if args.show_log:
        console.quiet = False
        if args.log_tools:
            console_tool_result.quiet = False
        if args.log_agents:
            console_sub_agents.quiet = False
        if args.log_artifacts:
            console_artifacts.quiet = False
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
