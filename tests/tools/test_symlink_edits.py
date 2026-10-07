"""File-editing tools must edit THROUGH a symlink, not replace it.

``write_file``, ``replace_text`` and ``insert_lines`` all write via
``file_safety.atomic_write``, which renames a temp file into place. Renaming
replaces a directory entry, so before the fix an edit of a symlinked path
swapped the link for a regular file and the real target kept its old content:
a silent data fork reported as success. These tests pin the fixed contract at
the tool surface, so a regression in the shared writer is caught here too.
"""

from __future__ import annotations

import os

from wichy.tools.insert_lines import InsertLinesTool
from wichy.tools.replace_text import ReplaceTextTool
from wichy.tools.write_file import WriteFileTool


def _symlink(target, link) -> None:
    link.symlink_to(target)


def test_write_file_through_symlink_updates_target(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("original\n")
    link = tmp_path / "link.txt"
    _symlink(real, link)

    WriteFileTool().execute(path=str(link), content="replaced\n")

    assert link.is_symlink()
    assert real.read_text() == "replaced\n"
    assert link.read_text() == "replaced\n"


def test_replace_text_through_symlink_updates_target(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("old line\n")
    link = tmp_path / "link.txt"
    _symlink(real, link)

    ReplaceTextTool().execute(
        file_path=str(link), old_content="old line", new_content="new line", count=1
    )

    assert link.is_symlink()
    assert real.read_text() == "new line\n"


def test_insert_lines_through_symlink_updates_target(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("body\n")
    link = tmp_path / "link.txt"
    _symlink(real, link)

    InsertLinesTool().execute(file_path=str(link), offset=0, content="HEADER\n")

    assert link.is_symlink()
    assert real.read_text() == "HEADER\nbody\n"


def test_write_file_through_symlink_creates_dangling_target(tmp_path):
    """A dangling link is followed, not shadowed by a new regular file."""
    real = tmp_path / "real.txt"
    link = tmp_path / "link.txt"
    _symlink(real, link)

    WriteFileTool().execute(path=str(link), content="fresh\n")

    assert link.is_symlink()
    assert real.read_text() == "fresh\n"


def test_symlink_edit_leaves_no_temp_files(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("original\n")
    link = tmp_path / "link.txt"
    _symlink(real, link)

    WriteFileTool().execute(path=str(link), content="new\n")

    leftovers = [n for n in os.listdir(tmp_path) if n.startswith(".wichy-tmp-")]
    assert leftovers == []
