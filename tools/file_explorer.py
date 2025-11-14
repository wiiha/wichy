from pydantic import BaseModel, Field
from typing import Optional
import subprocess
from .base import BaseTool
import os


class ListFilesParameters(BaseModel):
    path: Optional[str] = Field(
        ".",
        description="directory of which to list files for, default=current directory",
    )


class ListFilesTool(BaseTool):
    name = "ls"
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


class CatFileParameters(BaseModel):
    path: str = Field(
        ".",
        description="path to file for which to look at content of",
    )


class CatFileContentTool(BaseTool):
    name = "cat"
    description = "Get the content of a file."
    parameters_model = CatFileParameters

    def execute(self, path) -> str:
        """Execute file cat"""
        try:
            result = subprocess.run(
                ["cat", path],
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
            return result.stdout
        except Exception as e:
            return f"error: {e}"


class WriteFileParameters(BaseModel):
    path: str = Field(
        ...,
        description="path for file to write content into",
    )
    content: str = Field(..., description="content to write")


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to file at path. This will always overwrite the current content of a file. Hence, I file update needs to contain the full new version of the content."
    parameters_model = WriteFileParameters

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
