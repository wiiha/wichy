"""
Test cases for the ReadFileTool.
"""

import json
import os
import tempfile

import pytest

from wichy.tools.read_file import ReadFileTool, SUPPORTED_IMAGE_TYPES


@pytest.fixture
def read_file_tool():
    """Fixture to create a fresh ReadFileTool instance for each test."""
    return ReadFileTool()


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create test text file
        test_file = os.path.join(tmpdir, "test.txt")
        with open(test_file, "w") as f:
            f.write("line 1\nline 2\nline 3\n")

        # Create a minimal valid PNG (1x1 pixel red PNG)
        png_file = os.path.join(tmpdir, "test.png")
        # Minimal PNG: 8-byte signature + IHDR + IDAT + IEND chunks
        import base64

        # 1x1 red pixel PNG
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhK4miQAAAABJRU5ErkJggg=="
        )
        with open(png_file, "wb") as f:
            f.write(png_data)

        yield tmpdir


class TestReadFileTextContent:
    """Tests for reading text files."""

    def test_read_file_basic(self, read_file_tool, temp_workspace):
        """Test basic file reading."""
        test_file = os.path.join(temp_workspace, "test.txt")
        result = read_file_tool.execute(path=test_file)

        assert "line 1" in result
        assert "line 2" in result
        assert "line 3" in result

    def test_read_file_with_offset(self, read_file_tool, temp_workspace):
        """Test reading from a specific line offset."""
        test_file = os.path.join(temp_workspace, "test.txt")
        result = read_file_tool.execute(path=test_file, offset=2)

        assert "line 1" not in result
        assert "line 2" in result
        assert "line 3" in result

    def test_read_file_with_limit(self, read_file_tool, temp_workspace):
        """Test reading with line limit."""
        test_file = os.path.join(temp_workspace, "test.txt")
        result = read_file_tool.execute(path=test_file, limit=1)

        assert "line 1" in result
        assert "line 2" not in result

    def test_read_file_not_found(self, read_file_tool):
        """Test reading a non-existent file."""
        result = read_file_tool.execute(path="/nonexistent/file.txt")
        assert "error: file not found" in result

    def test_read_empty_file(self, read_file_tool, temp_workspace):
        """Test reading an empty file."""
        empty_file = os.path.join(temp_workspace, "empty.txt")
        with open(empty_file, "w"):
            pass  # Create empty file

        result = read_file_tool.execute(path=empty_file)
        assert "empty" in result.lower()


class TestReadFileMultimodal:
    """Tests for reading image files with multimodal support."""

    def test_read_image_with_media_type_auto(self, read_file_tool, temp_workspace):
        """Test reading an image file with media_type='auto'."""
        png_file = os.path.join(temp_workspace, "test.png")
        result = read_file_tool.execute(path=png_file, media_type="auto")

        # Result should be JSON
        data = json.loads(result)

        assert "multimodal_content" in data
        assert isinstance(data["multimodal_content"], list)
        assert len(data["multimodal_content"]) == 1
        assert data["multimodal_content"][0]["type"] == "image_url"
        assert "image_url" in data["multimodal_content"][0]
        assert data["media_type"] == "image/png"
        assert data["file_path"] == png_file

    def test_read_image_with_explicit_mime_type(self, read_file_tool, temp_workspace):
        """Test reading an image file with explicit media type."""
        png_file = os.path.join(temp_workspace, "test.png")
        result = read_file_tool.execute(path=png_file, media_type="image/png")

        data = json.loads(result)

        assert "multimodal_content" in data
        assert data["media_type"] == "image/png"

    def test_read_image_without_media_type_shows_hint(
        self, read_file_tool, temp_workspace
    ):
        """Test that reading an image without media_type shows a helpful hint."""
        png_file = os.path.join(temp_workspace, "test.png")
        result = read_file_tool.execute(path=png_file)

        data = json.loads(result)

        # Should return info about detecting image type
        assert "info" in data
        assert "image" in data["info"].lower()
        assert "detected_type" in data
        assert data["detected_type"] == "image/png"

    def test_read_unsupported_media_type(self, read_file_tool, temp_workspace):
        """Test reading with an unsupported media type."""
        png_file = os.path.join(temp_workspace, "test.png")
        result = read_file_tool.execute(path=png_file, media_type="video/mp4")

        data = json.loads(result)

        assert "error" in data
        assert "Unsupported media type" in data["error"]

    def test_multimodal_content_has_base64_data(self, read_file_tool, temp_workspace):
        """Test that multimodal content includes valid base64 image data."""
        png_file = os.path.join(temp_workspace, "test.png")
        result = read_file_tool.execute(path=png_file, media_type="auto")

        data = json.loads(result)

        # Extract the data URL
        image_url = data["multimodal_content"][0]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")

        # Verify the base64 part is valid
        import base64

        base64_part = image_url.split(",", 1)[1]
        # Should not raise an exception
        decoded = base64.b64decode(base64_part)
        assert len(decoded) > 0


class TestSupportedImageTypes:
    """Tests for supported image type constants."""

    def test_supported_types_include_common_formats(self):
        """Verify common image formats are supported."""
        assert "image/jpeg" in SUPPORTED_IMAGE_TYPES
        assert "image/png" in SUPPORTED_IMAGE_TYPES
        assert "image/gif" in SUPPORTED_IMAGE_TYPES
        assert "image/webp" in SUPPORTED_IMAGE_TYPES
