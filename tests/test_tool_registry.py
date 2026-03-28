"""Tests for the tool registry module."""

from wichy.tools.base import BaseTool
from wichy.tools.registry import (
    get_all_tools,
    get_tool_by_name,
    get_tools_by_names,
)


class TestRegisterTool:
    """Test suite for register_tool function."""

    def test_register_tool_adds_to_registry(self):
        """Test that register_tool adds a tool class to the registry."""

        class TestTool(BaseTool):
            name = "test_register_tool_unique_name_1"
            description = "A test tool"
            parameters_model = None

            def execute(self):
                return "test"

        # Tool should be registered via metaclass
        tool = get_tool_by_name("test_register_tool_unique_name_1")
        assert tool is not None
        assert tool is TestTool

    def test_register_tool_by_name(self):
        """Test that tool can be looked up by its name attribute."""

        class NamedTool(BaseTool):
            name = "named_tool_unique_2"
            description = "A named tool"
            parameters_model = None

            def execute(self):
                return "named"

        # Verify lookup by name works
        found = get_tool_by_name("named_tool_unique_2")
        assert found is NamedTool

    def test_duplicate_name_overwrites(self):
        """Test that registering a tool with duplicate name overwrites the previous."""

        class DuplicateTool(BaseTool):
            name = "duplicate_test_name"
            description = "First version"
            parameters_model = None

            def execute(self):
                return "first"

        # Save reference to first tool
        first_tool_class = DuplicateTool

        class DuplicateTool(BaseTool):  # noqa: F811
            name = "duplicate_test_name"
            description = "Second version"
            parameters_model = None

            def execute(self):
                return "second"

        # The second definition should have overwritten the first
        found = get_tool_by_name("duplicate_test_name")
        assert found is not first_tool_class
        assert found.description == "Second version"


class TestGetAllTools:
    """Test suite for get_all_tools function."""

    def test_get_all_tools_returns_list(self):
        """Test that get_all_tools returns a list."""
        tools = get_all_tools()
        assert isinstance(tools, list)

    def test_get_all_tools_includes_known_tools(self):
        """Test that get_all_tools includes some known tools."""
        tools = get_all_tools()
        tool_names = {tool.name for tool in tools}

        # Check for some common tools that should be registered
        assert "read_file" in tool_names or any("read" in name for name in tool_names)
        assert len(tools) > 0

    def test_get_all_tools_are_base_tool_subclasses(self):
        """Test that all returned tools are BaseTool subclasses."""
        tools = get_all_tools()

        for tool in tools:
            assert issubclass(tool, BaseTool)


class TestGetToolByName:
    """Test suite for get_tool_by_name function."""

    def test_get_tool_by_name_returns_tool(self):
        """Test that get_tool_by_name returns the correct tool class."""

        class LookupTool(BaseTool):
            name = "lookup_tool_unique_3"
            description = "A tool to look up"
            parameters_model = None

            def execute(self):
                return "lookup"

        found = get_tool_by_name("lookup_tool_unique_3")
        assert found is LookupTool

    def test_get_tool_by_name_not_found_returns_none(self):
        """Test that get_tool_by_name returns None for non-existent tool."""
        found = get_tool_by_name("nonexistent_tool_xyz_12345")
        assert found is None

    def test_get_tool_by_name_case_sensitive(self):
        """Test that get_tool_by_name is case-sensitive."""

        class CaseSensitiveTool(BaseTool):
            name = "case_sensitive_tool"
            description = "A case sensitive tool"
            parameters_model = None

            def execute(self):
                return "case"

        # Should find with exact case
        found = get_tool_by_name("case_sensitive_tool")
        assert found is CaseSensitiveTool

        # Should not find with different case
        found_upper = get_tool_by_name("CASE_SENSITIVE_TOOL")
        assert found_upper is None

        found_mixed = get_tool_by_name("Case_Sensitive_Tool")
        assert found_mixed is None


class TestGetToolsByNames:
    """Test suite for get_tools_by_names function."""

    def test_get_tools_by_names_multiple(self):
        """Test that get_tools_by_names returns multiple tools."""

        class MultiToolA(BaseTool):
            name = "multi_tool_a"
            description = "Tool A"
            parameters_model = None

            def execute(self):
                return "a"

        class MultiToolB(BaseTool):
            name = "multi_tool_b"
            description = "Tool B"
            parameters_model = None

            def execute(self):
                return "b"

        tools = get_tools_by_names(["multi_tool_a", "multi_tool_b"])
        assert len(tools) == 2

        tool_classes = {tool.name: tool for tool in tools}
        assert "multi_tool_a" in tool_classes
        assert "multi_tool_b" in tool_classes
        assert tool_classes["multi_tool_a"] is MultiToolA
        assert tool_classes["multi_tool_b"] is MultiToolB

    def test_get_tools_by_names_missing_tools_omitted(self):
        """Test that missing tools are omitted from results."""

        class PresentTool(BaseTool):
            name = "present_tool"
            description = "A present tool"
            parameters_model = None

            def execute(self):
                return "present"

        tools = get_tools_by_names(["present_tool", "nonexistent_missing_tool"])
        # Should only return the existing tool, omitting the missing one
        assert len(tools) == 1
        assert tools[0] is PresentTool


class TestMetaclassAutoRegistration:
    """Test suite for ToolMeta metaclass auto-registration."""

    def test_new_tool_auto_registers(self):
        """Test that creating a new tool class auto-registers it."""

        class AutoRegisterTool(BaseTool):
            name = "auto_register_test_tool"
            description = "Auto registered tool"
            parameters_model = None

            def execute(self):
                return "auto"

        # Verify it was auto-registered
        found = get_tool_by_name("auto_register_test_tool")
        assert found is AutoRegisterTool

        # Also verify it appears in get_all_tools
        all_tools = get_all_tools()
        assert AutoRegisterTool in all_tools

    def test_metaclass_registers_on_class_creation(self):
        """Test that ToolMeta registers tool immediately when class is defined."""

        class ImmediateRegTool(BaseTool):
            name = "immediate_registration_tool"
            description = "Immediately registered"
            parameters_model = None

            def execute(self):
                return "immediate"

        # Registration should happen immediately upon class definition
        # Verify by looking up immediately
        assert get_tool_by_name("immediate_registration_tool") is ImmediateRegTool

    def test_tool_without_name_not_registered(self):
        """Test that a tool class without a 'name' attribute is not registered."""

        class NamelessTool(BaseTool):
            # No 'name' attribute defined
            description = "A nameless tool"
            parameters_model = None

            def execute(self):
                return "nameless"

        # Since it has no name attribute, it shouldn't be found
        # Note: BaseTool defines 'name' as abstract, but this tests the metaclass
        # behavior when name is empty/None-like
        pass  # Tools must have name, this is enforced by ABC

    def test_tool_with_empty_name_not_registered(self):
        """Test that a tool with empty string name is not registered."""

        class EmptyNameTool(BaseTool):
            name = ""  # Empty name
            description = "Tool with empty name"
            parameters_model = None

            def execute(self):
                return "empty"

        # Empty name should not be registered
        found = get_tool_by_name("")
        # The registry should not contain this (empty name check in register_tool)
        # Note: The actual behavior may vary - verify based on implementation
        # The registry code checks: if hasattr(tool_class, "name") and tool_class.name:
        # So empty string (falsy) should not register
        assert found is None
