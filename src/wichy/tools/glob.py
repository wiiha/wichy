import glob
import os
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

    def info(self):
        pattern = self.pattern
        path = self.path
        limit = self.limit
        exclude_venvs = self.exclude_venvs

        return f'pattern="{pattern}" path="{path}" limit="{limit}" exclude_venvs="{exclude_venvs}"'


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
        try:
            # Construct the full search path
            search_path = os.path.join(path, pattern)

            # Find all files matching the pattern
            matching_files = glob.glob(search_path, recursive=True)

            # Filter out directories, keep only files
            files_only = [f for f in matching_files if os.path.isfile(f)]

            # Exclude virtual environment directories if requested
            if exclude_venvs:
                files_only = self._exclude_virtual_environments(files_only)

            # Sort by modification time (newest first)
            files_sorted = sorted(
                files_only, key=lambda x: os.path.getmtime(x), reverse=True
            )

            total_count = len(files_sorted)

            # Apply limit if specified and needed
            if limit is not None and total_count > limit:
                files_to_show = files_sorted[:limit]
                result = f"Found {total_count} file(s) matching '{pattern}', showing {limit} (limit reached):\n"
            else:
                files_to_show = files_sorted
                result = f"Found {total_count} file(s) matching '{pattern}':\n"

            # Return results as a formatted string
            if not files_to_show:
                return f"No files found matching pattern '{pattern}' in '{path}'"

            for i, file_path in enumerate(files_to_show, 1):
                mod_time = os.path.getmtime(file_path)
                result += f"{i}. {file_path} (modified: {mod_time})\n"

            return result.strip()

        except Exception as e:
            return format_error(str(e))

    def _exclude_virtual_environments(self, files: List[str]) -> List[str]:
        """Exclude files that are in common virtual environment directories."""
        excluded_dirs = {
            "venv",
            ".venv",
            "env",
            ".env",  # Common venv directory names
            "site-packages",
            "lib",
            "bin",  # Common Python package directories
        }

        excluded_files = []
        for file_path in files:
            path_obj = Path(file_path)
            # Check if any parent directory is a venv directory
            if not any(part in excluded_dirs for part in path_obj.parts):
                excluded_files.append(file_path)

        return excluded_files
