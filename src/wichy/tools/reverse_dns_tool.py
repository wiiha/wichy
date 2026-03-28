import socket

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error


class ReverseDnsParameters(ParametersModel):
    ip: str = Field(..., description="IP address to perform reverse DNS lookup on")

    def info(self):
        return self.ip


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
            return format_error(f"Failed to resolve IP {ip} - {str(e)}")
        except Exception as e:
            return format_error(f"An unexpected error occurred - {str(e)}")
