from dotenv import load_dotenv

load_dotenv()

import datetime
from rich import print
from rich.markdown import Markdown
from wichy.helpers.console import console
from wichy.helpers.string import strip_thinking_content
from wichy.slash_commands import (
    SlashCommandChecker,
    slash_completer,
    ContextResetException,
    ContextResetStrategies,
)
from wichy.tools import ALL_TOOLS
from wichy.tools.base import console_tool_result
from wichy.agents.sub_agent import console_sub_agents
from wichy.agents.root_agent import RootAgent
from wichy.agents import (
    code_reviewer_agent,
    code_implementer_agent,
    web_research_agent,
    web_research_agent_lite,
    code_planner_agent,
)
import argparse
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from pathlib import Path
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory


TOOLS = ALL_TOOLS
TOOLS.extend(
    [
        code_planner_agent,
        code_implementer_agent,
        code_reviewer_agent,
        web_research_agent,
        web_research_agent_lite,
    ]
)


class ArgumentParserWrapper:
    def __init__(self):
        self.parser = argparse.ArgumentParser(
            description="Agentic LLM - An interactive command-line interface for an agentic LLM that can perform tasks using available tools."
        )
        self.parser.add_argument(
            "--show-log", action="store_true", help="Show logs during execution"
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
            "--model-str",
            default="ollama/ministral-3:3b",
            help="Specify the model string (format: <backend>/<model>)",
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

    # print(f"{args.bash_allow_all=}")

    root_agent = RootAgent(model_str=args.model_str, tools=TOOLS)

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
            result = root_agent.process(line)
            result = strip_thinking_content(result)
            result = "\n---\n\n### Assistant\n" + result
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
