import os

from pydantic import Field

from wichy.helpers.string import truncate_to_len
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


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
    description_long = """Writes a file to the local filesystem.

Usage:

- This tool will overwrite the existing file if there is one at the provided path. Hence, a file update needs to contain the full new version of the content.
- If this is an existing file, you MUST use the cat tool first to read the file's contents. This tool will fail if you did not read the file first.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
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
            return format_error(f"Failed to write to {path}: {e}")
