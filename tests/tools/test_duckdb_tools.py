"""Tests for DuckDB tools."""

import os
import tempfile
import pytest

from wichy.tools.duckdb_manager import DuckDBManager
from wichy.tools.duckdb_load import DuckDBLoadTool
from wichy.tools.duckdb_query import DuckDBQueryTool
from wichy.tools.duckdb_schema import DuckDBSchemaTool
from wichy.tools.duckdb_status import DuckDBStatusTool
from wichy.tools.duckdb_persist import DuckDBPersistTool, DuckDBLoadDBTool
from wichy.tools.duckdb_reset import DuckDBResetTool


@pytest.fixture(autouse=True)
def reset_duckdb():
    """Reset DuckDB manager before each test."""
    DuckDBManager.reset()
    yield
    DuckDBManager.reset()


@pytest.fixture
def sample_csv():
    """Create a sample CSV file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("name,age,city\n")
        f.write("Alice,30,Stockholm\n")
        f.write("Bob,25,Oslo\n")
        f.write("Charlie,35,Copenhagen\n")
        f.flush()
        yield f.name
    os.unlink(f.name)


class TestDuckDBManager:
    """Tests for DuckDBManager singleton."""

    def test_singleton_pattern(self):
        """Test that manager is a singleton."""
        manager1 = DuckDBManager.get_instance()
        manager2 = DuckDBManager.get_instance()
        assert manager1 is manager2

    def test_get_connection(self):
        """Test that connection is created."""
        manager = DuckDBManager.get_instance()
        conn = manager.get_connection()
        assert conn is not None

    def test_reset_clears_state(self):
        """Test that reset clears all state."""
        manager = DuckDBManager.get_instance()
        with manager.get_connection():
            pass
        DuckDBManager.reset()
        assert DuckDBManager._instance is None
        assert DuckDBManager._pool is None


class TestDuckDBLoadTool:
    """Tests for DuckDBLoadTool."""

    def test_load_csv(self, sample_csv):
        """Test loading a CSV file."""
        tool = DuckDBLoadTool()
        result = tool.execute(data_path=sample_csv)
        assert "Loaded" in result

    def test_load_nonexistent_file(self):
        """Test error handling for missing file."""
        tool = DuckDBLoadTool()
        result = tool.execute(data_path="/nonexistent/file.csv")
        assert "error" in result.lower()

    def test_load_with_custom_table_name(self, sample_csv):
        """Test loading with custom table name."""
        tool = DuckDBLoadTool()
        result = tool.execute(data_path=sample_csv, table_name="my_table")
        assert "Loaded" in result
        assert "my_table" in result


class TestDuckDBSchemaTool:
    """Tests for DuckDBSchemaTool."""

    def test_schema_no_tables(self):
        """Test schema when no tables loaded."""
        tool = DuckDBSchemaTool()
        result = tool.execute()
        assert "No tables loaded" in result

    def test_schema_after_load(self, sample_csv):
        """Test schema after loading data."""
        load_tool = DuckDBLoadTool()
        load_tool.execute(data_path=sample_csv)

        schema_tool = DuckDBSchemaTool()
        result = schema_tool.execute()
        assert "Table:" in result
        assert "Column" in result
        assert "Type" in result


class TestDuckDBQueryTool:
    """Tests for DuckDBQueryTool."""

    def test_query_no_tables(self):
        """Test query when no tables loaded."""
        tool = DuckDBQueryTool()
        result = tool.execute(query="SELECT * FROM nonexistent")
        assert "error" in result.lower() or "Catalog Error" in result

    def test_query_after_load(self, sample_csv):
        """Test query after loading data."""
        load_tool = DuckDBLoadTool()
        # Use custom table name to have predictable name
        load_tool.execute(data_path=sample_csv, table_name="test_data")

        query_tool = DuckDBQueryTool()
        result = query_tool.execute(query="SELECT * FROM test_data LIMIT 2")
        assert "Alice" in result or "Bob" in result or "rows" in result.lower()

    def test_query_with_limit(self, sample_csv):
        """Test query result limiting."""
        load_tool = DuckDBLoadTool()
        load_tool.execute(data_path=sample_csv, table_name="test_data")

        query_tool = DuckDBQueryTool()
        result = query_tool.execute(query="SELECT * FROM test_data", limit=1)
        assert "limit reached" in result or "row" in result.lower()


class TestDuckDBStatusTool:
    """Tests for DuckDBStatusTool."""

    def test_status_empty(self):
        """Test status with no data."""
        tool = DuckDBStatusTool()
        result = tool.execute()
        assert "in-memory" in result
        assert "Tables loaded: 0" in result

    def test_status_with_data(self, sample_csv):
        """Test status after loading data."""
        load_tool = DuckDBLoadTool()
        load_tool.execute(data_path=sample_csv)

        status_tool = DuckDBStatusTool()
        result = status_tool.execute()
        assert "Tables loaded: 1" in result


class TestDuckDBResetTool:
    """Tests for DuckDBResetTool."""

    def test_reset(self, sample_csv):
        """Test reset clears data."""
        load_tool = DuckDBLoadTool()
        load_tool.execute(data_path=sample_csv)

        reset_tool = DuckDBResetTool()
        result = reset_tool.execute()
        assert "reset" in result.lower()

        status_tool = DuckDBStatusTool()
        status = status_tool.execute()
        assert "Tables loaded: 0" in status


class TestDuckDBPersistence:
    """Tests for persistence functionality."""

    def test_persist_and_load(self, sample_csv):
        """Test persisting and loading database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.duckdb")

            # Load data
            load_tool = DuckDBLoadTool()
            load_tool.execute(data_path=sample_csv, table_name="test_table")

            # Persist
            persist_tool = DuckDBPersistTool()
            result = persist_tool.execute(db_path=db_path)
            assert "persisted" in result.lower() or "error" not in result.lower()

            # Reset
            DuckDBManager.reset()

            # Load database
            load_db_tool = DuckDBLoadDBTool()
            result = load_db_tool.execute(db_path=db_path)
            assert "Loaded database" in result or "error" not in result.lower()

            # Verify data still exists
            status_tool = DuckDBStatusTool()
            status = status_tool.execute()
            assert "Tables loaded: 1" in status


class TestToolDefinitions:
    """Test that tools have proper definitions."""

    def test_load_tool_definition(self):
        """Test DuckDBLoadTool has proper definition."""
        tool = DuckDBLoadTool()
        assert tool.name == "duckdb_load"
        assert tool.description != ""
        assert tool.description_long != ""
        assert hasattr(tool, "parameters_model")

    def test_query_tool_definition(self):
        """Test DuckDBQueryTool has proper definition."""
        tool = DuckDBQueryTool()
        assert tool.name == "duckdb_query"
        assert tool.description != ""
        assert tool.description_long != ""
        assert hasattr(tool, "parameters_model")

    def test_schema_tool_definition(self):
        """Test DuckDBSchemaTool has proper definition."""
        tool = DuckDBSchemaTool()
        assert tool.name == "duckdb_schema"
        assert tool.description != ""
        assert tool.description_long != ""
        assert hasattr(tool, "parameters_model")

    def test_all_tools_registered(self):
        """Test that all DuckDB tools are registered in the tool registry."""
        from wichy.tools import get_tool_by_name

        # Verify all 7 DuckDB tools are registered
        expected_tools = [
            "duckdb_load",
            "duckdb_query",
            "duckdb_schema",
            "duckdb_status",
            "duckdb_reset",
            "duckdb_persist",
            "duckdb_load_db",
        ]

        for tool_name in expected_tools:
            tool_class = get_tool_by_name(tool_name)
            assert tool_class is not None, f"Tool '{tool_name}' not registered"
            assert tool_class.name == tool_name
