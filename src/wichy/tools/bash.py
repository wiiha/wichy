import subprocess
from typing import Optional

from pydantic import BaseModel, Field

from wichy.helpers.string import truncate_to_len

from .base import BaseTool, ParametersModel
from .human_verification import require_human_verification


class BashParameters(ParametersModel):
    command: str = Field(..., description="The command to execute")
    timeout: Optional[int] = Field(
        30, description="Timeout in seconds for the command execution"
    )

    def info(self):
        return f'command="{truncate_to_len(self.command)}" timeout={self.timeout}'


class BashTool(BaseTool):
    name = "bash"
    description = "Execute an arbitrary command using subprocess, imagine it being bash. Calls to this tool will be audited before execution."
    parameters_model = BashParameters

    @require_human_verification
    def execute(self, command: str, timeout: int = 30) -> str:
        """Execute the given command."""
        try:
            result = subprocess.run(
                command,  # Pass as string, not split
                shell=True,  # Enable shell processing
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
                timeout=timeout,
            )
            return result.stdout
        except Exception as e:
            return f"error: {e}"
