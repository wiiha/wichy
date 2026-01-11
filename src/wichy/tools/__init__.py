from .base import BaseTool
from .bash import BashTool
from .fetch_webpage import FetchWebPageTool
from .file_explorer import CatFileContentTool, ListFilesTool, WriteFileTool
from .file_search_in import SearchRecursiveTool
from .ping import PingTool
from .reverse_dns_tool import ReverseDnsTool
from .search_ddg import SearchDDGTool
from .todo import TodoTool
from .tree import TreeTool

# Different tool collections for different contexts
BASIC_TOOLS = [
    BashTool,
    TodoTool,
    SearchDDGTool,
    FetchWebPageTool,
]

FILE_SYSTEM_TOOLS = [
    ListFilesTool,
    CatFileContentTool,
    WriteFileTool,
    SearchRecursiveTool,
    TreeTool,
]


ALL_TOOLS_UNINSTANTIATED: list[BaseTool] = []

ALL_TOOLS_UNINSTANTIATED.extend(BASIC_TOOLS)
ALL_TOOLS_UNINSTANTIATED.extend(FILE_SYSTEM_TOOLS)


def get_tool_definitions(tools):
    """Convert a list of tools to function definitions."""
    return [tool.to_function_definition() for tool in tools]


__all__ = [
    "get_tool_definitions",
]
