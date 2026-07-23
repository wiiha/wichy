"""Tests for the generate_chart agent tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from wichy.tools.generate_chart import GenerateChartTool
from wichy.tools.registry import get_tool_by_name


@pytest.fixture
def isolated_charts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated charts directory for each test."""
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    from wichy.config.settings import settings

    monkeypatch.setattr(type(settings), "charts_dir", property(lambda self: charts_dir))
    return charts_dir


@pytest.fixture
def loaded_test_table() -> str:
    """Load a test table into DuckDB and return its name.

    Uses DuckDB's in-memory database to create a small test table.
    """
    from wichy.tools.duckdb_manager import DuckDBManager
    from wichy.tools.duckdb_reset import DuckDBResetTool

    # Reset to ensure clean state
    DuckDBResetTool().execute()

    manager = DuckDBManager.get_instance()
    with manager.get_connection() as conn:
        conn.execute(
            "CREATE TABLE test_chart_data AS "
            "SELECT range AS id, 'cat_' || (range % 3) AS category, "
            "range * 10.0 AS value FROM range(0, 20)"
        )
    return "test_chart_data"


class TestGenerateChartToolRegistration:
    """Test tool registration."""

    def test_tool_registered(self) -> None:
        """GenerateChartTool is registered in the tool registry."""
        tool = get_tool_by_name("generate_chart")
        assert tool is not None
        assert tool.name == "generate_chart"

    def test_tool_in_all_tools(self) -> None:
        """GenerateChartTool appears in the tools package __all__."""
        from wichy.tools import GenerateChartTool as ImportedTool

        assert ImportedTool is GenerateChartTool


class TestGenerateChartWithTable:
    """Test chart generation using a table name as data source."""

    def test_returns_file_path(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Tool returns a file path string when using a table name."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="bar",
            config={"x": "category", "y": "value", "title": "Test Bar"},
        )

        # Result should be a file path string
        assert isinstance(result, str)
        assert result.endswith(".png")
        assert str(isolated_charts_dir) in result or ".wichy/charts" in result

        # The PNG file should exist
        path = Path(result)
        assert path.exists()

    def test_file_path_only_no_multimodal(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Returned string is just a path — no JSON/multimodal wrapper (INV-008)."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="scatter",
            config={"x": "id", "y": "value"},
        )

        assert isinstance(result, str)
        assert not result.startswith("{")  # Not JSON
        assert "multimodal" not in result.lower()
        assert result.endswith(".png")


class TestGenerateChartWithSQL:
    """Test chart generation using a SQL query as data source."""

    def test_sql_source(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Tool works with a SQL SELECT query as data source."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source=f"SELECT category, COUNT(*) as count FROM {loaded_test_table} GROUP BY category",
            chart_type="bar",
            config={"x": "category", "y": "count"},
        )

        assert isinstance(result, str)
        assert result.endswith(".png")
        path = Path(result)
        assert path.exists()


class TestGenerateChartErrors:
    """Test error handling."""

    def test_invalid_table_name(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Invalid table name characters are rejected."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source="table; DROP TABLE test_chart_data; --",
            chart_type="bar",
            config={"x": "category", "y": "value"},
        )

        # Should return an error, not crash
        assert isinstance(result, str)
        assert "error" in result.lower() or "invalid" in result.lower()

    def test_unknown_chart_type(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Unknown chart type returns an error message."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="nonexistent",
            config={},
        )

        assert isinstance(result, str)
        assert "error" in result.lower() or "unknown" in result.lower()

    def test_invalid_config(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Invalid config (missing required fields) returns an error."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source=loaded_test_table,
            chart_type="bar",
            config={},  # Missing required x and y
        )

        assert isinstance(result, str)
        assert "error" in result.lower() or "invalid" in result.lower()

    def test_nonexistent_table(
        self, isolated_charts_dir: Path, loaded_test_table: str
    ) -> None:
        """Nonexistent table returns an error."""
        tool = GenerateChartTool()
        result = tool.execute(
            data_source="nonexistent_table_xyz",
            chart_type="bar",
            config={"x": "a", "y": "b"},
        )

        assert isinstance(result, str)
        assert "error" in result.lower() or "does not exist" in result.lower()
