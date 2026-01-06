import json
from rich import print
from rich.markdown import Markdown
from wichy.helpers.console import console
from wichy.helpers.context import new_context
from wichy.helpers.string import strip_thinking_content
from wichy.tools import get_tool_definitions
from wichy.llm_backend import called_tool, Message, call
from enum import Enum


class ContextResetStrategies(str, Enum):
    NUKE = "nuke"
    SUMMARY = "summary"


class RootAgent:
    def __init__(self, model_str, tools):
        self.context = new_context()
        self.model_str = model_str
        self.tools = tools
        console.log({"model_str": self.model_str})

    def tool_call(self, tools, item: called_tool):
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)
        console.log({"tool": name, "args": args})

        # Inject extra argument if name has 'agent-' prefix
        if name.startswith("agent-"):
            args["model_str"] = self.model_str

        if name.startswith("artifact_"):
            args["creator"] = "root_agent"

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
            context=self.context(), tool_defs=tool_defs, model_str=self.model_str
        )

        while self.handle_tools(self.tools, response):
            response = call(self.context(), tool_defs, model_str=self.model_str)
        self.context.append({"role": "assistant", "content": response.content})
        return response.content

    def reset_context(self, strategy: ContextResetStrategies):
        first_prompt = self.context()[0]

        if strategy == ContextResetStrategies.SUMMARY:
            ctx = new_context()
            ctx.append(
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Your task is to generate a summary of the following conversation between yourself and the user. /think",
                }
            )

            for i in self.context()[1:]:
                ctx.append(i)

            ctx.add(
                role="user",
                content="Now summarize our conversation in a single message. Keep it simple, structured and concise. If external sources has been mentioned, list these.",
            )

            response = call(context=ctx(), model_str=self.model_str)

            res = "\n\n---\n\n ### Summary of context\n\n" + response.content

            print(Markdown(res))
            ctx.delete()
            n_ctx = new_context()
            n_ctx.append(first_prompt)
            n_ctx.add(role="user", content=res)
            self.context = n_ctx
            return

        # nuke default case
        ctx = new_context()
        ctx.append(first_prompt)
        self.context = ctx
