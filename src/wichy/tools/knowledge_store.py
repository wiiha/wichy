import os
import re
import shutil
import subprocess
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.human_verification import block_on


class OutputMode(str, Enum):
    CONTENT = "content"
    FILES_WITH_MATCHES = "files_with_matches"
    COUNT = "count"


class KnowledgeStoreParameters(ParametersModel):
    pattern: str = Field(
        ...,
        description="text pattern to search for in the knowledge store files",
    )
    output_mode: OutputMode = Field(
        OutputMode.FILES_WITH_MATCHES,
        description="output format: 'content' shows matching lines, 'files_with_matches' shows only file paths (default), 'count' shows match counts per file",
    )
    glob: str = Field(
        "*.md",
        description="file pattern to filter files (e.g., '*.md', '*.txt', '**/*.md')",
    )
    model_str: Optional[str] = Field(
        None,
        description="HIDE_FROM_LLM The model string for the LLM backend (e.g., ollama/xxx, open_router/xxx)",
    )

    def info(self):
        return f'pattern="{self.pattern}" output_mode="{self.output_mode.value}" glob="{self.glob}"'


def block_on_open_router(
    self,
    pattern="",
    output_mode=OutputMode.FILES_WITH_MATCHES,
    glob="*.md",
    model_str=None,
) -> tuple[bool, Optional[str]]:
    """
    Decision function to block KnowledgeStoreTool execution when LLM backend is open_router or unknown (None).

    Args:
        self: The KnowledgeStoreTool instance
        pattern: Search pattern (from execute params)
        output_mode: Output mode (from execute params)
        glob: Glob pattern (from execute params)
        model_str: The model string passed by RootAgent (e.g., "ollama/qwen3.5:4b", "open_router/some-model")

    Returns:
        (True, reason) if should block, (False, None) otherwise
    """
    if model_str is None:
        return (
            True,
            "KnowledgeStoreTool requires a known LLM backend (model_str cannot be None). Please use a local backend like ollama or llama_cpp.",
        )
    if model_str.startswith("open_router"):
        return (
            True,
            f"KnowledgeStoreTool is not allowed to be used with open_router backend. Please use a local backend like ollama or llama_cpp. Got: {model_str}",
        )
    return False, None


class KnowledgeStoreTool(BaseTool):
    name = "knowledge_store"
    description = "Search the user's knowledge store (markdown-based notes) for a pattern. Similar to grep but with a locked directory path. Searches markdown files by default."
    description_long = """
    Search your personal knowledge store for patterns or content.

    This tool searches through your personal knowledge store, which is
    typically stored in markdown format. It works similar to the grep tool but
    operates on a dedicated knowledge directory.

    Usage notes:

    - The knowledge store directory is set by the user and cannot be changed.
    - By default searches markdown files (*.md), but can be configured with different glob patterns
    - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
    - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
    """
    parameters_model = KnowledgeStoreParameters
    DEFAULT_TIMEOUT = 30

    def __init__(self, knowledge_dir: str = None):
        """
        Initialize the KnowledgeStoreTool.

        :param knowledge_dir: Path to the knowledge store directory (default: from KNOWLEDGE_BASE env var, or "notes")
        """
        if knowledge_dir is None:
            knowledge_dir = os.getenv("KNOWLEDGE_BASE", "notes")
        self.knowledge_dir = Path(knowledge_dir).resolve()

    @block_on(block_on_open_router)
    def execute(
        self,
        pattern="",
        output_mode=OutputMode.FILES_WITH_MATCHES,
        glob="*.md",
        model_str=None,
    ) -> str:
        """Execute search in the knowledge store"""
        # Guard clauses for validation
        if not pattern or not pattern.strip():
            return "error: pattern is required"

        if not self.knowledge_dir.exists():
            return f"error: knowledge store directory '{self.knowledge_dir}' does not exist"

        # Normalize output_mode to enum
        if isinstance(output_mode, str):
            try:
                output_mode = OutputMode(output_mode)
            except ValueError:
                return f"error: invalid output_mode '{output_mode}'"

        # Choose search strategy: ripgrep > Python glob + manual search > simple grep
        if shutil.which("rg"):
            return self._search_with_ripgrep(pattern, output_mode, glob)

        if glob and glob != "*":
            return self._search_with_python_glob(pattern, output_mode, glob)

        return self._search_fallback_simple(pattern, output_mode)

    def _search_with_ripgrep(
        self, pattern: str, output_mode: OutputMode, glob: str
    ) -> str:
        """Execute search using ripgrep."""
        cmd = ["rg"]

        # Output mode flags
        if output_mode == OutputMode.FILES_WITH_MATCHES:
            cmd.append("--files-with-matches")
        elif output_mode == OutputMode.COUNT:
            cmd.append("--count")
        else:  # CONTENT
            cmd.extend(["--no-heading", "--with-filename"])

        # Glob pattern
        if glob and glob != "*":
            cmd.extend(["-g", glob])

        cmd.extend([pattern, str(self.knowledge_dir)])
        return self._run_command(cmd)

    def _search_with_python_glob(
        self, pattern: str, output_mode: OutputMode, glob: str
    ) -> str:
        """Fallback search using Python's glob to find files, then search them with regex."""
        try:
            knowledge_path = Path(self.knowledge_dir)
            files = [str(p) for p in knowledge_path.rglob(glob) if p.is_file()]
        except Exception as e:
            return f"error: {e}"

        if not files:
            return "no files found matching the pattern"

        return self._search_in_files(pattern, output_mode, files)

    def _search_in_files(
        self, pattern: str, output_mode: OutputMode, files: list[str]
    ) -> str:
        """
        Search through a list of files for the pattern and format results.
        Handles all three output modes.
        """
        regex = re.compile(pattern)
        results = []

        for file in files:
            try:
                with open(file, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (IOError, OSError):
                continue

            if output_mode == OutputMode.CONTENT:
                # For content mode, show each matching line with file:line:content
                for i, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        results.append(f"{file}:{i}:{line}")
            else:
                # For files_with_matches and COUNT modes, we just need to count matches
                matches = regex.findall(content)
                if matches:
                    if output_mode == OutputMode.FILES_WITH_MATCHES:
                        results.append(file)
                    else:  # COUNT
                        results.append(f"{file}:{len(matches)}")

        return "\n".join(results) if results else "no matches found"

    def _search_fallback_simple(self, pattern: str, output_mode: OutputMode) -> str:
        """Fallback search using system grep (no glob filtering)."""
        cmd = ["grep", "-r", "-I"]

        if output_mode == OutputMode.FILES_WITH_MATCHES:
            cmd.append("-l")
        elif output_mode == OutputMode.COUNT:
            cmd.append("-c")
        # CONTENT mode uses default grep output (no flag needed)

        cmd.extend([pattern, str(self.knowledge_dir)])
        return self._run_command(cmd)

    def _run_command(self, cmd: list[str], **kwargs) -> str:
        """Execute a subprocess command with standardized timeout and error handling."""
        try:
            result = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=self.DEFAULT_TIMEOUT,
                **kwargs,
            )
            return result.stdout if result.stdout else "no matches found"
        except subprocess.TimeoutExpired:
            return "error: search timed out after 30 seconds"
        except Exception as e:
            return f"error: {e}"
