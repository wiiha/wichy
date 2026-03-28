import subprocess
from typing import Optional

from pydantic import Field

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
