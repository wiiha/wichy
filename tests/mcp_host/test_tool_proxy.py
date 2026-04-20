"""Tests for MCP tool proxy."""

import pytest

from wichy.mcp_host.tool_proxy import MCPToolProxy


class MockClient:
    """A mock MCP client for testing."""

    def __init__(self, call_tool_result="mock result"):
        self.call_tool_calls = []
        self._call_tool_result = call_tool_result

    def call_tool(self, tool_name, arguments):
        self.call_tool_calls.append((tool_name, arguments))
        return self._call_tool_result


@pytest.fixture
def simple_tool_def():
    """A simple tool definition with one required string param."""
    return {
        "name": "search",
        "description": "Search for items",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    }


@pytest.fixture
def multi_param_tool_def():
    """A tool with multiple parameter types."""
    return {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "integer", "description": "First number"},
                "b": {"type": "integer", "description": "Second number"},
                "verbose": {"type": "boolean", "description": "Verbose output"},
            },
            "required": ["a", "b"],
        },
    }


@pytest.fixture
def complex_tool_def():
    """A tool with complex ($ref) schema that should fall back."""
    return {
        "name": "complex_op",
        "description": "Complex operation",
        "inputSchema": {
            "type": "object",
            "$ref": "#/definitions/Something",
        },
    }


class TestMCPToolProxyNaming:
    """Test tool naming and namespacing."""

    def test_name_prefixed_with_server(self, simple_tool_def):
        """Tool name should be prefixed with server name."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, simple_tool_def)
        assert proxy.name == "myserver_search"

    def test_description_from_definition(self, simple_tool_def):
        """Tool description should come from the definition."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, simple_tool_def)
        assert proxy.description == "Search for items"

    def test_description_long_matches_description(self, simple_tool_def):
        """description_long should match description."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, simple_tool_def)
        assert proxy.description_long == proxy.description

    def test_default_description_when_missing(self):
        """Fallback description when tool definition has none."""
        client = MockClient()
        tool_def = {"name": "mytool", "inputSchema": {}}
        proxy = MCPToolProxy("srv", client, tool_def)
        assert proxy.description == "Tool from srv"


class TestMCPToolProxyParameters:
    """Test dynamic Pydantic parameter model creation."""

    def test_simple_schema_creates_model(self, simple_tool_def):
        """Simple JSON Schema should create a proper ParametersModel."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, simple_tool_def)

        assert proxy.parameters_model is not None
        fields = proxy.parameters_model.model_fields
        assert "query" in fields

    def test_multi_param_schema(self, multi_param_tool_def):
        """Multiple parameter types should be handled correctly."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, multi_param_tool_def)

        fields = proxy.parameters_model.model_fields
        assert "a" in fields
        assert "b" in fields
        assert "verbose" in fields

    def test_complex_schema_falls_back(self, complex_tool_def):
        """Schemas with $ref should fall back to generic dict parameter."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, complex_tool_def)

        fields = proxy.parameters_model.model_fields
        assert "arguments" in fields

    def test_any_of_schema_falls_back(self):
        """Schemas with anyOf should fall back to generic dict parameter."""
        client = MockClient()
        tool_def = {
            "name": "flexible",
            "description": "Flexible tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        }
        proxy = MCPToolProxy("srv", client, tool_def)
        fields = proxy.parameters_model.model_fields
        assert "arguments" in fields

    def test_one_of_schema_falls_back(self):
        """Schemas with oneOf should fall back to generic dict parameter."""
        client = MockClient()
        tool_def = {
            "name": "choice",
            "description": "Choice tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "option": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                },
            },
        }
        proxy = MCPToolProxy("srv", client, tool_def)
        fields = proxy.parameters_model.model_fields
        assert "arguments" in fields

    def test_empty_schema_creates_model(self):
        """Empty inputSchema should create a model with no fields."""
        client = MockClient()
        tool_def = {"name": "noop", "description": "No-op", "inputSchema": {}}
        proxy = MCPToolProxy("srv", client, tool_def)
        fields = proxy.parameters_model.model_fields
        assert len(fields) == 0

    def test_optional_parameters_with_defaults(self):
        """Optional (non-required) fields should accept None and have defaults."""
        client = MockClient()
        tool_def = {
            "name": "search",
            "description": "Search tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        }
        proxy = MCPToolProxy("srv", client, tool_def)

        fields = proxy.parameters_model.model_fields
        # Required field
        assert "query" in fields
        # Optional field present
        assert "limit" in fields
        # Verify the optional field uses the schema's default value
        model = proxy.parameters_model(query="test")
        assert model.query == "test"
        assert model.limit == 10  # Schema default applied
        # Verify optional field can be overridden
        model2 = proxy.parameters_model(query="test", limit=5)
        assert model2.limit == 5

    def test_schema_conversion_error_falls_back(self):
        """If _build_model_from_schema raises, should fall back to generic dict."""
        client = MockClient()
        # A schema with a property name that collides with Pydantic internals
        # (model_ prefix is reserved by Pydantic and will raise)
        tool_def = {
            "name": "broken",
            "description": "Broken tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_validate": {"type": "string"},
                },
            },
        }
        proxy = MCPToolProxy("srv", client, tool_def)
        # Should fall back to generic dict argument model
        fields = proxy.parameters_model.model_fields
        assert "arguments" in fields

    def test_unknown_json_type_defaults_to_str(self):
        """Unknown JSON types should map to str."""
        schema = {"type": "superinteger"}
        proxy = MCPToolProxy.__new__(MCPToolProxy)
        result = proxy._json_type_to_python(schema)
        assert result is str

    def test_json_type_mapping(self):
        """Verify JSON Schema types map to Python types correctly."""
        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
        }

        for type_str, expected_python_type in type_map.items():
            result = MCPToolProxy.__new__(MCPToolProxy)._json_type_to_python(
                {"type": type_str}
            )
            assert (
                result is expected_python_type
            ), f"{type_str} should map to {expected_python_type}"


class TestMCPToolProxyExecution:
    """Test tool execution delegation."""

    def test_execute_delegates_to_client(self, simple_tool_def):
        """Execute should delegate to client.call_tool."""
        client = MockClient(call_tool_result="search results")
        proxy = MCPToolProxy("myserver", client, simple_tool_def)

        result = proxy.execute(query="test query")
        assert result == "search results"
        assert client.call_tool_calls == [("search", {"query": "test query"})]

    def test_execute_passes_kwargs(self, multi_param_tool_def):
        """Execute should pass through all keyword arguments."""
        client = MockClient(call_tool_result="42")
        proxy = MCPToolProxy("myserver", client, multi_param_tool_def)

        result = proxy.execute(a=20, b=22, verbose=True)
        assert result == "42"
        assert client.call_tool_calls[0] == ("add", {"a": 20, "b": 22, "verbose": True})

    def test_execute_returns_error_string(self):
        """When client returns an error string, execute should pass it through."""
        client = MockClient(call_tool_result="[MCP Error] server/tool: network error")
        tool_def = {
            "name": "failing",
            "description": "A failing tool",
            "inputSchema": {},
        }
        proxy = MCPToolProxy("srv", client, tool_def)

        result = proxy.execute()
        assert "[MCP Error]" in result


class TestMCPToolProxyInfo:
    """Test the info() method."""

    def test_info_format(self, simple_tool_def):
        """info() should return '[MCP] {name}'."""
        client = MockClient()
        proxy = MCPToolProxy("myserver", client, simple_tool_def)
        assert proxy.info() == "[MCP] myserver_search"
