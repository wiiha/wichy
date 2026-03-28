from wichy.skills import (
    SkillDiscoveryTool,
    SkillFileTool,
    SkillInfoTool,
    SkillScriptTool,
    SkillSearchTool,
)
from wichy.tools.ask_user_question import AskUserQuestionTool
from wichy.tools.base import BaseTool
from wichy.tools.bash import BashTool
from wichy.tools.duckdb_load import DuckDBLoadTool
from wichy.tools.duckdb_persist import DuckDBLoadDBTool, DuckDBPersistTool
from wichy.tools.duckdb_query import DuckDBQueryTool
from wichy.tools.duckdb_reset import DuckDBResetTool
from wichy.tools.duckdb_schema import DuckDBSchemaTool
from wichy.tools.duckdb_status import DuckDBStatusTool
from wichy.tools.fetch_webpage import (
    BrowserActTool,
    BrowserPageInfoTool,
    BrowserRawTool,
    BrowserStatusTool,
    FetchWebPageTool,
    NavigateTool,
    ScreenshotTool,
)
from wichy.tools.file_search_in import SearchInFilesTool
from wichy.tools.glob import GlobTool
from wichy.tools.graph_tools import CreateGraphTool, ListGraphsTool, ReadGraphTool
from wichy.tools.helpers import get_tool_definitions
from wichy.tools.insert_lines import InsertLinesTool
from wichy.tools.knowledge_store import KnowledgeStoreTool
from wichy.tools.list_files import ListFilesTool
from wichy.tools.read_file import ReadFileTool
from wichy.tools.read_scratchpad import ReadScratchpadTool
from wichy.tools.registry import (
    clear_registry,
    get_all_tools,
    get_registry_copy,
    get_tool_by_name,
    get_tools_by_names,
    restore_registry,
)
from wichy.tools.replace_text import ReplaceTextTool
from wichy.tools.reverse_dns_tool import ReverseDnsTool
from wichy.tools.search_ddg import WebSearchTool
from wichy.tools.task_tool import TaskAgentTool
from wichy.tools.todo import TodoTool
from wichy.tools.write_file import WriteFileTool
from wichy.tools.write_scratchpad import WriteScratchpadTool

# Note: The tool classes imported above are automatically registered via the
# ToolMeta metaclass when they are defined. The registry functions below
# can then be used to look up tools by name.

__all__ = [
    # Registry functions
    "get_all_tools",
    "get_tool_by_name",
    "get_tools_by_names",
    "clear_registry",
    "get_registry_copy",
    "restore_registry",
    # Helper functions
    "get_tool_definitions",
    # Base class
    "BaseTool",
    # Tool classes
    "AskUserQuestionTool",
    "BashTool",
    "DuckDBLoadTool",
    "DuckDBLoadDBTool",
    "DuckDBPersistTool",
    "DuckDBQueryTool",
    "DuckDBResetTool",
    "DuckDBSchemaTool",
    "DuckDBStatusTool",
    "BrowserActTool",
    "BrowserPageInfoTool",
    "BrowserRawTool",
    "BrowserStatusTool",
    "FetchWebPageTool",
    "NavigateTool",
    "ScreenshotTool",
    "SearchInFilesTool",
    "GlobTool",
    "CreateGraphTool",
    "ListGraphsTool",
    "ReadGraphTool",
    "InsertLinesTool",
    "KnowledgeStoreTool",
    "ListFilesTool",
    "ReadFileTool",
    "ReadScratchpadTool",
    "ReplaceTextTool",
    "ReverseDnsTool",
    "WebSearchTool",
    "TaskAgentTool",
    "TodoTool",
    "WriteFileTool",
    "WriteScratchpadTool",
    "SkillDiscoveryTool",
    "SkillFileTool",
    "SkillInfoTool",
    "SkillScriptTool",
    "SkillSearchTool",
    # Backward compatibility
    "ALL_TOOLS_NOT_INSTANTIATED",
]

# All registered tools, automatically populated from the registry.
# Note: ReverseDnsTool is intentionally excluded for backward compatibility.
ALL_TOOLS_NOT_INSTANTIATED: list[type[BaseTool]] = [
    tool for tool in get_all_tools() if tool is not ReverseDnsTool
]
