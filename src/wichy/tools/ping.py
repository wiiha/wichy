import subprocess
from typing import Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel


class PingParameters(ParametersModel):
    host: str = Field(..., description="hostname or IP address to ping")
    count: Optional[int] = Field(3, description="number of pings to try, max 5")

    def info(self):
        return f'host="{self.host}" count={self.count}'


class PingTool(BaseTool):
    name = "ping"
    description = "Ping a host on the internet to check connectivity"
    parameters_model = PingParameters

    def execute(self, host: str, count: int = 3) -> str:
        """Execute ping command."""
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
            return f"error: {e}"
