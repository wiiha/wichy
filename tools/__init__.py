from .ping import PingTool
from .file_explorer import ListFilesTool, CatFileContentTool, WriteFileTool
from .file_search_in import SearchRecursiveTool

# Different tool collections for different contexts
BASIC_TOOLS = [
    PingTool(),
]

FILE_SYSTEM_TOOLS = [
    ListFilesTool(),
    CatFileContentTool(),
    WriteFileTool(),
    SearchRecursiveTool(),
]


# All tools combined
ALL_TOOLS = []
ALL_TOOLS.extend(BASIC_TOOLS)
ALL_TOOLS.extend(FILE_SYSTEM_TOOLS)


def get_tool_definitions(tools):
    """Convert a list of tools to function definitions."""
    return [tool.to_function_definition() for tool in tools]


__all__ = [
    'BASIC_TOOLS',
    'FILE_SYSTEM_TOOLS',
    'ALL_TOOLS',
    'get_tool_definitions',
]