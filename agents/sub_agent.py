# sub agent is a type of agent that is based on a markdown file describing its task
from helpers.markdown import read_markdown_with_frontmatter
from helpers.context import ContextHandler
from rich import print
from tools import (
    BashTool,
    TreeTool,
    CatFileContentTool,
    WriteFileTool,
    SearchRecursiveTool,
    ListFilesTool,
)

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
    def __init__(self, markdown_description):
        frontmatter, instructions = read_markdown_with_frontmatter(markdown_description)

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
            print('[yellow]WAR: subagent property "model" is not implemented.[/yellow]')

        context = ContextHandler(custom_suffix=self.name, sub_dir="sub_agents")
        context.add(role="system", content=instructions)
        self.context = context

    def __call__(self, first_user_prompt = "Follow your given instructions and complete your task."):
        self.context.add(role="user", content=first_user_prompt)

