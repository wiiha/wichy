import glob
import os
import time
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class GlobParameters(ParametersModel):
    pattern: str = Field(
        ..., description="Glob pattern to match files (e.g., '**/*.py', '*.txt')"
    )
    path: Optional[str] = Field(
        ".", description="Base directory to search in, default=current directory"
    )
    limit: Optional[int] = Field(
        50, description="Maximum number of results to return, default=50"
    )
    exclude_venvs: Optional[bool] = Field(
        True, description="Exclude common virtual environment directories from search"
    )
    timeout: Optional[int] = Field(
        60 * 5,
        gt=0,
        description="Timeout in seconds after which the search aborts and returns partial finds. Default is 5 minutes.",
    )

    def info(self) -> str:
        pattern = self.pattern
        path = self.path
        limit = self.limit
        exclude_venvs = self.exclude_venvs
        timeout = self.timeout

        return f'pattern="{pattern}" path="{path}" limit="{limit}" exclude_venvs="{exclude_venvs}" timeout="{timeout}"'


class GlobTool(BaseTool):
    name = "glob"
    description = (
        "Find files matching a glob pattern, sorted by modification time (newest first)"
    )
    description_long = """
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time (newest first)
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the task tool instead
- You can call multiple tools in a single response. It is always better to speculatively perform multiple searches in parallel if they are potentially useful.
- Automatically excludes common virtual environment directories (venv, .venv, env, .env) by default
"""
    parameters_model = GlobParameters
    needs_verification_in_api: bool = False

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute glob search and return matching files sorted by modification time."""
        pattern: str = kwargs["pattern"]
        path: str = kwargs.get("path", ".")
        limit: int = kwargs.get("limit", 50)
        exclude_venvs: bool = kwargs.get("exclude_venvs", True)
        timeout: Optional[int] = kwargs.get("timeout", 60 * 5)

        try:
            search_path = os.path.join(path, pattern)
            deadline = None if timeout is None else time.monotonic() + timeout

            # iglob is lazy, so the deadline can abort a walk mid-iteration.
            matching_files: List[str] = []
            timed_out = False
            for candidate in glob.iglob(search_path, recursive=True):
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    break
                matching_files.append(candidate)

            files_only = [f for f in matching_files if os.path.isfile(f)]

            if exclude_venvs:
                files_only = self._exclude_virtual_environments(files_only)

            files_sorted = sorted(
                files_only, key=lambda x: os.path.getmtime(x), reverse=True
            )

            total_count = len(files_sorted)

            if limit is not None and total_count > limit:
                files_to_show = files_sorted[:limit]
                limit_note = f", showing {limit} (limit reached)"
            else:
                files_to_show = files_sorted
                limit_note = ""

            if timed_out:
                header = (
                    f"TIMEOUT: glob search aborted after {timeout}s, "
                    f"returning {len(files_to_show)} partial file(s) "
                    f"matching '{pattern}'"
                )
            elif not files_to_show:
                return f"No files found matching pattern '{pattern}' in '{path}'"
            else:
                header = f"Found {total_count} file(s) matching '{pattern}'"

            result = header + limit_note + ":\n"
            for i, file_path in enumerate(files_to_show, 1):
                mod_time = os.path.getmtime(file_path)
                result += f"{i}. {file_path} (modified: {mod_time})\n"

            return result.strip()

        except Exception as e:
            return format_error(str(e))

    def _exclude_virtual_environments(self, files: List[str]) -> List[str]:
        """Exclude files that are in common virtual environment directories."""
        # NOTE: Do NOT add "lib" or "bin" here — those are common project
        # directory names (e.g. src/lib/utils.py, project/bin/cli.py) and
        # would silently drop legitimate files. Actual virtual environments
        # are covered by venv/.venv/env/.env and site-packages below.
        excluded_dirs = {
            "venv",
            ".venv",
            "env",
            ".env",  # Common venv directory names
            "site-packages",
        }

        filtered_files = []
        for file_path in files:
            path_obj = Path(file_path)
            # Check if any parent directory is a venv directory
            if not any(part in excluded_dirs for part in path_obj.parts):
                filtered_files.append(file_path)

        return filtered_files
