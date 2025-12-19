import json
from rich import print
from rich.markdown import Markdown
from wichy.helpers.console import console
from wichy.helpers.context import new_context
from wichy.helpers.string import strip_thinking_content
from wichy.tools import get_tool_definitions
from wichy.llm_backend import called_tool, Message, call


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
