"""Tests for the KnowledgeStoreTool."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from wichy.tools.knowledge_store import KnowledgeStoreTool, OutputMode


@pytest.fixture
def temp_knowledge_dir():
    """Create a temporary directory with sample markdown files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create some markdown files
        notes_dir = Path(tmpdir) / "notes"
        notes_dir.mkdir()

        # File 1: Note about Python
        (notes_dir / "python_note.md").write_text(
            "Python is a programming language.\nPython is great for data science.\n"
        )

        # File 2: Note about JavaScript
        (notes_dir / "js_note.md").write_text(
            "JavaScript runs in the browser.\nJavaScript is versatile.\n"
        )

        # File 3: Another note about Python
        (notes_dir / "python_tips.md").write_text(
            "Python tips: Use virtual environments.\nPython tips: Use type hints.\n"
        )

        yield str(notes_dir)


def test_knowledge_store_default_path(temp_knowledge_dir, monkeypatch):
    """Test that default knowledge_dir is 'notes' relative to cwd."""
    tool = KnowledgeStoreTool()
    assert tool.knowledge_dir.name == "notes"


def test_knowledge_store_custom_path(temp_knowledge_dir):
    """Test that custom knowledge_dir is used."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    assert tool.knowledge_dir.resolve() == Path(temp_knowledge_dir).resolve()


def test_knowledge_store_missing_pattern():
    """Test that empty pattern returns an error."""
    tool = KnowledgeStoreTool(knowledge_dir="/nonexistent")
    result = tool.execute(pattern="")
    assert "error: pattern is required" in result


def test_knowledge_store_nonexistent_dir():
    """Test that nonexistent directory returns an error."""
    tool = KnowledgeStoreTool(knowledge_dir="/this/path/does/not/exist")
    result = tool.execute(pattern="test")
    assert "error: knowledge store directory" in result
    assert "does not exist" in result


def test_knowledge_store_files_with_matches(temp_knowledge_dir):
    """Test output_mode=FILES_WITH_MATCHES."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode=OutputMode.FILES_WITH_MATCHES)

    files = result.strip().split("\n")
    # Should find 2 files containing "Python" (not js_note.md)
    assert len(files) >= 1, f"Expected at least 1 file, got: {files}"
    # At least one Python-containing file should be found
    python_files = [f for f in files if "python_note.md" in f or "python_tips.md" in f]
    assert (
        len(python_files) >= 1
    ), f"Expected to find python_note.md or python_tips.md in: {files}"


def test_knowledge_store_content_mode(temp_knowledge_dir):
    """Test output_mode=CONTENT."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode=OutputMode.CONTENT)

    # Should include matching lines with file paths
    assert "python_note.md" in result or "python_tips.md" in result
    assert "Python is" in result or "Python tips" in result


def test_knowledge_store_count_mode(temp_knowledge_dir):
    """Test output_mode=COUNT."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode=OutputMode.COUNT)

    lines = result.strip().split("\n")
    # Each file with matches should show count
    found_count = False
    for line in lines:
        if "python_note.md" in line or "python_tips.md" in line:
            assert ":" in line  # format: file:count
            parts = line.split(":")
            # Should have at least file path and count
            assert len(parts) >= 2
            found_count = True
    assert found_count, f"Expected to find count entries for Python matches in: {lines}"


def test_knowledge_store_no_matches(temp_knowledge_dir):
    """Test when no matches are found."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="nonexistent_pattern_xyz")
    assert "no matches found" in result.lower()


def test_knowledge_store_glob_pattern(temp_knowledge_dir):
    """Test using a different glob pattern."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    # Search only in files ending with "tips.md"
    result = tool.execute(pattern="Python", glob="*tips.md")

    # Should only search in python_tips.md
    assert "python_tips.md" in result or "Python tips" in result
    # Should not include python_note.md
    if result and result.strip():
        assert (
            "python_note.md" not in result
        ), f"Should not include python_note.md when using glob=*tips.md, got: {result}"


def test_knowledge_store_string_output_mode(temp_knowledge_dir):
    """Test passing output_mode as string instead of enum."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode="content")
    assert "Python is" in result or "Python tips" in result


# New tests to cover fallback paths and edge cases


@pytest.mark.parametrize(
    "output_mode", [OutputMode.FILES_WITH_MATCHES, OutputMode.CONTENT, OutputMode.COUNT]
)
def test_fallback_with_glob(temp_knowledge_dir, monkeypatch, output_mode):
    """Test fallback_with_glob (non-* glob) for all output modes."""
    original_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: False if name == "rg" else original_which(name)
    )
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode=output_mode, glob="*.md")

    if output_mode == OutputMode.FILES_WITH_MATCHES:
        files = result.strip().split("\n")
        assert len(files) >= 1
        python_files = [
            f for f in files if "python_note.md" in f or "python_tips.md" in f
        ]
        assert len(python_files) >= 1
    elif output_mode == OutputMode.CONTENT:
        assert "Python is" in result or "Python tips" in result
        assert "python_note.md" in result or "python_tips.md" in result
    else:  # COUNT
        lines = result.strip().split("\n")
        found_count = False
        for line in lines:
            if "python_note.md" in line or "python_tips.md" in line:
                assert ":" in line
                parts = line.split(":")
                assert len(parts) >= 2
                found_count = True
        assert found_count


@pytest.mark.parametrize(
    "output_mode", [OutputMode.FILES_WITH_MATCHES, OutputMode.CONTENT, OutputMode.COUNT]
)
def test_fallback_simple(temp_knowledge_dir, monkeypatch, output_mode):
    """Test fallback_simple (no glob, glob='*') for all output modes."""
    original_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: False if name == "rg" else original_which(name)
    )
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode=output_mode, glob="*")

    if output_mode == OutputMode.FILES_WITH_MATCHES:
        files = result.strip().split("\n")
        assert len(files) >= 1
        python_files = [
            f for f in files if "python_note.md" in f or "python_tips.md" in f
        ]
        assert len(python_files) >= 1
    elif output_mode == OutputMode.CONTENT:
        assert "Python is" in result or "Python tips" in result
        assert "python_note.md" in result or "python_tips.md" in result
    else:  # COUNT
        lines = result.strip().split("\n")
        found_count = False
        for line in lines:
            if "python_note.md" in line or "python_tips.md" in line:
                assert ":" in line
                parts = line.split(":")
                assert len(parts) >= 2
                found_count = True
        assert found_count


def test_fallback_with_glob_no_files(temp_knowledge_dir, monkeypatch):
    """Test fallback_with_glob when no files match the glob."""
    original_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: False if name == "rg" else original_which(name)
    )
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(
        pattern="Python",
        output_mode=OutputMode.FILES_WITH_MATCHES,
        glob="*.nonexistent",
    )
    assert "no files found matching the pattern" in result.lower()


def test_knowledge_store_invalid_output_mode_string(temp_knowledge_dir):
    """Test passing invalid output_mode as string returns an error."""
    tool = KnowledgeStoreTool(knowledge_dir=temp_knowledge_dir)
    result = tool.execute(pattern="Python", output_mode="invalid_mode")
    assert "error: invalid output_mode" in result
