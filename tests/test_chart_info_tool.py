"""Tests for the chart_info agent tool and viz info formatting functions."""

from __future__ import annotations

import pytest

from wichy.tools.chart_info import ChartInfoTool
from wichy.tools.registry import get_tool_by_name
from wichy.tools.viz.info import (
    format_chart_requirements,
)


class TestChartInfoToolRegistration:
    """Test tool registration."""

    def test_tool_registered(self) -> None:
        """ChartInfoTool is registered in the tool registry."""
        tool = get_tool_by_name("chart_info")
        assert tool is not None
        assert tool.name == "chart_info"

    def test_tool_in_all_tools(self) -> None:
        """ChartInfoTool appears in the tools package __all__."""
        from wichy.tools import ChartInfoTool as ImportedTool

        assert ImportedTool is ChartInfoTool


class TestChartInfoSummary:
    """Test chart_info without a specific chart_type (all-types summary)."""

    def test_summary_lists_all_types(self) -> None:
        """Summary lists all 14 chart types."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type=None)

        assert isinstance(result, str)
        assert "14" in result  # "14 total"
        # Spot-check a few types
        for ct in ["bar", "scatter", "heatmap", "correlogram"]:
            assert ct in result

    def test_summary_shows_required_fields(self) -> None:
        """Summary includes required field names for each type."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type=None)

        # bar requires x and y
        assert "x" in result
        assert "y" in result
        # chord requires source, target, value
        assert "source" in result
        assert "target" in result
        assert "value" in result


class TestChartInfoDetail:
    """Test chart_info with a specific chart_type."""

    def test_bar_detail(self) -> None:
        """Full detail for bar chart includes required/optional fields and example."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="bar")

        assert isinstance(result, str)
        assert "Bar Chart" in result
        assert "bar" in result
        # Required fields
        assert "x" in result
        assert "y" in result
        assert "required" in result.lower()
        # Optional fields
        assert "color_by" in result
        assert "optional" in result.lower()
        # Example config
        assert "example" in result.lower()
        assert "title" in result  # example includes title

    def test_scatter_detail_includes_size_by(self) -> None:
        """Scatter detail includes both color_by and size_by as optional."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="scatter")

        assert "scatter" in result.lower() or "Scatter" in result
        assert "color_by" in result
        assert "size_by" in result

    def test_radar_detail_shows_multiple(self) -> None:
        """Radar detail shows that categories and values accept multiple columns."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="radar")

        assert "radar" in result.lower() or "Radar" in result
        assert "categories" in result
        assert "values" in result
        assert "multiple" in result.lower()

    def test_detail_includes_styling_fields(self) -> None:
        """Detail includes common styling fields like width, height, theme."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="line")

        assert "width" in result
        assert "height" in result
        assert "theme" in result

    def test_detail_includes_chart_specific_options(self) -> None:
        """Bar detail includes chart-specific options like orientation and mode."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="bar")

        assert "orientation" in result
        assert "mode" in result

    def test_distribution_detail_includes_subtype(self) -> None:
        """Distribution detail includes subtype option."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="distribution")

        assert "subtype" in result


class TestChartInfoUnknownType:
    """Test chart_info with an unknown chart type."""

    def test_unknown_type_returns_error(self) -> None:
        """Unknown chart type returns an error with available types listed."""
        tool = ChartInfoTool()
        result = tool.execute(chart_type="nonexistent")

        assert isinstance(result, str)
        assert "error" in result.lower() or "unknown" in result.lower()
        assert "bar" in result  # Available types are listed


class TestFormatChartRequirements:
    """Test the format_chart_requirements helper used in error messages."""

    def test_bar_requirements(self) -> None:
        """format_chart_requirements returns compact field listing for bar."""
        result = format_chart_requirements("bar")
        assert result is not None
        assert "bar" in result
        assert "x" in result
        assert "y" in result
        assert "color_by" in result

    def test_unknown_type_returns_none(self) -> None:
        """format_chart_requirements returns None for unknown type."""
        result = format_chart_requirements("nonexistent")
        assert result is None


class TestEnrichedErrorMessages:
    """Test that generate_chart errors now include schema guidance."""

    @pytest.fixture
    def loaded_test_table(self) -> str:
        """Load a test table into DuckDB and return its name."""
        from wichy.tools.duckdb_manager import DuckDBManager
        from wichy.tools.duckdb_reset import DuckDBResetTool

        DuckDBResetTool().execute()
        manager = DuckDBManager.get_instance()
        with manager.get_connection() as conn:
            conn.execute(
                "CREATE TABLE test_chart_data AS "
                "SELECT range AS id, 'cat_' || (range % 3) AS category, "
                "range * 10.0 AS value FROM range(0, 20)"
            )
        return "test_chart_data"

    def test_missing_required_fields_includes_schema(
        self, loaded_test_table: str
    ) -> None:
        """Error for missing required fields includes the field requirements."""
        from wichy.tools.generate_chart import GenerateChartTool

        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="bar",
            config={},  # Missing required x and y
        )

        assert isinstance(result, str)
        # Should mention the required fields
        assert "x" in result
        assert "y" in result
        assert "required" in result.lower()

    def test_unknown_chart_type_lists_available(self, loaded_test_table: str) -> None:
        """Error for unknown chart type lists all available types."""
        from wichy.tools.generate_chart import GenerateChartTool

        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="nonexistent",
            config={},
        )

        assert isinstance(result, str)
        assert "unknown" in result.lower() or "error" in result.lower()
        # Should list at least some available types
        assert "bar" in result
        assert "scatter" in result

    def test_config_error_includes_color_by_hint(self, loaded_test_table: str) -> None:
        """Error for bar chart includes optional color_by in the hint."""
        from wichy.tools.generate_chart import GenerateChartTool

        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="bar",
            config={},  # Missing required fields
        )

        assert isinstance(result, str)
        # The enriched error should mention optional fields too
        assert "color_by" in result
