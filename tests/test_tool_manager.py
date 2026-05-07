"""Tests for the ToolManager class."""

from wichy.tool_manager import ToolManager
from wichy.tools.base import BaseTool


class MockTool(BaseTool):
    """Mock tool for testing."""

    name = "mock_tool"
    description = "A mock tool for testing"
    parameters_model = None

    def execute(self):
        return "mock result"


class TestToolManager:
    """Test suite for ToolManager."""

    def test_instantiate_all(self):
        """Test that instantiate_all creates tool instances."""
        manager = ToolManager([MockTool])
        tools = manager.instantiate_all()

        assert len(tools) == 1
        assert isinstance(tools[0], MockTool)
        assert tools[0].name == "mock_tool"

    def test_instantiate_all_sorts_by_name(self):
        """Test that tools are sorted alphabetically by name."""

        class ToolB(BaseTool):
            name = "tool_b"
            description = "Tool B"
            parameters_model = None

            def execute(self):
                return "b"

        class ToolA(BaseTool):
            name = "tool_a"
            description = "Tool A"
            parameters_model = None

            def execute(self):
                return "a"

        manager = ToolManager([ToolB, ToolA])
        tools = manager.instantiate_all()

        assert tools[0].name == "tool_a"
        assert tools[1].name == "tool_b"

    def test_filter_tools_empty_filters(self):
        """Test filter_tools with no filters returns all tools."""
        manager = ToolManager()
        tools = manager.instantiate_all()
        filtered = manager.filter_tools(tools, "", "")

        assert filtered == tools

    def test_filter_tools_allowed_only(self):
        """Test filtering with only allowed list."""
        manager = ToolManager()
        tools = manager.instantiate_all()

        # Get two tool names for filtering
        if len(tools) >= 2:
            allowed_names = f"{tools[0].name},{tools[1].name}"
            filtered = manager.filter_tools(tools, allowed=allowed_names, excluded="")

            assert len(filtered) == 2
            filtered_names = {t.name for t in filtered}
            assert filtered_names == {tools[0].name, tools[1].name}

    def test_filter_tools_excluded_only(self):
        """Test filtering with only exclusion list."""
        manager = ToolManager()
        tools = manager.instantiate_all()

        if len(tools) >= 2:
            excluded_names = f"{tools[0].name}"
            filtered = manager.filter_tools(tools, allowed="", excluded=excluded_names)

            assert len(filtered) == len(tools) - 1
            assert tools[0].name not in {t.name for t in filtered}

    def test_filter_tools_allowed_and_excluded(self):
        """Test filtering with both allowed and exclusion lists."""
        manager = ToolManager()
        tools = manager.instantiate_all()

        if len(tools) >= 3:
            allowed_names = f"{tools[0].name},{tools[1].name},{tools[2].name}"
            excluded_names = tools[1].name
            filtered = manager.filter_tools(
                tools, allowed=allowed_names, excluded=excluded_names
            )

            assert len(filtered) == 2
            filtered_names = {t.name for t in filtered}
            assert filtered_names == {tools[0].name, tools[2].name}

    def test_filter_tools_case_insensitive(self):
        """Test that tool filtering is case-insensitive."""
        manager = ToolManager()
        tools = manager.instantiate_all()

        if tools:
            tool_name = tools[0].name
            # Use uppercase for filtering
            filtered = manager.filter_tools(
                tools, allowed=tool_name.upper(), excluded=""
            )
            assert len(filtered) == 1
            assert filtered[0].name == tool_name

    def test_filter_tools_ignores_empty_names(self):
        """Test that empty tool names in filter are handled gracefully."""
        manager = ToolManager()
        tools = manager.instantiate_all()

        if tools:
            # Include empty string in filter
            filtered = manager.filter_tools(
                tools, allowed=f"{tools[0].name},", excluded=""
            )
            assert len(filtered) == 1
            assert filtered[0].name == tools[0].name

    def test_create_tools_convenience(self):
        """Test create_tools convenience method."""
        manager = ToolManager([MockTool])
        tools = manager.create_tools()

        assert len(tools) == 1
        assert isinstance(tools[0], MockTool)

    def test_create_tools_with_filters(self):
        """Test create_tools with filters applied."""
        manager = ToolManager([MockTool])
        tools = manager.create_tools(allowed=MockTool.name)

        assert len(tools) == 1
        assert isinstance(tools[0], MockTool)

    def test_filter_tools_with_nonexistent_names(self):
        """Test filtering with names that don't exist returns empty list."""
        manager = ToolManager()
        tools = manager.instantiate_all()

        filtered = manager.filter_tools(
            tools, allowed="nonexistent_tool_xyz", excluded=""
        )
        assert len(filtered) == 0

    def test_tool_manager_default_uses_all_tools(self, isolated_tool_registry):
        """Test that ToolManager without args uses all tools from the registry."""
        from wichy.tools.registry import get_all_tools

        manager = ToolManager()
        expected = get_all_tools()
        assert manager.all_tools == expected
