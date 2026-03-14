import os
import subprocess
from typing import Optional

from pydantic import BaseModel, Field

from wichy.helpers.string import truncate_to_len
from wichy.tools.base import BaseTool, ParametersModel


class ListFilesParameters(ParametersModel):
    path: Optional[str] = Field(
        ".",
        description="directory of which to list files for, default=current directory",
    )

    def info(self):
        return 'path="' + self.path + '"'


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files in a directory"
    parameters_model = ListFilesParameters

    def execute(self, path=".") -> str:
        """Execute file listing"""
        try:
            result = subprocess.run(
                ["ls", "-l", path],
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
            return result.stdout
        except Exception as e:
            return f"error: {e}"


class CatFileParameters(ParametersModel):
    path: str = Field(
        ...,
        description="path to file for which to look at content of",
    )
    offset: int = Field(
        1,
        description="starting line number (1-indexed), default=1",
    )
    limit: int = Field(
        2000,
        description="maximum number of lines to read, default=2000",
    )
    show_none_printable_chars: bool = Field(
        False,
        description="show all non-printing characters (like cat -A): show $ at line ends, TAB as ^I, and other non-printables in ^ notation",
    )

    def info(self):
        if self.offset == 1 and self.limit == 2000 and not self.A:
            return self.path
        return f"{self.path} (lines {self.offset}-{self.offset + self.limit - 1})"


class CatFileContentTool(BaseTool):
    name = "read_file"
    description = "Get the content of a file."
    description_long = """
Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:

- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Any lines longer than 2000 characters will be truncated
- Results are returned using cat -n format, with line numbers starting at 1
- This tool can only read files, not directories. To read a directory, use ls tool.
- You can call multiple tools in a single response. It is always better to speculatively read multiple potentially useful files in parallel.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""
    parameters_model = CatFileParameters

    MAX_LINE_LENGTH = 2000

    def _visualize_line(self, line: str) -> str:
        """Visualize non-printing characters like cat -A"""
        # Show $ at the end to mark line ending
        result = line + "$"
        # Convert tabs to ^I
        result = result.replace("\t", "^I")
        # Convert other non-printing characters to ^ notation
        # Characters below 32 (except tab, newline, carriage return) and DEL (127)
        visualized = []
        for char in result:
            code = ord(char)
            if code < 32 and char not in ("\n", "\r", "\t"):
                visualized.append(f"^{chr(code + 64)}")
            elif code == 127:
                visualized.append("^?")
            else:
                visualized.append(char)
        return "".join(visualized)

    def execute(
        self, path, offset=1, limit=2000, show_none_printable_chars=False
    ) -> str:
        """Execute read_file with optional offset, limit, and show none printable chars option"""
        try:
            # Read the file
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Check if file is empty
            if not lines:
                return f"warning: file '{path}' exists but is empty"

            # Apply offset and limit (offset is 1-indexed)
            start_idx = offset - 1
            end_idx = start_idx + limit

            if start_idx >= len(lines):
                return (
                    f"error: offset {offset} exceeds file length ({len(lines)} lines)"
                )

            selected_lines = lines[start_idx:end_idx]

            # Format with line numbers (cat -n style) and truncate long lines
            output_lines = []
            truncated_line_warning = False
            for i, line in enumerate(selected_lines, start=offset):
                # Remove trailing newline for processing
                line = line.rstrip("\n")

                # Apply -A option: visualize non-printing characters
                if show_none_printable_chars:
                    line = self._visualize_line(line)
                else:
                    # Truncate if longer than MAX_LINE_LENGTH (only when not using -A)
                    if len(line) > self.MAX_LINE_LENGTH:
                        line = line[: self.MAX_LINE_LENGTH] + "... [truncated]"
                        truncated_line_warning = True

                if show_none_printable_chars:
                    # In -A mode, show as-is without line numbers
                    output_lines.append(line)
                else:
                    # Format with line number (6 spaces for alignment like cat -n)
                    output_lines.append(f"{i:6d}  {line}")

            result = "\n".join(output_lines)

            # Add warning if file is longer than what we read
            if end_idx < len(lines):
                result += f"\n\nwarning: file has {len(lines)} lines total, but only showing lines {offset}-{min(end_idx, len(lines))}, do consecutive reads to get the rest if needed."

            # Add warning if any lines were truncated
            if truncated_line_warning:
                result += f"\n\nwarning: some lines exceeded {self.MAX_LINE_LENGTH} characters and were truncated"

            return result

        except FileNotFoundError:
            return f"error: file not found: {path}"
        except IsADirectoryError:
            return f"error: '{path}' is a directory, not a file. Use ls tool to read directories."
        except PermissionError:
            return f"error: permission denied: {path}"
        except UnicodeDecodeError:
            return (
                f"error: file '{path}' appears to be binary and cannot be read as text"
            )
        except Exception as e:
            return f"error: {e}"


class WriteFileParameters(ParametersModel):
    path: str = Field(
        ...,
        description="path for file to write content into",
    )
    content: str = Field(..., description="content to write")

    def info(self):
        return f'path="{self.path}" content="{truncate_to_len(self.content)}"'


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to file at path. This will always overwrite the current content of a file. Hence, a file update needs to contain the full new version of the content."
    parameters_model = WriteFileParameters
    description_long = """
Writes a file to the local filesystem.

Usage:

- This tool will overwrite the existing file if there is one at the provided path. Hence, a file update needs to contain the full new version of the content.
- If this is an existing file, you MUST use the cat tool first to read the file's contents. This tool will fail if you did not read the file first.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- NEVER proactively create documentation files (\*.md) or README files. Only create documentation files if explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""

    def execute(self, path, content) -> str:
        """Execute write file"""
        try:
            parent_dir_path = os.path.dirname(path)
            if parent_dir_path != "":
                os.makedirs(parent_dir_path, exist_ok=True)

            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"error: {e}"
