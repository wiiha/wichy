def get_tool_definitions(tools):
    """Convert a list of tools to function definitions."""
    return [tool.to_function_definition() for tool in tools]
