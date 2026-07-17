"""
Replace Text Tool for wichy - replaces exact text matches in files.

Simplified alternative to patch tool that uses before/after strings instead of
unified diff format. Much easier for agents to use correctly.
"""

import difflib
import os
from typing import Any

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error_with_context


class ReplaceTextParameters(ParametersModel):
    file_path: str = Field(
        ...,
        description="Path to the file to modify (relative or absolute)",
    )
    old_content: str = Field(
        ...,
        description="Exact text to search for and replace",
    )
    new_content: str = Field(
        ...,
        description="New text to replace the old content with",
    )
    count: int = Field(
        1,
        description="Which occurrence to replace (1-indexed). Use 0 or negative to replace all occurrences. Default is 1.",
    )
    encoding: str = Field(
        "utf-8",
        description="File encoding to use when reading/writing. Default is 'utf-8'.",
    )

    def info(self) -> str:
        summary = f'replace in "{self.file_path}"'
        if self.count == 0 or self.count < 0:
            summary += " (all occurrences)"
        else:
            summary += f" (occurrence #{self.count})"
        return summary


class ReplaceTextTool(BaseTool):
    name = "replace_text"
    description = "Replace exact text content in a file"
    description_long = """
- Replace specific text by providing the exact old content and new content
- Simpler than unified diff patches - just specify what to find and what to replace it with
- Use count parameter to control which occurrence gets replaced (default: first only)
- Set count=0 or negative to replace all occurrences
- The old_content must match exactly, including whitespace and newlines
- Good for small, targeted edits within files
- Use write_file tool if you need to replace the entire file content
"""
    parameters_model = ReplaceTextParameters

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Replace text in a file."""
        file_path: str = kwargs["file_path"]
        old_content: str = kwargs["old_content"]
        new_content: str = kwargs["new_content"]
        count: int = kwargs.get("count", 1)
        encoding: str = kwargs.get("encoding", "utf-8")
        # Validate file exists
        if not os.path.isfile(file_path):
            return format_error_with_context(file_path, "File not found")

        # Read file content
        try:
            with open(file_path, "r", encoding=encoding) as f:
                original_content = f.read()
        except Exception as e:
            return format_error_with_context(file_path, f"Failed to read file: {e}")

        # Find all occurrences
        occurrences = []
        search_start = 0
        while True:
            idx = original_content.find(old_content, search_start)
            if idx == -1:
                break
            occurrences.append(idx)
            search_start = idx + len(old_content)

        total_occurrences = len(occurrences)

        if total_occurrences == 0:
            return format_error_with_context(
                file_path,
                "old_content not found. Make sure the content matches exactly (including whitespace and newlines).",
            )

        # Determine which occurrences to replace
        if count <= 0:
            # Replace all
            indices_to_replace = occurrences
        else:
            # Replace specific occurrence (1-indexed)
            if count > total_occurrences:
                return format_error_with_context(
                    file_path,
                    f"Only found {total_occurrences} occurrence(s), cannot replace occurrence #{count} (count out of range)",
                )
            indices_to_replace = [occurrences[count - 1]]

        # Perform replacement from end to start to preserve indices
        new_file_content = original_content
        for idx in reversed(indices_to_replace):
            new_file_content = (
                new_file_content[:idx]
                + new_content
                + new_file_content[idx + len(old_content) :]
            )

        # create diff that will be part of result message
        diff = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            new_file_content.splitlines(keepends=True),
            fromfile="old",
            tofile="new",
        )
        txt_diff = "".join(diff)
        # Write back to file
        try:
            with open(file_path, "w", encoding=encoding) as f:
                f.write(new_file_content)
        except Exception as e:
            return format_error_with_context(file_path, f"Failed to write file: {e}")

        num_replacements = len(indices_to_replace)
        result_msg = f"Replaced {num_replacements} occurrence(s) in {file_path}"
        if num_replacements < total_occurrences and count > 0:
            result_msg += f" (left {total_occurrences - num_replacements} unchanged)"
        result_msg += "\n\n" + txt_diff
        return result_msg
