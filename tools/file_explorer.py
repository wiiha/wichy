from pydantic import BaseModel, Field
from typing import Optional
import subprocess
from .base import BaseTool


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