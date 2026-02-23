"""
Test cases for the PatchTool.
"""

import os
import tempfile

import pytest

from wichy.tools.patch import PatchTool


@pytest.fixture
def patch_tool():
    """Fixture to create a fresh PatchTool instance for each test."""
    return PatchTool()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test file
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("line 1\nline 2\nline 3\n")

        yield tmpdir


def test_patch_apply_simple_change(patch_tool, temp_workspace):
    """Test applying a simple unified diff that changes one line."""
    test_file = os.path.join(temp_workspace, "test.txt")
    patch_content = """--- a/test.txt
+++ b/test.txt
@@ -1,3 +1,3 @@
 line 1
-line 2
+line 2 modified
 line 3
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )

        assert "Applied:" in result
        assert "test.txt" in result or "b/test.txt" in result

        # Verify file was modified
        with open(test_file, "r") as f:
            content = f.read()
        assert "line 2 modified" in content
        assert "line 2\n" not in content  # original removed line should not be present
    finally:
        os.chdir(original_cwd)


def test_patch_add_new_file(patch_tool, temp_workspace):
    """Test adding a new file via diff (creating from /dev/null)."""
    new_file = os.path.join(temp_workspace, "new.txt")
    patch_content = """--- /dev/null
+++ b/new.txt
@@ -0,0 +1,3 @@
+new line 1
+new line 2
+new line 3
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )

        assert "Applied:" in result
        assert os.path.exists(new_file)
        with open(new_file, "r") as f:
            content = f.read()
        assert "new line 1" in content
        assert "new line 2" in content
        assert "new line 3" in content
    finally:
        os.chdir(original_cwd)


def test_patch_delete_file(patch_tool, temp_workspace):
    """Test deleting a file via diff (deleting to /dev/null)."""
    test_file = os.path.join(temp_workspace, "test.txt")
    patch_content = """--- a/test.txt
+++ /dev/null
@@ -1,3 +0,0 @@
-line 1
-line 2
-line 3
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )

        assert "Applied:" in result
        assert not os.path.exists(test_file)
    finally:
        os.chdir(original_cwd)


def test_patch_dry_run(patch_tool, temp_workspace):
    """Test dry run mode does not actually modify files."""
    test_file = os.path.join(temp_workspace, "test.txt")
    original_content = open(test_file).read()

    patch_content = """--- a/test.txt
+++ b/test.txt
@@ -1,3 +1,4 @@
 line 1
 line 2
+new line inserted
 line 3
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=True,
            restrict_to_cwd=False,
        )

        assert "DRY RUN" in result
        assert "Applied:" in result

        # File should remain unchanged
        with open(test_file, "r") as f:
            content = f.read()
        assert content == original_content
    finally:
        os.chdir(original_cwd)


def test_patch_strip_parameter(patch_tool, temp_workspace):
    """Test the strip parameter for paths with leading directories."""
    test_file = os.path.join(temp_workspace, "test.txt")
    with open(test_file, "w") as f:
        f.write("original\n")

    patch_content = """--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,1 @@
-original
+modified
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        # With strip=1, remove the leading 'a/' to find test.txt
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
            strip=1,
        )
        assert "Applied:" in result

        # Verify it was applied
        with open(test_file, "r") as f:
            content = f.read()
        assert "modified" in content
    finally:
        os.chdir(original_cwd)


def test_patch_multiple_files(patch_tool, temp_workspace):
    """Test applying a patch that affects multiple files."""
    file1 = os.path.join(temp_workspace, "file1.txt")
    file2 = os.path.join(temp_workspace, "file2.txt")
    with open(file1, "w") as f:
        f.write("original1\n")
    with open(file2, "w") as f:
        f.write("original2\n")

    patch_content = """--- a/file1.txt
+++ b/file1.txt
@@ -1,1 +1,1 @@
-original1
+modified1

--- a/file2.txt
+++ b/file2.txt
@@ -1,1 +1,1 @@
-original2
+modified2
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )

        assert "Applied: 2 file(s)" in result
        assert "file1.txt" in result or "b/file1.txt" in result
        assert "file2.txt" in result or "b/file2.txt" in result

        # Verify both files modified
        with open(file1, "r") as f:
            assert "modified1" in f.read()
        with open(file2, "r") as f:
            assert "modified2" in f.read()
    finally:
        os.chdir(original_cwd)


def test_patch_invalid_patch_format(patch_tool, temp_workspace):
    """Test handling of invalid patch content."""
    patch_content = "This is not a valid unified diff"

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )
        assert "error" in result
        assert "parse patch" in result.lower()
    finally:
        os.chdir(original_cwd)


def test_patch_multi_hunk(patch_tool, temp_workspace):
    """Test patch with multiple hunks in a single file."""
    test_file = os.path.join(temp_workspace, "test.txt")
    with open(test_file, "w") as f:
        f.write("line1\nline2\nline3\nline4\n")

    patch_content = """--- a/test.txt
+++ b/test.txt
@@ -1,2 +1,2 @@
 line1
-line2
+line2 changed
@@ -3,2 +3,2 @@
 line3
-line4
+line4 changed
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )

        assert "Applied:" in result

        with open(test_file, "r") as f:
            content = f.read()
        assert "line2 changed" in content
        assert "line4 changed" in content
        assert "line2\n" not in content
        assert "line4\n" not in content
    finally:
        os.chdir(original_cwd)


def test_patch_file_not_found_for_update(patch_tool, temp_workspace):
    """Test updating a non-existent file (when patch requires it)."""
    # This patch attempts to modify a file that doesn't exist
    patch_content = """--- a/nonexistent.txt
+++ b/nonexistent.txt
@@ -1,1 +1,1 @@
-old
+new
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )
        # The patch library will fail because the source file doesn't exist
        assert "error" in result or "Failed:" in result
    finally:
        os.chdir(original_cwd)


def test_patch_no_changes(patch_tool, temp_workspace):
    """Test applying a patch that results in no changes (content already matches)."""
    test_file = os.path.join(temp_workspace, "test.txt")
    # File already contains exactly this content
    original_content = "line 1\nline 2\nline 3\n"
    with open(test_file, "w") as f:
        f.write(original_content)

    patch_content = """--- a/test.txt
+++ b/test.txt
@@ -1,3 +1,3 @@
 line 1
 line 2
 line 3
"""

    original_cwd = os.getcwd()
    try:
        os.chdir(temp_workspace)
        result = patch_tool.execute(
            patch_content=patch_content,
            dry_run=False,
            restrict_to_cwd=False,
        )

        # The patch is a no-op but still applies successfully (0 changes)
        # Accept either "Applied:" or "No changes made" or "success"
        assert "Applied:" in result or "success" in result.lower()

        # File should be unchanged
        with open(test_file, "r") as f:
            content = f.read()
        assert content == original_content
    finally:
        os.chdir(original_cwd)
