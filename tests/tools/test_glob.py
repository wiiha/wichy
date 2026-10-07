"""
Test cases for the GlobTool.
"""

import pytest
import os
import tempfile
from pydantic import ValidationError

from wichy.tools.glob import GlobParameters, GlobTool


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


class _StepClock:
    """Fake monotonic clock advancing one step per call, for deterministic deadlines."""

    def __init__(self, step=1.0):
        self._t = 0.0
        self._step = step

    def monotonic(self):
        self._t += self._step
        return self._t


def _make_tree(root, n_top, n_sub):
    sub = os.path.join(root, "sub")
    os.makedirs(sub)
    for i in range(n_top):
        with open(os.path.join(root, f"top{i}.txt"), "w") as f:
            f.write("x")
    for i in range(n_sub):
        with open(os.path.join(sub, f"nested{i}.txt"), "w") as f:
            f.write("x")
    return sub


def _result_rows(result):
    """Return the numbered file rows of a glob result, ignoring the header."""
    return [
        line
        for line in result.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]


def test_glob_timeout_aborts_and_returns_partial(glob_tool, monkeypatch):
    """Deadline firing mid-walk returns the matches collected so far, labelled partial."""
    import wichy.tools.glob as glob_module

    with tempfile.TemporaryDirectory() as tmpdir:
        _make_tree(tmpdir, n_top=10, n_sub=10)

        monkeypatch.setattr(glob_module, "time", _StepClock(step=1.0))
        result = glob_tool.execute(pattern="**/*.txt", path=tmpdir, timeout=5)

        assert "TIMEOUT" in result
        assert "after 5s" in result
        assert 0 < len(_result_rows(result)) < 20


def test_glob_timeout_already_elapsed_returns_no_files(glob_tool, monkeypatch):
    """A deadline in the past aborts before any file is collected, without raising."""
    import wichy.tools.glob as glob_module

    with tempfile.TemporaryDirectory() as tmpdir:
        _make_tree(tmpdir, n_top=3, n_sub=2)

        monkeypatch.setattr(glob_module, "time", _StepClock(step=1e9))
        result = glob_tool.execute(pattern="**/*.txt", path=tmpdir, timeout=1)

        assert "TIMEOUT" in result
        assert "after 1s" in result
        assert _result_rows(result) == []


def test_glob_timeout_none_is_unbounded(glob_tool):
    """timeout=None disables the deadline entirely."""
    with tempfile.TemporaryDirectory() as tmpdir:
        _make_tree(tmpdir, n_top=3, n_sub=2)
        result = glob_tool.execute(pattern="**/*.txt", path=tmpdir, timeout=None)

        assert "TIMEOUT" not in result
        assert "top0.txt" in result
        assert "nested0.txt" in result


def test_glob_recursive_star_skips_hidden_directories(glob_tool):
    """A `**` segment matches neither hidden files nor files under hidden dirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".hidden"))
        with open(os.path.join(tmpdir, ".hidden", "secret.txt"), "w") as f:
            f.write("x")
        with open(os.path.join(tmpdir, "visible.txt"), "w") as f:
            f.write("x")

        result = glob_tool.execute(pattern="**/*.txt", path=tmpdir)

        assert "visible.txt" in result
        assert "secret.txt" not in result


def test_glob_parameters_reject_non_positive_timeout():
    """Validation rejects a non-positive timeout at the parameter boundary."""
    assert GlobParameters(pattern="*", timeout=None).timeout is None
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            GlobParameters(pattern="*", timeout=bad)
