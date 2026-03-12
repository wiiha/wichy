import shutil
import subprocess
from enum import Enum

from pydantic import BaseModel, Field

from wichy.tools.base import BaseTool, ParametersModel


class OutputMode(str, Enum):
    CONTENT = "content"
    FILES_WITH_MATCHES = "files_with_matches"
    COUNT = "count"


class SearchRecursiveParameters(ParametersModel):
    path: str = Field(
        ".",
        description="directory to recursively search in, default=current directory",
    )
    pattern: str = Field(
        "",
        description="text pattern to search for in files",
    )
    output_mode: OutputMode = Field(
        OutputMode.FILES_WITH_MATCHES,
        description="output format: 'content' shows matching lines, 'files_with_matches' shows only file paths (default), 'count' shows match counts per file",
    )

    def info(self):
        pattern = self.pattern
        path = self.path
        mode = self.output_mode.value

        return f'pattern="{pattern}" path="{path}" output_mode="{mode}"'


class SearchRecursiveTool(BaseTool):
    name = "grep"
    description = "Search for a pattern recursively in all files within a directory. Returns lines containing the pattern along with file paths. Under the hood, this function uses ripgrep or grep (fallback). Therefore, take care of using regex patterns."
    description_long = """
A powerful search tool built on ripgrep

Usage:

- BEST PRACTICE is to always do "count" for a search before doing "content", in order to avoid exploding the context.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
- Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
- Use Task tool for open-ended searches requiring multiple rounds
- Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
"""
    parameters_model = SearchRecursiveParameters

    # Directories to exclude from search
    EXCLUDED_DIRS = [
        "venv",
        ".venv",
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".tox",
        "dist",
        "build",
        ".eggs",
        "*.egg-info",
        ".npm",
        ".yarn",
        "coverage",
        ".coverage",
        ".next",
        ".nuxt",
        "target",  # Rust/Java
        "bin",
        "obj",  # .NET
    ]

    def execute(
        self, path=".", pattern="", output_mode=OutputMode.FILES_WITH_MATCHES
    ) -> str:
        """Execute recursive search"""
        if not pattern or pattern.strip() == "":
            return "error: pattern is required"

        # Convert string to enum if needed
        if isinstance(output_mode, str):
            output_mode = OutputMode(output_mode)

        try:
            # Try ripgrep first (much faster and respects .gitignore)
            if shutil.which("rg"):
                rg_cmd = ["rg"]
                rg_cmd.append("--hidden")

                if output_mode == OutputMode.FILES_WITH_MATCHES:
                    rg_cmd.append("--files-with-matches")
                elif output_mode == OutputMode.COUNT:
                    rg_cmd.append("--count")
                else:  # CONTENT
                    rg_cmd.extend(["--no-heading", "--with-filename"])

                rg_cmd.extend([pattern, path])

                result = subprocess.run(
                    rg_cmd,
                    text=True,
                    timeout=30,
                    stderr=subprocess.STDOUT,
                    stdout=subprocess.PIPE,
                )
                return result.stdout if result.stdout else "no matches found"

            # Fallback to grep -r with dynamic exclusions
            grep_cmd = ["grep", "-r", "-I"]

            # Add output mode flags
            if output_mode == OutputMode.FILES_WITH_MATCHES:
                grep_cmd.append("-l")
            elif output_mode == OutputMode.COUNT:
                grep_cmd.append("-c")
            # CONTENT mode doesn't need extra flags

            # Add all exclusions dynamically
            for excluded in self.EXCLUDED_DIRS:
                grep_cmd.append(f"--exclude-dir={excluded}")

            grep_cmd.extend([pattern, path])

            result = subprocess.run(
                grep_cmd,
                text=True,
                capture_output=True,
                timeout=30,
            )
            return result.stdout if result.stdout else "no matches found"

        except subprocess.TimeoutExpired:
            return "error: search timed out after 30 seconds"
        except Exception as e:
            return f"error: {e}"
