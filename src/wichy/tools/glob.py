import glob
import os
from typing import Optional

from pydantic import BaseModel, Field

from wichy.helpers.string import truncate_to_len

from .base import BaseTool, ParametersModel


class GlobParameters(ParametersModel):
    pattern: str = Field(
        ..., description="Glob pattern to match files (e.g., '**/*.py', '*.txt')"
    )
    path: Optional[str] = Field(
        ".", description="Base directory to search in, default=current directory"
    )

    def info(self):
        pattern = self.pattern
        path = self.path

        return f'pattern="{pattern}" path="{path}"'


class GlobTool(BaseTool):
    name = "glob"
    description = (
        "Find files matching a glob pattern, sorted by modification time (newest first)"
    )
    description_long = """
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/\*.js" or "src/**/\*.ts"
- Returns matching file paths sorted by modification time (newest first)
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the task tool instead
- You can call multiple tools in a single response. It is always better to speculatively perform multiple searches in parallel if they are potentially useful."""

    parameters_model = GlobParameters

    def execute(self, pattern: str, path: str = ".") -> str:
        """Execute glob search and return matching files sorted by modification time."""
        try:
            # Construct the full search path
            search_path = os.path.join(path, pattern)

            # Find all files matching the pattern
            matching_files = glob.glob(search_path, recursive=True)

            # Filter out directories, keep only files
            files_only = [f for f in matching_files if os.path.isfile(f)]

            # Sort by modification time (newest first)
            files_sorted = sorted(
                files_only, key=lambda x: os.path.getmtime(x), reverse=True
            )

            # Return results as a formatted string
            if not files_sorted:
                return f"No files found matching pattern '{pattern}' in '{path}'"

            result = f"Found {len(files_sorted)} file(s) matching '{pattern}':\n"
            for i, file_path in enumerate(files_sorted, 1):
                mod_time = os.path.getmtime(file_path)
                result += f"{i}. {file_path} (modified: {mod_time})\n"

            return result.strip()

        except Exception as e:
            return f"error: {e}"
