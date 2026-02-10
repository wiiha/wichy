from .base import BaseTool
from .bash import BashTool
from .fetch_webpage import FetchWebPageTool
from .file_explorer import CatFileContentTool, ListFilesTool, WriteFileTool
from .file_search_in import SearchRecursiveTool
from .glob import GlobTool
from .helpers import get_tool_definitions
from .ping import PingTool
from .reverse_dns_tool import ReverseDnsTool
from .search_ddg import SearchDDGTool
from .task_tool import TaskAgentTool
from .todo import TodoTool
from .tree import TreeTool

# Different tool collections for different contexts
BASIC_TOOLS = [
    BashTool,
    TodoTool,
]

WEB_TOOLS = [SearchDDGTool, FetchWebPageTool]

NETWORKING_TOOLS = [PingTool, ReverseDnsTool]

FILE_SYSTEM_TOOLS = [
    ListFilesTool,
    CatFileContentTool,
    WriteFileTool,
    SearchRecursiveTool,
    TreeTool,
    GlobTool,
]

SUB_AGENT_TOOLS = [TaskAgentTool]


ALL_TOOLS_UNINSTANTIATED: list[BaseTool] = []

ALL_TOOLS_UNINSTANTIATED.extend(BASIC_TOOLS)
ALL_TOOLS_UNINSTANTIATED.extend(FILE_SYSTEM_TOOLS)
ALL_TOOLS_UNINSTANTIATED.extend(SUB_AGENT_TOOLS)
