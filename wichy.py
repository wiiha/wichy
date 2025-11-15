import json
from rich import print
from rich.markdown import Markdown
from helpers.console import console
from helpers.context import new_context
from slash_commands import SlashCommandChecker
from tools import ALL_TOOLS, get_tool_definitions
from tools.base import console_tool_result
from llm_backend import called_tool, Message, call
import argparse
import os


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
        self.args = None

    def parse_args(self):
        self.args = self.parser.parse_args()
        return self.args


context = new_context()


def tool_call(tools, item: called_tool):
    result = None
    name = item.function.name
    args = json.loads(item.function.arguments)
    console.log({"tool": name, "args": args})
    for tool in tools:
        if name == tool.name:
            result = tool.validate_and_execute(**args)

    if result is None:
        result = "There is no tool called " + item.function.name + "."
    return {"role": "tool", "tool_call_id": item.id, "content": result}


def handle_tools(tools, response: Message):
    if response.finish_reason != "tool_calls":
        return False

    context.append(
        {
            "role": "assistent",
            "content": response.content,
            "tool_calls": [t.model_dump() for t in response.tool_calls],
        }
    )

    console.log("[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]")
    osz = len(context)
    for item in response.tool_calls:
        context.append(tool_call(tools, item))
    return len(context) != osz


def process(line):

    tools = ALL_TOOLS
    context.append({"role": "user", "content": line})
    tool_defs = get_tool_definitions(tools)
    response = call(context=context(), tool_defs=tool_defs)

    while handle_tools(tools, response):
        response = call(context(), tool_defs)
    context.append({"role": "assistant", "content": response.content})
    return response.content


def main():
    context.append({"role": "system", "content": "You are a helpful assistent."})
    parser = ArgumentParserWrapper()
    args = parser.parse_args()
    cmd_checker = SlashCommandChecker()

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
            line = input("> ")
            possible_cmd = cmd_checker.check_command(line)
            if possible_cmd != None:
                print(possible_cmd)
                continue
            result = process(line)
            result = "\n---\n\n### Assistant\n" + result
            markdown = Markdown(result)
            print(markdown)
            # print(f">> {result}\n")
        except KeyboardInterrupt:
            continue
        except EOFError:
            print("\nexiting...")
            exit(0)


if __name__ == "__main__":
    main()
