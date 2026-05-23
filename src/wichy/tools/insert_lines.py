"""
Insert Lines Tool for wichy - inserts content into files at specific line offsets.

Simplified alternative to patch tool that inserts content at line numbers. Useful for avoiding full file rewrites when adding content to large files.
"""

import os

from typing import Any

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error_with_context


class InsertLinesParameters(ParametersModel):
    file_path: str = Field(
        ...,
        description="Path to the file to modify (relative or absolute)",
    )
    offset: int = Field(
        ...,
        description="Line number (1-indexed) after which to insert the content. Content will be inserted after this line.",
    )
    content: str = Field(
        ...,
        description="Content to insert into the file",
    )
    encoding: str = Field(
        "utf-8",
        description="File encoding to use when reading/writing. Default is 'utf-8'.",
    )

    def info(self):
        return (
            f'insert after line {self.offset} in "{self.file_path}"'
            if self.offset > 0
            else f'insert at beginning of "{self.file_path}"'
        )


class InsertLinesTool(BaseTool):
    name = "insert_lines"
    description = "Insert content into a file at a specific line offset"
    description_long = """
- Insert content into a file after a specific line number
- Use offset parameter to specify where to insert (1-indexed line numbers)
- Content will be inserted after the specified line
- Useful for avoiding full file rewrites when adding content to large files
- If offset is 0 or negative, content will be inserted at the beginning of the file
- If offset exceeds file length, content will be appended at the end
- Handles all file encodings (default: utf-8)
"""
    parameters_model = InsertLinesParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Insert content into a file at a specific line offset."""
        file_path: str = kwargs["file_path"]
        offset: int = kwargs["offset"]
        content: str = kwargs["content"]
        encoding: str = kwargs.get("encoding", "utf-8")
        # Validate file exists
        if not os.path.isfile(file_path):
            return format_error_with_context(file_path, "File not found")

        # Read file content
        lines: list[str] = []
        try:
            with open(file_path, "r", encoding=encoding) as f:
                lines = f.readlines()
        except Exception as e:
            return format_error_with_context(file_path, f"Failed to read file: {e}")

        # Determine insertion point
        if offset <= 0:
            # Insert at beginning
            lines.insert(0, content)
        elif offset >= len(lines):
            # Insert at end (append)
            lines.append(content)
        else:
            # Insert after specified line
            lines.insert(offset, content)

        # Write back to file
        try:
            with open(file_path, "w", encoding=encoding) as f:
                f.writelines(lines)
        except Exception as e:
            return format_error_with_context(file_path, f"Failed to write file: {e}")

        return f"Inserted content after line {offset} in {file_path}"
