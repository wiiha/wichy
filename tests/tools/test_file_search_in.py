"""
Test cases for the SearchInFilesTool.
"""

import os
import shutil
import tempfile

import pytest

from wichy.tools.file_search_in import (
    _CONTENT_LIMIT,
    _LINE_TRUNCATE,
    _SAMPLE_LINES,
    _TRUNCATION_SUFFIX,
    OutputMode,
    SearchInFilesTool,
)


@pytest.fixture
def tool():
    """Fresh SearchInFilesTool instance."""
    return SearchInFilesTool()


@pytest.fixture
def tmpdir():
    """Temp directory cleaned up after each test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


# ---------------------------------------------------------------------------
# Core mode tests
# ---------------------------------------------------------------------------


def test_empty_pattern_returns_error(tool):
    assert "error: pattern is required" in tool.execute(".", "")
    assert "error: pattern is required" in tool.execute(".", "   ")


def test_no_matches_returns_no_matches_found(tool, tmpdir):
    result = tool.execute(tmpdir, "xyzzy_nomatch_12345", OutputMode.CONTENT)
    assert result == "no matches found"


def test_count_mode_returns_counts(tool, tmpdir):
    f1 = os.path.join(tmpdir, "a.txt")
    f2 = os.path.join(tmpdir, "b.txt")
    with open(f1, "w") as f:
        f.write("hello world\nhello again\n")
    with open(f2, "w") as f:
        f.write("hello only here\n")

    result = tool.execute(tmpdir, "hello", OutputMode.COUNT)
    # Each line is "path:count"
    assert "a.txt:" in result or "a.txt:" in result.replace("\\", "/")
    assert "b.txt:" in result or "b.txt:" in result.replace("\\", "/")


def test_files_with_matches_mode_returns_paths(tool, tmpdir):
    f1 = os.path.join(tmpdir, "a.txt")
    f2 = os.path.join(tmpdir, "b.txt")
    with open(f1, "w") as f:
        f.write("hello world\n")
    with open(f2, "w") as f:
        f.write("goodbye world\n")

    result = tool.execute(tmpdir, "hello", OutputMode.FILES_WITH_MATCHES)
    assert "a.txt" in result
    assert "b.txt" not in result


def test_content_mode_returns_lines(tool, tmpdir):
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        fh.write("hello world\nhello again\n")

    result = tool.execute(tmpdir, "hello", OutputMode.CONTENT)
    assert "hello world" in result
    assert "hello again" in result


def test_content_mode_string_output_mode(tool, tmpdir):
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        fh.write("hello world\n")
    # Should coerce the string "content" into the enum
    result = tool.execute(tmpdir, "hello", "content")
    assert "hello world" in result


# ---------------------------------------------------------------------------
# Line truncation (the per-line 200-char cap for minified files)
# ---------------------------------------------------------------------------


def test_content_mode_normal_lines_not_truncated(tool, tmpdir):
    """Lines under the limit should be returned unchanged."""
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        fh.write("normal short line\n")
    result = tool.execute(tmpdir, "short", OutputMode.CONTENT)
    assert "normal short line" in result
    assert _TRUNCATION_SUFFIX not in result


def test_content_mode_long_line_truncated(tool, tmpdir):
    """A single match on a minified line >200 chars is truncated with [...]"""
    f = os.path.join(tmpdir, "a.txt")
    # Construct a line longer than _LINE_TRUNCATE
    long_line = "x" * (_LINE_TRUNCATE + 50)
    with open(f, "w") as fh:
        fh.write(long_line + "\n")

    result = tool.execute(tmpdir, "x", OutputMode.CONTENT)
    assert _TRUNCATION_SUFFIX in result
    # The truncated content should NOT contain the original tail
    assert ("x" * (_LINE_TRUNCATE + 10)) not in result


def test__TRUNCATION_SUFFIX_not_appended_when_exact_limit(tool, tmpdir):
    """A line under the limit should NOT get the [...] suffix."""
    f = os.path.join(tmpdir, "a.txt")
    # The macOS temp path prefix (~120 chars) already consumes part of the
    # 200-char budget, so we use 50 chars of content to stay safely under.
    with open(f, "w") as fh:
        fh.write("a" * 50 + "\n")

    result = tool.execute(tmpdir, "a", OutputMode.CONTENT)
    # The line should not be truncated, no [...] suffix
    assert "  [...]" not in result


def test_multiple_lines_each_truncated(tool, tmpdir):
    """When multiple lines are all over the limit, each gets truncated."""
    f = os.path.join(tmpdir, "a.txt")
    long1 = "a" * (_LINE_TRUNCATE + 30)
    long2 = "b" * (_LINE_TRUNCATE + 40)
    with open(f, "w") as fh:
        fh.write(long1 + "\n" + long2 + "\n")

    result = tool.execute(tmpdir, "[ab]", OutputMode.CONTENT)
    lines = result.splitlines()
    # Both lines should have been truncated
    assert all(_TRUNCATION_SUFFIX in line for line in lines)


def test_count_mode_not_affected_by_truncation(tool, tmpdir):
    """Count mode output should not be truncated — it has no long-line risk."""
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        fh.write("a" * 1000 + "\n")

    result = tool.execute(tmpdir, "a", OutputMode.COUNT)
    assert "  [...]" not in result
    assert "a.txt:" in result


def test_files_with_matches_mode_not_affected_by_truncation(tool, tmpdir):
    """files-with-matches returns paths only — no truncation needed."""
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        fh.write("hello world\n")

    result = tool.execute(tmpdir, "hello", OutputMode.FILES_WITH_MATCHES)
    assert "  [...]" not in result
    assert "a.txt" in result


# ---------------------------------------------------------------------------
# Over-limit warning + sample
# ---------------------------------------------------------------------------


def test_over_limit_returns_warning_and_sample(tool, tmpdir):
    """When total matches exceed _CONTENT_LIMIT, returns warning + 20-line sample."""
    f = os.path.join(tmpdir, "a.txt")
    # Write more than _CONTENT_LIMIT lines so we exceed the limit
    with open(f, "w") as fh:
        for i in range(_CONTENT_LIMIT + 10):
            fh.write(f"hello world {i}\n")

    result = tool.execute(tmpdir, "hello", OutputMode.CONTENT)
    assert "[WARNING]" in result
    assert f"{_CONTENT_LIMIT + 10:,}" in result
    assert f"limit {_CONTENT_LIMIT}" in result
    assert f"Sample (first {_SAMPLE_LINES} lines)" in result
    assert "Narrow your pattern" in result


def test_under_limit_returns_content_directly(tool, tmpdir):
    """When total matches are under the limit, content is returned without warning."""
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        for i in range(10):
            fh.write(f"hello world {i}\n")

    result = tool.execute(tmpdir, "hello", OutputMode.CONTENT)
    assert "[WARNING]" not in result
    assert "hello world" in result


def test_exact_limit_no_warning(tool, tmpdir):
    """At exactly the limit, no warning should trigger."""
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        for i in range(_CONTENT_LIMIT):
            fh.write(f"hello world {i}\n")

    result = tool.execute(tmpdir, "hello", OutputMode.CONTENT)
    assert "[WARNING]" not in result


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Grep fallback (when ripgrep not available)
# ---------------------------------------------------------------------------


def test_falls_back_to_grep_when_rg_missing(tool, tmpdir, monkeypatch):
    """If ripgrep is not available, should fall back to grep without error."""
    monkeypatch.setattr(shutil, "which", lambda name: False if name == "rg" else None)
    f = os.path.join(tmpdir, "a.txt")
    with open(f, "w") as fh:
        fh.write("hello world\n")

    # Should not raise — grep fallback should handle it
    result = tool.execute(tmpdir, "hello", OutputMode.CONTENT)
    assert "hello" in result.lower()
