"""Test MCP server for manually testing wichy's MCP integration.

Run it standalone to verify it works:
    python /workspace/test_mcp_server.py
"""
from fastmcp import FastMCP

mcp = FastMCP("test")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b

@mcp.tool()
def greet(name: str, formal: bool = False) -> str:
    """Greet someone."""
    if formal:
        return f"Good day, {name}."
    return f"Hey {name}!"

@mcp.tool()
def echo(message: str) -> str:
    """Echo back a message."""
    return message

if __name__ == "__main__":
    mcp.run()