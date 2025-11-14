import subprocess
import shutil
from pydantic import BaseModel, Field
from .base import BaseTool


class SearchRecursiveParameters(BaseModel):
    path: str = Field(
        ".",
        description="directory to recursively search in, default=current directory",
    )
    pattern: str = Field(
        "",
        description="text pattern to search for in files",
    )


class SearchRecursiveTool(BaseTool):
    name = "search_recursive"
    description = "Search for a pattern recursively in all files within a directory. Returns lines containing the pattern along with file paths. Under the hood, this function uses ripgrep or grep (fallback). Therefore, take care of using regex patterns."
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

    def execute(self, path=".", pattern="") -> str:
        """Execute recursive search"""
        if not pattern:
            return "error: pattern is required"

        try:
            # Try ripgrep first (much faster and respects .gitignore)
            if shutil.which("rg"):
                result = subprocess.run(
                    ["rg", "--no-heading", "--with-filename", pattern, path],
                    text=True,
                    timeout=30,
                    stderr=subprocess.STDOUT,
                    stdout=subprocess.PIPE,
                )
                return result.stdout

            # Fallback to grep -r with dynamic exclusions
            grep_cmd = ["grep", "-r", "-I"]

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


# FLAG{hidden_at_source}
