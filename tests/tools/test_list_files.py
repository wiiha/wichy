"""
Test cases for the ListFilesTool.
"""

import os
import tempfile

import pytest

from wichy.tools.list_files import ListFilesTool


@pytest.fixture
def list_files_tool():
    """Fixture to create a fresh ListFilesTool instance for each test."""
    return ListFilesTool()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test text file
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("line 1\nline 2\nline 3\n")

        # Create a subdirectory
        subdir = os.path.join(tmpdir, "subdir")
        os.makedirs(subdir)

        # Create file in subdirectory
        sub_file = os.path.join(subdir, "nested.txt")
        with open(sub_file, "w") as f:
            f.write("nested content\n")

        yield tmpdir


class TestListFiles:
    """Tests for listing files in directories."""

    def test_list_files_basic(self, list_files_tool, temp_workspace):
        """Test basic directory listing."""
        result = list_files_tool.execute(path=temp_workspace)

        assert "test.txt" in result
        assert "subdir" in result

    def test_list_files_nonexistent_directory(self, list_files_tool):
        """Test listing a non-existent directory."""
        result = list_files_tool.execute(path="/nonexistent/directory/path")

        # Should contain error indication
        assert "error" in result.lower() or "no such file" in result.lower()

    def test_list_files_default_path(self, list_files_tool):
        """Test listing with default path (current directory)."""
        result = list_files_tool.execute()

        # Default path is "." which should list current directory
        # The result should contain something (at minimum total line)
        assert result is not None
        assert len(result) > 0

    def test_list_files_subdirectory(self, list_files_tool, temp_workspace):
        """Test listing a subdirectory."""
        subdir = os.path.join(temp_workspace, "subdir")
        result = list_files_tool.execute(path=subdir)

        assert "nested.txt" in result
