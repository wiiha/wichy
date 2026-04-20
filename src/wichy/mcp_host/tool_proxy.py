"""
Bridges MCP tools to wichy's native BaseTool interface.

MCPToolProxy dynamically wraps an MCP server tool as a wichy BaseTool,
creating a Pydantic parameters model from the tool's JSON Schema and
delegating execution to the MCP client.
"""

from __future__ import annotations

import json

from pydantic import Field, create_model

from wichy.tools.base import BaseTool, ParametersModel
from .client import MCPClient


class MCPToolProxy(BaseTool):
    """
    Proxies a tool from an MCP server to wichy's tool system.

    Extends BaseTool so MCP tools get:
    - Hook system (pre/post tool hooks)
    - Result offloading (if enabled)
    - Consistent error handling

    Note: `name` is set as an instance attribute in __init__, not as a
    class attribute. This prevents ToolMeta from auto-registering this
    class in the global tool registry — MCPToolProxy instances are
    dynamically created and registered separately.
    """

    # Class-level defaults (instance attributes will shadow these)
    description = "MCP tool"
    description_long = None
    enable_result_offload = False

    def __init__(
        self,
        server_name: str,
        client: MCPClient,
        tool_definition: dict,
    ):
        self._server_name = server_name
        self._client = client
        self._tool_name = tool_definition["name"]
        self._input_schema = tool_definition.get("inputSchema", {})

        # Instance attributes (shadow class defaults)
        self.name = f"{server_name}_{tool_definition['name']}"
        self.description = tool_definition.get(
            "description", f"Tool from {server_name}"
        )
        self.description_long = self.description

        # Create parameters model from JSON Schema
        self.parameters_model = self._create_parameters_model()

    def _create_parameters_model(self) -> type[ParametersModel]:
        """
        Create a Pydantic model from the tool's JSON Schema.

        Falls back to a generic dict parameter if schema is too complex.
        """
        schema = self._input_schema

        # Check for unsupported patterns — use json.dumps uniformly
        # to catch both top-level and nested $ref/anyOf/oneOf
        schema_str = json.dumps(schema)
        if "$ref" in schema_str or "anyOf" in schema_str or "oneOf" in schema_str:
            from wichy.console import user_console

            user_console.print(
                f"[yellow]Complex JSON Schema in '{self.name}', using generic parameters[/yellow]"
            )
            return self._fallback_model()

        try:
            return self._build_model_from_schema(schema)
        except Exception as e:
            from wichy.console import user_console

            user_console.print(
                f"[yellow]Schema conversion failed for '{self.name}': {e}[/yellow]"
            )
            return self._fallback_model()

    def _build_model_from_schema(self, schema: dict) -> type[ParametersModel]:
        """Build Pydantic model from simple JSON Schema."""
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        fields = {}

        for prop_name, prop_schema in properties.items():
            py_type = self._json_type_to_python(prop_schema)
            description = prop_schema.get("description")

            if prop_name in required:
                fields[prop_name] = (py_type, Field(..., description=description))
            else:
                default = prop_schema.get("default", None)
                fields[prop_name] = (
                    py_type | None,
                    Field(default=default, description=description),
                )

        return create_model(
            f"{self.name}_Parameters",
            __base__=ParametersModel,
            **fields,
        )

    def _fallback_model(self) -> type[ParametersModel]:
        """Fallback model for complex or unparseable schemas."""
        return create_model(
            f"{self.name}_Parameters",
            __base__=ParametersModel,
            arguments=(dict, Field(default={}, description="Tool arguments (JSON)")),
        )

    def _json_type_to_python(self, schema: dict) -> type:
        """Map JSON Schema types to Python types."""
        type_str = schema.get("type", "string")

        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        return type_map.get(type_str, str)

    def execute(self, **kwargs) -> str:
        """Execute the MCP tool. Returns result as string."""
        return self._client.call_tool(self._tool_name, kwargs)

    def info(self) -> str:
        """Return tool info for logging."""
        return f"[MCP] {self.name}"
