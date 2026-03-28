"""
Test cases for the WriteFileTool.
"""

import os
import tempfile

import pytest

from wichy.tools.write_file import WriteFileTool


@pytest.fixture
def write_file_tool():
    """Fixture to create a fresh WriteFileTool instance for each test."""
    return WriteFileTool()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestWriteFile:
    """Tests for writing files."""

    def test_write_file_basic(self, write_file_tool, temp_workspace):
        """Test basic file writing."""
        test_file = os.path.join(temp_workspace, "output.txt")
        content = "Hello, World!\nThis is a test file."

        result = write_file_tool.execute(path=test_file, content=content)

        assert "Successfully wrote" in result
        assert test_file in result

        # Verify file was written correctly
        with open(test_file, "r") as f:
            assert f.read() == content

    def test_write_file_creates_nested_directories(
        self, write_file_tool, temp_workspace
    ):
        """Test that writing creates nested directories if they don't exist."""
        nested_file = os.path.join(temp_workspace, "deep", "nested", "dir", "file.txt")
        content = "Nested content"

        result = write_file_tool.execute(path=nested_file, content=content)

        assert "Successfully wrote" in result

        # Verify file was created
        assert os.path.exists(nested_file)
        with open(nested_file, "r") as f:
            assert f.read() == content

    def test_write_file_overwrites_existing(self, write_file_tool, temp_workspace):
        """Test that writing overwrites existing file content."""
        test_file = os.path.join(temp_workspace, "overwrite.txt")

        # Write initial content
        initial_content = "Initial content"
        write_file_tool.execute(path=test_file, content=initial_content)

        # Overwrite with new content
        new_content = "New content that replaces the old"
        result = write_file_tool.execute(path=test_file, content=new_content)

        assert "Successfully wrote" in result

        # Verify content was replaced
        with open(test_file, "r") as f:
            assert f.read() == new_content

    def test_write_file_empty_content(self, write_file_tool, temp_workspace):
        """Test writing an empty file."""
        test_file = os.path.join(temp_workspace, "empty.txt")

        result = write_file_tool.execute(path=test_file, content="")

        assert "Successfully wrote" in result

        # Verify empty file was created
        assert os.path.exists(test_file)
        with open(test_file, "r") as f:
            assert f.read() == ""

    def test_write_file_multiline_content(self, write_file_tool, temp_workspace):
        """Test writing multiline content."""
        test_file = os.path.join(temp_workspace, "multiline.txt")
        content = "Line 1\nLine 2\nLine 3\nLine 4\n"

        result = write_file_tool.execute(path=test_file, content=content)

        assert "Successfully wrote" in result

        # Verify multiline content
        with open(test_file, "r") as f:
            assert f.read() == content
