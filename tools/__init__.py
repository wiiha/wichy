from .ping import PingTool

# Different tool collections for different contexts
BASIC_TOOLS = [
    PingTool(),
]


# All tools combined
ALL_TOOLS = [
    PingTool(),
]


def get_tool_definitions(tools):
    """Convert a list of tools to function definitions."""
    return [tool.to_function_definition() for tool in tools]


__all__ = [
    'BASIC_TOOLS',
    'ALL_TOOLS',
    'get_tool_definitions',
]