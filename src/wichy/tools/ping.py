import subprocess
from typing import Any, Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class PingParameters(ParametersModel):
    host: str = Field(..., description="hostname or IP address to ping")
    count: Optional[int] = Field(3, description="number of pings to try, max 5")

    def info(self):
        return f'host="{self.host}" count={self.count}'


class PingTool(BaseTool):
    name = "ping"
    description = "Ping a host on the internet to check connectivity"
    parameters_model = PingParameters
    needs_verification_in_api: bool = False

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """Execute ping command."""
        host: str = kwargs["host"]
        count: int = kwargs.get("count", 3)
        if count > 5:
            count = 5
        try:
            result = subprocess.run(
                ["ping", "-c", str(count), host],
                text=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
            return result.stdout
        except Exception as e:
            return format_error(str(e))
