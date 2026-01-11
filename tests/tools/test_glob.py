"""
Test cases for the GlobTool.
"""

import pytest
import os
import tempfile
from wichy.tools.glob import GlobTool


@pytest.fixture
def glob_tool():
    """Fixture to create a fresh GlobTool instance for each test."""
    return GlobTool()


def test_glob_pattern_matching(glob_tool):
    """Test glob pattern matching with various patterns."""
    # Test with a pattern that should match Python files
    result = glob_tool.execute(pattern="*.py", path="src/wichy/tools")
    assert "No files found" not in result
    assert ".py" in result
    assert "Found" in result


def test_glob_no_matches(glob_tool):
    """Test glob pattern with no matches."""
    result = glob_tool.execute(pattern="*.nonexistent", path="src/wichy/tools")
    assert "No files found" in result


def test_glob_sorting_by_modification_time(glob_tool):
    """Test that files are sorted by modification time (newest first)."""
    # Create a temporary directory with files of known modification times
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create files with different modification times
        file1 = os.path.join(tmpdir, "file1.txt")
        file2 = os.path.join(tmpdir, "file2.txt")
        
        with open(file1, "w") as f:
            f.write("old file")
        
        # Sleep to ensure different modification times
        import time
        time.sleep(0.1)
        
        with open(file2, "w") as f:
            f.write("new file")
        
        # Execute glob search
        result = glob_tool.execute(pattern="*.txt", path=tmpdir)
        
        # Verify sorting
        assert "file2.txt" in result
        assert "file1.txt" in result
        # file2 should appear before file1 (newest first)
        assert result.index("file2.txt") < result.index("file1.txt")


def test_glob_recursive_search(glob_tool):
    """Test recursive glob search."""
    # Create a temporary directory structure
    with tempfile.TemporaryDirectory() as tmpdir:
        subdir = os.path.join(tmpdir, "subdir")
        os.makedirs(subdir)
        
        # Create files in subdirectory
        with open(os.path.join(subdir, "nested.txt"), "w") as f:
            f.write("nested file")
        
        # Execute recursive glob search
        result = glob_tool.execute(pattern="**/*.txt", path=tmpdir)
        
        # Verify nested file is found
        assert "nested.txt" in result


def test_glob_invalid_path(glob_tool):
    """Test glob with invalid path."""
    result = glob_tool.execute(pattern="*.py", path="/nonexistent/path")
    assert "error" in result or "No files found" in result


def test_glob_default_path(glob_tool):
    """Test glob with default path (current directory)."""
    # Save current directory
    original_dir = os.getcwd()
    
    try:
        # Change to a directory with known files
        os.chdir("src/wichy/tools")
        result = glob_tool.execute(pattern="*.py")
        
        # Verify results
        assert "No files found" not in result
        assert ".py" in result
    finally:
        # Restore original directory
        os.chdir(original_dir)
