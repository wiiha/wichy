from .bash import BashTool
from .fetch_webpage import FetchWebPageTool
from .file_explorer import CatFileContentTool, ListFilesTool, WriteFileTool
from .file_search_in import SearchRecursiveTool
from .ping import PingTool
from .reverse_dns_tool import ReverseDnsTool
from .search_ddg import SearchDDGTool
from .tree import TreeTool

# Different tool collections for different contexts
BASIC_TOOLS = [
    BashTool(),
    #    SearchDDGTool(),
    #    FetchWebPageTool(),
]

NETWORK_TOOLS = [PingTool(), ReverseDnsTool()]

FILE_SYSTEM_TOOLS = [
    ListFilesTool(),
    CatFileContentTool(),
    WriteFileTool(),
    SearchRecursiveTool(),
    TreeTool(),
]


# All tools combined
ALL_TOOLS = []
ALL_TOOLS.extend(BASIC_TOOLS)
# ALL_TOOLS.extend(NETWORK_TOOLS)
ALL_TOOLS.extend(FILE_SYSTEM_TOOLS)


def get_tool_definitions(tools):
    """Convert a list of tools to function definitions."""
    return [tool.to_function_definition() for tool in tools]


__all__ = [
    "BASIC_TOOLS",
    "FILE_SYSTEM_TOOLS",
    "NETWORK_TOOLS",
    "ALL_TOOLS",
    "get_tool_definitions",
]
