from dotenv import load_dotenv

load_dotenv()

import json
import datetime
from rich import print
from rich.markdown import Markdown
from wichy.helpers.console import console
from wichy.helpers.context import new_context
from wichy.helpers.string import strip_thinking_content
from wichy.slash_commands import SlashCommandChecker
from wichy.tools import ALL_TOOLS, get_tool_definitions
from wichy.tools.base import console_tool_result
from wichy.agents.sub_agent import console_sub_agents
from wichy.agents import (
    code_agent,
    web_research_agent,
    web_research_agent_lite,
    code_planner_agent,
)
from wichy.llm_backend import called_tool, Message, call
import argparse

TOOLS = ALL_TOOLS
TOOLS.extend(
    [code_planner_agent, code_agent, web_research_agent, web_research_agent_lite]
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
        self.args = None

    def parse_args(self):
        self.args = self.parser.parse_args()
        return self.args


class RootAgent:
    def __init__(self, model_name, tools):
        self.context = new_context()
        self.model_name = model_name
        self.tools = tools

    def tool_call(self, tools, item: called_tool):
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)
        console.log({"tool": name, "args": args})

        # Inject extra argument if name has 'agent-' prefix
        if name.startswith("agent-"):
            args["model_name"] = self.model_name

        for tool in tools:
            if name == tool.name:
                result = tool.validate_and_execute(**args)

        if result is None:
            result = "There is no tool called " + item.function.name + "."
        return {"role": "tool", "tool_call_id": item.id, "content": result}

    def handle_tools(self, tools, response: Message):
        if response.finish_reason != "tool_calls":
            return False

        if strip_thinking_content(response.content) != "":
            result = "\n---\n\n### Assistant\n" + strip_thinking_content(
                response.content
            )
            markdown = Markdown(result)
            print(markdown)

        self.context.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [t.model_dump() for t in response.tool_calls],
            }
        )

        console.log(
            "[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]"
        )
        osz = len(self.context)
        for item in response.tool_calls:
            self.context.append(self.tool_call(tools, item))
        return len(self.context) != osz

    def process(self, line):

        self.context.append({"role": "user", "content": line})
        tool_defs = get_tool_definitions(self.tools)
        response = call(
            context=self.context(), tool_defs=tool_defs, model_name=self.model_name
        )

        while self.handle_tools(self.tools, response):
            response = call(self.context(), tool_defs, model_name=self.model_name)
        self.context.append({"role": "assistant", "content": response.content})
        return response.content


def main():

    root_agent = RootAgent(
        model_name="hf.co/unsloth/Qwen3-8B-GGUF:UD-Q4_K_XL", tools=TOOLS
    )

    root_agent.context.append(
        {
            "role": "system",
            "content": "You are a helpful assistant. Whenever possible, defer tasks to available agents. Current year is "
            + str(datetime.date.today().year)
            + ". /think",
        }
    )
    parser = ArgumentParserWrapper()
    args = parser.parse_args()
    cmd_checker = SlashCommandChecker()

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
            line = input("> ")
            possible_cmd = cmd_checker.check_command(line)
            if possible_cmd != None:
                print(possible_cmd)
                continue
            result = root_agent.process(line)
            result = strip_thinking_content(result)
            result = "\n---\n\n### Assistant\n" + result
            markdown = Markdown(result)
            print(markdown)
        except KeyboardInterrupt:
            continue
        except EOFError:
            print("\nexiting...")
            exit(0)


if __name__ == "__main__":
    main()
