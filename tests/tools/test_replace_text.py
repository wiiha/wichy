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

    result = replace_text_tool.execute(
        file_path=test_file,
        old_content="nonexistent text\n",
        new_content="something\n",
        count=1,
    )
    assert result.startswith("error:")
    assert "old_content not found" in result


def test_count_out_of_range(replace_text_tool, temp_workspace):
    """Test behavior when count exceeds number of occurrences."""
    test_file = os.path.join(temp_workspace, "test.txt")

    result = replace_text_tool.execute(
        file_path=test_file,
        old_content="line 2\n",
        new_content="x\n",
        count=5,
    )
    assert result.startswith("error:")
    assert "count out of range" in result


def test_file_not_found(replace_text_tool):
    """Test behavior when file doesn't exist."""
    result = replace_text_tool.execute(
        file_path="/nonexistent/file.txt",
        old_content="something",
        new_content="else",
    )
    assert result.startswith("error:")
    assert "not found" in result


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


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Hard address-space cap for the child process. The pre-fix failure is
#: unbounded ALLOCATION (`find("", start)` returns `start`, `start += 0`), not a
#: CPU spin, so an in-process guard would be OOM-killed and take the pytest
#: runner down with it -- it cannot fail cleanly.
_EMPTY_MATCH_MEMORY_CAP = 512 * 1024 * 1024


def _run_replace_in_capped_child(tmp_path, old_content, new_content, count=1):
    """Run one replace_text call in a memory-capped child; return (status, out).

    Status is "OK", "SIGNALLED" or "MEMORY" -- the latter two mean the
    unbounded loop is back.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        import resource, sys
        resource.setrlimit(
            resource.RLIMIT_AS,
            ({_EMPTY_MATCH_MEMORY_CAP}, {_EMPTY_MATCH_MEMORY_CAP}),
        )
        sys.path.insert(0, {REPO_ROOT!r})
        from wichy.tools.replace_text import ReplaceTextTool
        try:
            r = ReplaceTextTool().execute(
                file_path={str(tmp_path)!r},
                old_content={old_content!r},
                new_content={new_content!r},
                count={count},
            )
        except MemoryError:
            print("STATUS:MEMORY")
        else:
            print("STATUS:OK")
            print("RESULT:" + r.replace(chr(10), " "))
        """)
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )
    if proc.stdout.startswith("STATUS:"):
        status, _, result = proc.stdout.partition("\n")
        return status.split(":", 1)[1], result
    return f"SIGNALLED(rc={proc.returncode})", proc.stderr[-200:]


def test_replace_empty_old_content_is_rejected(temp_workspace):
    """An empty search string must be rejected, not looped."""
    test_file = os.path.join(temp_workspace, "test.txt")
    before = open(test_file).read()

    status, result = _run_replace_in_capped_child(test_file, "", "X")

    assert status == "OK", f"empty old_content looped again: {status} {result}"
    assert "error:" in result, result
    assert "must not be empty" in result, result
    assert open(test_file).read() == before


def test_replace_empty_old_content_fails_loudly_not_silently(temp_workspace):
    """The rejection must name the tool argument and suggest the fix.

    A tool error is guidance, not just a diagnosis.
    """
    test_file = os.path.join(temp_workspace, "test.txt")

    status, result = _run_replace_in_capped_child(test_file, "", "X")

    assert status == "OK"
    assert test_file in result, result
    assert "Provide the exact text to replace" in result, result


def test_replace_normal_use_terminates_in_capped_child(temp_workspace):
    """Control: a normal edit must succeed in the same capped child.

    Proves the cap and harness do not themselves cause the pass.
    """
    test_file = os.path.join(temp_workspace, "test.txt")

    status, result = _run_replace_in_capped_child(test_file, "line 1\n", "LINE 1\n")

    assert status == "OK", f"{status} {result}"
    assert "Replaced 1 occurrence(s)" in result, result
    with open(test_file) as f:
        assert f.read().startswith("LINE 1\n")
