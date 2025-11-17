# sub agent is a type of agent that is based on a markdown file describing its task
import json
from helpers.markdown import read_markdown_with_frontmatter
from helpers.context import ContextHandler
from rich import print
from rich.markdown import Markdown
from rich.console import Console
from llm_backend import Message, call, called_tool
from tools import (
    BashTool,
    TreeTool,
    CatFileContentTool,
    WriteFileTool,
    SearchRecursiveTool,
    ListFilesTool,
    get_tool_definitions,
)
from pydantic import BaseModel
from tools.base import BaseTool

console_sub_agents = Console(quiet=True)

REQ_KEYS = ("name", "description")

tools_map = {
    "bash": BashTool(),
    "tree": TreeTool(),
    "read": CatFileContentTool(),
    "write": WriteFileTool(),
    "grep": SearchRecursiveTool(),
    "ls": ListFilesTool(),
}


class SubAgent:
    def __init__(
        self,
        markdown_description,
        first_user_prompt="Follow your given instructions and complete your task.",
    ):
        frontmatter, instructions = read_markdown_with_frontmatter(markdown_description)
        if first_user_prompt is None:
            first_user_prompt = "Follow your given instructions and complete your task."

        if len(frontmatter.keys()) < len(REQ_KEYS):
            raise ValueError(
                f"expected at least {len(REQ_KEYS)} keys in frontmatter, got {len(frontmatter.keys())}"
            )

        if len(instructions.strip()) == 0:
            raise ValueError("there is no instruction for the sub agent")

        for rk in REQ_KEYS:
            if not rk in frontmatter.keys():
                raise ValueError(f"front matter missing key {rk}")

        self.name = frontmatter["name"]
        self.description = frontmatter["description"]
        tools = [tools_map[k] for k in tools_map]
        allowed_tools = frontmatter.get("tools", None)
        if allowed_tools != None:
            tools = []
            for tool_name in allowed_tools.split(","):
                tool_name = tool_name.strip()
                if tool_name == "":
                    continue
                tool = tools_map.get(tool_name, None)
                if tool is None:
                    raise ValueError(f"no tool named {tool_name}")
                tools.append(tool)

        self.tools = tools

        if "model" in frontmatter.keys():
            console_sub_agents.print('[yellow]WAR: subagent property "model" is not implemented.[/yellow]')

        context = ContextHandler(custom_suffix=self.name, sub_dir="sub_agents")
        context.add(role="system", content=instructions)
        context.add(role="user", content=first_user_prompt)
        self.context = context

    def run(self):
        res = self._process()
        console_sub_agents.log(
            Markdown(
                "\n\n---\n\n ### Sub Agent " + self.name + " - final message\n\n" + res
            )
        )
        return res

    def _tool_call(self, tools, item: called_tool):
        result = None
        name = item.function.name
        args = json.loads(item.function.arguments)
        console_sub_agents.log({"tool": name, "args": args})
        for tool in tools:
            if name == tool.name:
                result = tool.validate_and_execute(**args)

        if result is None:
            result = "There is no tool called " + item.function.name + "."
        return {"role": "tool", "tool_call_id": item.id, "content": result}

    def _handle_tools(self, tools, response: Message):
        if response.finish_reason != "tool_calls":
            return False

        self.context.append(
            {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [t.model_dump() for t in response.tool_calls],
            }
        )

        console_sub_agents.log(
            "[italic]got " + str(len(response.tool_calls)) + " tool calls[/italic]"
        )
        osz = len(self.context)
        for item in response.tool_calls:
            self.context.append(self._tool_call(tools, item))
        return len(self.context) != osz

    def _process(self, line=""):

        tools = self.tools
        if line != "":
            self.context.add(role="user", content=line)
        tool_defs = get_tool_definitions(tools)
        response = call(context=self.context(), tool_defs=tool_defs)

        while self._handle_tools(tools, response):
            response = call(self.context(), tool_defs)
        self.context.append({"role": "assistant", "content": response.content})
        return response.content


def new_sub_agent_as_tool(
    markdown_description,
    first_user_prompt=None,
):

    class SubAgentParameters(BaseModel):
        pass

    class SubAgentTool(BaseTool):
        name = "NOT_SET"
        description = "NOT_SET"
        parameters_model = SubAgentParameters

        def __init__(self, markdown_description, first_user_prompt=None):
            super().__init__()
            sa = SubAgent(markdown_description=markdown_description)
            self.name = sa.name
            self.description = sa.description
            sa.context.delete()
            self.markdown_description = markdown_description
            self.first_user_prompt = first_user_prompt

        def execute(self) -> str:
            """run sub agent"""
            sa = SubAgent(
                markdown_description=self.markdown_description,
                first_user_prompt=self.first_user_prompt,
            )
            try:
                result = sa.run()
                return result
            except Exception as e:
                return f"error: {e}"

    return SubAgentTool(markdown_description, first_user_prompt)
