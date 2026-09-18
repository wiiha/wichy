"""
Test cases for the InsertLinesTool.
"""

import os
import tempfile

import pytest

from wichy.tools.insert_lines import InsertLinesTool


@pytest.fixture
def insert_lines_tool():
    """Fixture to create a fresh InsertLinesTool instance for each test."""
    return InsertLinesTool()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test file with some content
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("line 1\nline 2\nline 3\n")
        yield tmpdir


def test_insert_after_line(insert_lines_tool, temp_workspace):
    """Test inserting content after a specific line."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=1,
        content="inserted line\n",
    )

    assert "Inserted content after line 1" in result
    assert test_file in result

    # Verify file was modified correctly
    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\ninserted line\nline 2\nline 3\n"
    assert content == expected


def test_insert_at_beginning(insert_lines_tool, temp_workspace):
    """Test inserting content at the beginning (offset=0)."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=0,
        content="first line\n",
    )

    assert "Inserted content after line 0" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "first line\nline 1\nline 2\nline 3\n"
    assert content == expected


def test_insert_at_end(insert_lines_tool, temp_workspace):
    """Inserting at offset == line count appends.

    This is the legitimate append point and must keep working; the old version
    of this test used offset=10 on a 3-line file, which asserted the adjacent
    behaviour of silently appending while reporting line 10.
    """
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=3,  # == len(lines) for the 3-line fixture
        content="last line\n",
    )

    assert "Inserted content after line 3" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nline 2\nline 3\nlast line\n"
    assert content == expected


def test_insert_beyond_end_is_rejected(insert_lines_tool, temp_workspace):
    """offset past EOF must be rejected, not silently appended.

    Previously this returned "Inserted content after line 10" for a 3-line
    file.
    """
    test_file = os.path.join(temp_workspace, "test.txt")
    with open(test_file) as f:
        before = f.read()

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=10,  # Beyond file length
        content="last line\n",
    )

    assert result.startswith("error:")
    assert "beyond the end of the file" in result

    with open(test_file) as f:
        assert f.read() == before  # file untouched


def test_insert_beyond_end_error_is_guiding(insert_lines_tool, temp_workspace):
    """The rejection must say how to append, and name the real line count."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=99,
        content="x\n",
    )

    assert "offset=3 to append" in result
    assert "3 line(s)" in result


def test_insert_multiple_lines(insert_lines_tool, temp_workspace):
    """Test inserting multiple lines of content."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=2,
        content="middle line 1\nmiddle line 2\n",
    )

    assert "Inserted content after line 2" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nline 2\nmiddle line 1\nmiddle line 2\nline 3\n"
    assert content == expected


def test_insert_empty_content(insert_lines_tool, temp_workspace):
    """Test inserting empty content (should still insert a blank line)."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=1,
        content="",
    )

    assert "Inserted content after line 1" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nline 2\nline 3\n"
    assert content == expected


def test_insert_with_special_characters(insert_lines_tool, temp_workspace):
    """Test inserting content with special characters."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=1,
        content="special: @#$%^&*()_+-=[]{}|;:'\",./<>?\n",
    )

    assert "Inserted content after line 1" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nspecial: @#$%^&*()_+-=[]{}|;:'\",./<>?\nline 2\nline 3\n"
    assert content == expected


def test_file_not_found(insert_lines_tool):
    """Test behavior when file doesn't exist."""
    result = insert_lines_tool.execute(
        file_path="/nonexistent/file.txt",
        offset=1,
        content="something",
    )
    assert result.startswith("error:")
    assert "not found" in result


def test_preserve_other_content(insert_lines_tool, temp_workspace):
    """Test that non-inserted content is preserved exactly."""
    test_file = os.path.join(temp_workspace, "test.txt")

    insert_lines_tool.execute(
        file_path=test_file,
        offset=1,
        content="inserted\n",
    )

    with open(test_file, "r") as f:
        content = f.read()

    # Check that all original lines are still present
    assert "line 1\n" in content
    assert "line 2\n" in content
    assert "line 3\n" in content
    assert "inserted\n" in content


def test_insert_in_empty_file(insert_lines_tool, temp_workspace):
    """Test inserting into an empty file."""
    test_file = os.path.join(temp_workspace, "empty.txt")
    with open(test_file, "w") as f:
        f.write("")  # Create empty file

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=0,
        content="first line\n",
    )

    assert "Inserted content after line 0" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "first line\n"
    assert content == expected


def test_insert_with_different_encoding(insert_lines_tool, temp_workspace):
    """Test inserting content with different encoding."""
    test_file = os.path.join(temp_workspace, "test.txt")

    # Create file with utf-16 encoding
    with open(test_file, "w", encoding="utf-16") as f:
        f.write("line 1\nline 2\nline 3\n")

    result = insert_lines_tool.execute(
        file_path=test_file,
        offset=1,
        content="inserted\n",
        encoding="utf-16",
    )

    assert "Inserted content after line 1" in result

    with open(test_file, "r", encoding="utf-16") as f:
        content = f.read()

    expected = "line 1\ninserted\nline 2\nline 3\n"
    assert content == expected
