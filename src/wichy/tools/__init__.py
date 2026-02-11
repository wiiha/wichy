from wichy.tools.base import BaseTool
from wichy.tools.bash import BashTool
from wichy.tools.fetch_webpage import FetchWebPageTool
from wichy.tools.file_explorer import CatFileContentTool, ListFilesTool, WriteFileTool
from wichy.tools.file_search_in import SearchRecursiveTool
from wichy.tools.glob import GlobTool
from wichy.tools.helpers import get_tool_definitions
from wichy.tools.ping import PingTool
from wichy.tools.reverse_dns_tool import ReverseDnsTool
from wichy.tools.search_ddg import SearchDDGTool
from wichy.tools.task_tool import TaskAgentTool
from wichy.tools.todo import TodoTool
from wichy.tools.tree import TreeTool

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


ALL_TOOLS_NOT_INSTANTIATED: list[BaseTool] = []

ALL_TOOLS_NOT_INSTANTIATED.extend(BASIC_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(WEB_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(FILE_SYSTEM_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(SUB_AGENT_TOOLS)
