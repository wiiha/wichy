from pydantic import BaseModel, Field
from typing import Optional
import subprocess
from .base import BaseTool
from .human_verification import require_human_verification


class ExecuteCommandParameters(BaseModel):
    command: str = Field(..., description="The command to execute")
    timeout: Optional[int] = Field(
        30, description="Timeout in seconds for the command execution"
    )


class ExecuteCommandTool(BaseTool):
    name = "execute_command"
    description = "Execute an arbitrary command using subprocess. Calls to this tool will be audited before execution."
    parameters_model = ExecuteCommandParameters

    @require_human_verification
    def execute(self, command: str, timeout: int = 30) -> str:
        """Execute the given command."""
        try:
            result = subprocess.run(
                command.split(),
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
                timeout=timeout,
            )
            return result.stdout
        except Exception as e:
            return f"error: {e}"
