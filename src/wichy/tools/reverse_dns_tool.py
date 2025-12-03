from pydantic import BaseModel, Field
from typing import Optional
import socket

from .base import BaseTool


class ReverseDnsParameters(BaseModel):
    ip: str = Field(..., description="IP address to perform reverse DNS lookup on")


class ReverseDnsTool(BaseTool):
    name = "reverse_dns"
    description = "Resolve an IP address to its hostname (reverse DNS lookup) using socket lib. This is an active action."
    parameters_model = ReverseDnsParameters

    def execute(self, ip: str) -> str:
        """Execute reverse DNS lookup for the given IP address."""
        try:
            hostname = socket.gethostbyaddr(ip)[0]
            return f"IP {ip} resolves to hostname: {hostname}"
        except socket.herror as e:
            return f"error: Failed to resolve IP {ip} - {str(e)}"
        except Exception as e:
            return f"error: An unexpected error occurred - {str(e)}"
