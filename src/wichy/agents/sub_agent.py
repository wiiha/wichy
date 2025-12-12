# sub agent is a type of agent that is based on a markdown file describing its task
import json
from wichy.helpers.markdown import read_markdown_with_frontmatter
from wichy.helpers.context import ContextHandler
from wichy.helpers.string import strip_thinking_content
from rich.markdown import Markdown
from rich.console import Console
from wichy.llm_backend import Message, call, called_tool
from wichy.tools import (
    BashTool,
    TreeTool,
    CatFileContentTool,
    WriteFileTool,
    SearchRecursiveTool,
    ListFilesTool,
    SearchDDGTool,
    FetchWebPageTool,
    get_tool_definitions,
)
from pydantic import BaseModel, Field
from wichy.tools.base import BaseTool

console_sub_agents = Console(quiet=True)

REQ_KEYS = ("name", "description", "model")

tools_map = {
    "bash": BashTool(),
    "tree": TreeTool(),
    "read": CatFileContentTool(),
    "write": WriteFileTool(),
    "grep": SearchRecursiveTool(),
    "ls": ListFilesTool(),
    "web_search": SearchDDGTool(),
    "web_fetch": FetchWebPageTool(),
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
        self.model = frontmatter["model"]
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

        context = ContextHandler(custom_suffix=self.name, sub_dir="sub_agents")
        context.add(role="system", content=instructions)
        context.add(role="user", content=first_user_prompt)
        self.context = context

    def run(self):
        console_sub_agents.log(
            Markdown(
                "\n\n---\n\n ### Sub Agent "
                + self.name
                + " called\n\n- llm model: "
                + self.model
                + "\n\n- given task: "
                + (self.context()[1]["content"] if len(self.context) >= 2 else self.context()[0]["content"])
            )
        )
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

        try:
            tools = self.tools
            if line != "":
                self.context.add(role="user", content=line)
            tool_defs = get_tool_definitions(tools)
            response = call(
                context=self.context(), tool_defs=tool_defs, model_name=self.model
            )

            while self._handle_tools(tools, response):
                response = call(self.context(), tool_defs, model_name=self.model)
            self.context.append({"role": "assistant", "content": response.content})
            return response.content
        except KeyboardInterrupt as e:
            raise Exception("user aborted execution of " + self.name)


def new_sub_agent_as_tool(
    markdown_description,
):

    class SubAgentParameters(BaseModel):
        first_prompt: str = Field(
            "Follow your given instructions and complete your task.",
            description="The initial instructions to give the agent. Unless mentioned, this parameter should be left with its default value.",
        )
        model_name: str = Field(
            None,
            description="HIDE_FROM_LLM What model to use. Name should be a reference to a valid model given the backend used.",
        )

    class SubAgentTool(BaseTool):
        name = "NOT_SET"
        description = "NOT_SET"
        parameters_model = SubAgentParameters

        def __init__(self, markdown_description):
            super().__init__()
            sa = SubAgent(markdown_description=markdown_description)
            self.name = sa.name
            self.description = sa.description
            sa.context.delete()
            self.markdown_description = markdown_description

        def execute(
            self,
            model_name=None,
            first_prompt="Follow your given instructions and complete your task.",
        ) -> str:
            """run sub agent"""
            sa = SubAgent(
                markdown_description=self.markdown_description,
                first_user_prompt=first_prompt,
            )
            if model_name == None:
                raise ValueError("Parameter model_name must be passed, got None")

            if sa.model == "inherit":
                sa.model = model_name

            try:
                result = sa.run()
                result = strip_thinking_content(result)
                return result
            except Exception as e:
                return f"error: {e}"

    return SubAgentTool(markdown_description)
