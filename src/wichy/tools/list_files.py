import subprocess
from typing import Any, Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class ListFilesParameters(ParametersModel):
    path: Optional[str] = Field(
        ".",
        description="directory of which to list files for, default=current directory",
    )

    def info(self):
        path = "."
        if self.path:
            path = self.path
        return 'path="' + path + '"'


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files in a directory"
    parameters_model = ListFilesParameters
    needs_verification_in_api: bool = False

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute file listing"""
        path: str = kwargs.get("path", ".")
        try:
            result = subprocess.run(
                ["ls", "-l", path],
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
            return result.stdout
        except Exception as e:
            return format_error(str(e))
