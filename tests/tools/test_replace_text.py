"""
Test cases for the ReplaceTextTool.
"""

import os
import tempfile
import pytest

from wichy.tools.replace_text import ReplaceTextTool


@pytest.fixture
def replace_text_tool():
    """Fixture to create a fresh ReplaceTextTool instance for each test."""
    return ReplaceTextTool()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test file with some content containing multiple occurrences of "line 2\n"
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("line 1\nline 2\nline 3\nline 2\n")
        yield tmpdir


def test_replace_single_occurrence(replace_text_tool, temp_workspace):
    """Test replacing a single occurrence (default behavior)."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = replace_text_tool.execute(
        file_path=test_file,
        old_content="line 2\n",
        new_content="line 2 modified\n",
        count=1,
    )

    assert "Replaced 1 occurrence(s)" in result
    assert test_file in result

    # Verify file was modified correctly
    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nline 2 modified\nline 3\nline 2\n"
    assert content == expected


def test_replace_all_occurrences(replace_text_tool, temp_workspace):
    """Test replacing all occurrences using count=0."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = replace_text_tool.execute(
        file_path=test_file,
        old_content="line 2\n",
        new_content="replaced\n",
        count=0,
    )

    assert "Replaced 2 occurrence(s)" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nreplaced\nline 3\nreplaced\n"
    assert content == expected


def test_replace_specific_occurrence(replace_text_tool, temp_workspace):
    """Test replacing a specific occurrence using count parameter."""
    test_file = os.path.join(temp_workspace, "test.txt")

    # Replace only the second occurrence (count=2)
    result = replace_text_tool.execute(
        file_path=test_file,
        old_content="line 2\n",
        new_content="SECOND\n",
        count=2,
    )

    assert "Replaced 1 occurrence(s)" in result
    assert "(left 1 unchanged)" in result

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nline 2\nline 3\nSECOND\n"
    assert content == expected


def test_old_content_not_found(replace_text_tool, temp_workspace):
    """Test behavior when old_content is not found."""
    test_file = os.path.join(temp_workspace, "test.txt")

    with pytest.raises(ValueError, match="old_content not found"):
        replace_text_tool.execute(
            file_path=test_file,
            old_content="nonexistent text\n",
            new_content="something\n",
            count=1,
        )


def test_count_out_of_range(replace_text_tool, temp_workspace):
    """Test behavior when count exceeds number of occurrences."""
    test_file = os.path.join(temp_workspace, "test.txt")

    with pytest.raises(ValueError, match="count out of range"):
        replace_text_tool.execute(
            file_path=test_file,
            old_content="line 2\n",
            new_content="x\n",
            count=5,
        )


def test_file_not_found(replace_text_tool):
    """Test behavior when file doesn't exist."""
    with pytest.raises(FileNotFoundError):
        replace_text_tool.execute(
            file_path="/nonexistent/file.txt",
            old_content="something",
            new_content="else",
        )


def test_preserve_other_content(replace_text_tool, temp_workspace):
    """Test that non-matching content is preserved exactly."""
    test_file = os.path.join(temp_workspace, "test.txt")

    replace_text_tool.execute(
        file_path=test_file,
        old_content="line 3\n",
        new_content="line THREE\n",
        count=1,
    )

    with open(test_file, "r") as f:
        content = f.read()

    # Check that line 1 and line 2(s) are unchanged
    assert "line 1" in content
    # Both "line 2\n" occurrences remain unchanged
    assert content.count("line 2\n") == 2
    assert "line THREE\n" in content


def test_replace_with_empty_string(replace_text_tool, temp_workspace):
    """Test replacing with empty new_content (deletion)."""
    test_file = os.path.join(temp_workspace, "test.txt")

    replace_text_tool.execute(
        file_path=test_file,
        old_content="line 2\n",
        new_content="",  # Delete
        count=1,
    )

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nline 3\nline 2\n"
    assert content == expected


def test_replace_multiline_content(replace_text_tool, temp_workspace):
    """Test replacing multi-line blocks."""
    test_file = os.path.join(temp_workspace, "test.txt")

    # Replace two consecutive lines
    replace_text_tool.execute(
        file_path=test_file,
        old_content="line 2\nline 3\n",
        new_content="lines 2-3 replaced\n",
        count=1,
    )

    with open(test_file, "r") as f:
        content = f.read()

    expected = "line 1\nlines 2-3 replaced\nline 2\n"
    assert content == expected
