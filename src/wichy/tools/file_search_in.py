import shutil
import subprocess
from enum import Enum
from typing import Any

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


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


# Content mode: if the result set exceeds this many matches, bail out with
# a warning + sample instead of returning all content.
# ~500 lines × ~120 chars/line ≈ 60k chars — well within any context window.
_CONTENT_LIMIT = 500

# How many sample lines to return when the limit is exceeded.
_SAMPLE_LINES = 20

# Truncate individual output lines at this many characters.
# Protects against minified/binary files with a single match on a line
# that is thousands of chars long.  ~260 chars of content after the
# "filename:" prefix is plenty for any match context.
_LINE_TRUNCATE = 300


_TRUNCATION_SUFFIX = "[...TRUNCATED]"


class SearchInFilesTool(BaseTool):
    name = "search_in_files"
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
    enable_result_offload = True
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

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _truncate_output(self, output: str) -> str:
        """Truncate each line in output to _LINE_TRUNCATE chars.

        Guards against minified / single-line files where one match
        expands to thousands of characters, which would still flood the
        context window even when the per-file match cap is respected.
        """
        lines = output.splitlines()
        truncated = []
        for line in lines:
            if len(line) > _LINE_TRUNCATE:
                truncated.append(line[:_LINE_TRUNCATE] + "  " + _TRUNCATION_SUFFIX)
            else:
                truncated.append(line)
        return "\n".join(truncated)

    def _run_rg(self, pattern: str, path: str, output_mode: OutputMode) -> str:
        """Run ripgrep and return output."""
        cmd = ["rg", "--hidden"]
        if output_mode == OutputMode.FILES_WITH_MATCHES:
            cmd.append("--files-with-matches")
        elif output_mode == OutputMode.COUNT:
            cmd.append("--count")
        else:
            cmd.extend(["--no-heading", "--with-filename"])
        cmd.extend([pattern, path])

        result = subprocess.run(
            cmd,
            text=True,
            timeout=30,
            stderr=subprocess.STDOUT,
            stdout=subprocess.PIPE,
        )
        raw = result.stdout if result.stdout else "no matches found"
        if output_mode == OutputMode.CONTENT:
            return self._truncate_output(raw)
        return raw

    def _run_grep(self, pattern: str, path: str, output_mode: OutputMode) -> str:
        """Run grep and return output."""
        cmd = ["grep", "-r", "-I"]
        if output_mode == OutputMode.FILES_WITH_MATCHES:
            cmd.append("-l")
        elif output_mode == OutputMode.COUNT:
            cmd.append("-c")
        else:
            cmd.append("-H")
        for excluded in self.EXCLUDED_DIRS:
            cmd.append(f"--exclude-dir={excluded}")
        cmd.extend([pattern, path])

        result = subprocess.run(
            cmd,
            text=True,
            timeout=30,
            capture_output=True,
        )
        raw = result.stdout if result.stdout else "no matches found"
        if output_mode == OutputMode.CONTENT:
            return self._truncate_output(raw)
        return raw

    def _count(self, pattern: str, path: str) -> int:
        """Return total match count across all files."""
        if shutil.which("rg"):
            cmd = ["rg", "--hidden", "--count", pattern, path]
            result = subprocess.run(
                cmd,
                text=True,
                timeout=30,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
        else:
            cmd = ["grep", "-r", "-I", "-c", pattern, path]
            for excluded in self.EXCLUDED_DIRS:
                cmd.append(f"--exclude-dir={excluded}")
            result = subprocess.run(
                cmd,
                text=True,
                timeout=30,
                capture_output=True,
            )

        total = 0
        for line in result.stdout.strip().splitlines():
            try:
                total += int(line.rsplit(":", 1)[-1])
            except ValueError:
                pass
        return total

    # -------------------------------------------------------------------------
    # BaseTool interface
    # -------------------------------------------------------------------------

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute recursive search."""
        path: str
        if "path" in kwargs:
            path = kwargs["path"]
        elif args:
            path = args[0]
        else:
            path = "."
        pattern: str
        if "pattern" in kwargs:
            pattern = kwargs["pattern"]
        elif len(args) > 1:
            pattern = args[1]
        else:
            pattern = ""

        output_mode: Any = (
            kwargs.get("output_mode")
            if "output_mode" in kwargs
            else (args[2] if len(args) > 2 else OutputMode.FILES_WITH_MATCHES)
        )
        if isinstance(output_mode, str):
            output_mode = OutputMode(output_mode)
        if not pattern or pattern.strip() == "":
            return format_error("pattern is required")

        if isinstance(output_mode, str):
            output_mode = OutputMode(output_mode)

        try:
            # For content mode: count first.  If the result set is small, fetch
            # content directly.  If it's large, return a warning + sample so the
            # LLM knows to narrow the pattern rather than getting a truncated dump.
            if output_mode == OutputMode.CONTENT:
                total = self._count(pattern, path)
                if total > _CONTENT_LIMIT:
                    # One bounded content call to get the sample.
                    if shutil.which("rg"):
                        output = self._run_rg(pattern, path, output_mode)
                    else:
                        output = self._run_grep(pattern, path, output_mode)
                    sample = "\n".join(output.splitlines()[:_SAMPLE_LINES])
                    return (
                        f"[WARNING] {total:,} total matches — too large for content "
                        f"mode (limit {_CONTENT_LIMIT}).\n\n"
                        f"[Sample (first {_SAMPLE_LINES} lines):]\n{sample}\n\n"
                        f"Narrow your pattern (e.g. add a file-type glob like '*.py', "
                        f"a subdirectory path, or an anchored regex) or use "
                        f"output_mode='count' to explore further."
                    )

            # Normal path: run the requested output mode directly.
            if shutil.which("rg"):
                return self._run_rg(pattern, path, output_mode)
            else:
                return self._run_grep(pattern, path, output_mode)

        except subprocess.TimeoutExpired:
            return format_error("search timed out after 30 seconds")
        except Exception as e:
            return format_error(str(e))
