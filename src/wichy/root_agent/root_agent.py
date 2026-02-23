import json
from enum import Enum
from typing import List

from rich import print
from rich.markdown import Markdown

from wichy.helpers.console import console
from wichy.helpers.context import new_context
from wichy.helpers.string import strip_thinking_content
from wichy.llm_backend import Message, call, called_tool
from wichy.tools import get_tool_definitions
from wichy.tools.base import BaseTool


class ContextResetStrategies(str, Enum):
    NUKE = "nuke"
    SUMMARY = "summary"


class RootAgent:
    def __init__(self, model_str, tools: List[BaseTool], name: str = "NOT SET"):
        self.context = new_context()
        self.name = name
        self.model_str = model_str
        self.tools = tools
        console.log(
            {"model_str": self.model_str, "tools": ", ".join([t.name for t in tools])}
        )
        tool_str = ""
        for t in tools:
            tool_str += t.name + ", "
        tool_str = tool_str.removesuffix(", ")
        tool_str += "\n"
        print(
            Markdown(
                f"### Root Agent Info\n - **template name:** {self.name}\n- **model string:** {self.model_str}\n- **tools:**\n{tool_str}"
            )
        )

    def tool_call(self, tools, item: called_tool):
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)
        console.log({"tool": name, "args": args})

        args["model_str"] = self.model_str

        for tool in tools:
            if name == tool.name:
                result = tool.validate_and_execute(**args)
                break

        if result is None:
            result = "There is no tool called " + item.function.name + "."
        return {"role": "tool", "tool_call_id": item.id, "content": result}

    def handle_tools(self, tools, response: Message):
        if response.finish_reason != "tool_calls":
            return False

        if strip_thinking_content(response.content) != "":
            result = (
                "\n---\n\n### Assistant\n"
                + strip_thinking_content(response.content)
                + "\n\n---"
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

    def drop_last_context_entry(self):
        if len(self.context) < 2:
            # only system msg left, cant drop that
            return

        self.context.drop()

    def reset_context(self, strategy: ContextResetStrategies):
        first_prompt = self.context()[0]

        if strategy == ContextResetStrategies.SUMMARY:
            # Keep the original system prompt from the first context entry
            ctx = new_context()
            ctx.append(first_prompt)
            # add in messages from old context
            for i in self.context()[1:]:
                ctx.append(i)

            # Add a summary message to the context
            ctx.add(
                role="user",
                content="Please summarize our conversation. Keep it concise and structured. Include any external sources mentioned.",
            )

            # Generate the summary
            response = call(context=ctx(), model_str=self.model_str)

            # Create the summary message
            summary_msg = "\n\n---\n\n### Summary of context\n\n" + response.content

            # Print the summary
            print(Markdown(summary_msg))
            ctx.delete()

            # Create new context with original system prompt and summary
            n_ctx = new_context()
            n_ctx.append(first_prompt)
            n_ctx.add(role="user", content=summary_msg)
            self.context = n_ctx
            return

        # nuke, default case
        ctx = new_context()
        ctx.append(first_prompt)
        self.context = ctx
