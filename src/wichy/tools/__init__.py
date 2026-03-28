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
from wichy.tools.replace_text import ReplaceTextTool
from wichy.tools.reverse_dns_tool import ReverseDnsTool
from wichy.tools.search_ddg import WebSearchTool
from wichy.tools.task_tool import TaskAgentTool
from wichy.tools.todo import TodoTool
from wichy.tools.write_file import WriteFileTool
from wichy.tools.write_scratchpad import WriteScratchpadTool

# Different tool collections for different contexts
BASIC_TOOLS = [
    BashTool,
    TodoTool,
    AskUserQuestionTool,
]

WEB_TOOLS = [
    WebSearchTool,
]

BROWSER_TOOLS = [
    NavigateTool,
    BrowserStatusTool,
    ScreenshotTool,
    FetchWebPageTool,
    BrowserRawTool,
]

NETWORKING_TOOLS = [ReverseDnsTool]

GRAPH_TOOLS = [
    CreateGraphTool,
    ReadGraphTool,
    ListGraphsTool,
]

FILE_SYSTEM_TOOLS = [
    ListFilesTool,
    ReadFileTool,
    WriteFileTool,
    SearchInFilesTool,
    GlobTool,
    KnowledgeStoreTool,
    ReplaceTextTool,
    InsertLinesTool,
]

DUCKDB_TOOLS = [
    DuckDBLoadTool,
    DuckDBQueryTool,
    DuckDBSchemaTool,
    DuckDBStatusTool,
    DuckDBPersistTool,
    DuckDBLoadDBTool,
    DuckDBResetTool,
]

SUB_AGENT_TOOLS = [TaskAgentTool]

# Skill tools - for discovering and using skills
SKILL_TOOLS = [
    SkillDiscoveryTool,
    SkillSearchTool,
    SkillInfoTool,
    SkillScriptTool,
    SkillFileTool,
]

ALL_TOOLS_NOT_INSTANTIATED: list[BaseTool] = []

ALL_TOOLS_NOT_INSTANTIATED.extend(BASIC_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(WEB_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(BROWSER_TOOLS)
# ALL_TOOLS_NOT_INSTANTIATED.extend(NETWORKING_TOOLS) # intensional
ALL_TOOLS_NOT_INSTANTIATED.extend(FILE_SYSTEM_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(SUB_AGENT_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(SKILL_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(GRAPH_TOOLS)
ALL_TOOLS_NOT_INSTANTIATED.extend(DUCKDB_TOOLS)

ALL_TOOLS_NOT_INSTANTIATED.append(ReadScratchpadTool)
ALL_TOOLS_NOT_INSTANTIATED.append(WriteScratchpadTool)
