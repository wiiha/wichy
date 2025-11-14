from pydantic import BaseModel, Field
from typing import Optional
import subprocess
from .base import BaseTool
from .human_verification import require_human_verification


class PingParameters(BaseModel):
    host: str = Field(..., description="hostname or IP address to ping")
    count: Optional[int] = Field(3, description="number of pings to try, max 5")

class PingTool(BaseTool):
    name = "ping"
    description = "Ping a host on the internet to check connectivity"
    parameters_model = PingParameters
    
    @require_human_verification
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