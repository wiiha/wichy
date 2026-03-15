"""
Test cases for multimodal content handling in root_agent.
"""

import json

from wichy.root_agent.root_agent import extract_multimodal_content
from wichy.llm_backend import (
    LLMBackendMultimodalNotSupported,
    error_indicates_multimodal_not_supported,
)


class TestExtractMultimodalContent:
    """Tests for the extract_multimodal_content function."""

    def test_non_json_result_returns_unchanged(self):
        """Test that non-JSON results are returned unchanged."""
        result = "This is plain text output"
        display, multimodal = extract_multimodal_content(result)

        assert display == result
        assert multimodal is None

    def test_json_without_multimodal_content_returns_unchanged(self):
        """Test that JSON without multimodal_content is returned unchanged."""
        result = json.dumps(
            {"status": "success", "data": {"file": "test.txt", "lines": 10}}
        )
        display, multimodal = extract_multimodal_content(result)

        assert display == result
        assert multimodal is None

    def test_json_with_multimodal_content_extracts_correctly(self):
        """Test that multimodal_content is extracted from valid JSON."""
        result = json.dumps(
            {
                "multimodal_content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc123"},
                    }
                ],
                "media_type": "image/png",
                "file_path": "/path/to/image.png",
                "file_size_bytes": 12345,
            }
        )
        display, multimodal = extract_multimodal_content(result)

        assert "Image loaded" in display
        assert "/path/to/image.png" in display
        assert "image/png" in display
        assert len(multimodal) == 1
        assert multimodal[0]["type"] == "image_url"
        assert multimodal[0]["image_url"]["url"] == "data:image/png;base64,abc123"

    def test_multimodal_content_with_multiple_parts(self):
        """Test extraction with multiple content parts."""
        result = json.dumps(
            {
                "multimodal_content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,img1"},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,img2"},
                    },
                ],
                "media_type": "image/png",
                "file_path": "/path/to/image.png",
                "file_size_bytes": 12345,
            }
        )
        display, multimodal = extract_multimodal_content(result)

        assert len(multimodal) == 2
        assert multimodal[0]["image_url"]["url"].endswith("img1")
        assert multimodal[1]["image_url"]["url"].endswith("img2")

    def test_multimodal_content_missing_optional_fields(self):
        """Test extraction when optional fields are missing."""
        result = json.dumps(
            {
                "multimodal_content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    }
                ]
            }
        )
        display, multimodal = extract_multimodal_content(result)

        assert "Image loaded" in display
        assert "unknown" in display  # Default for missing file_path and media_type
        assert multimodal is not None

    def test_invalid_json_returns_unchanged(self):
        """Test that invalid JSON returns the original string."""
        result = "This is not {valid json"
        display, multimodal = extract_multimodal_content(result)

        assert display == result
        assert multimodal is None

    def test_empty_multimodal_content(self):
        """Test handling of empty multimodal_content array."""
        result = json.dumps(
            {
                "multimodal_content": [],
                "media_type": "image/png",
                "file_path": "/path/to/image.png",
            }
        )
        display, multimodal = extract_multimodal_content(result)

        # Empty array should still be extracted
        assert multimodal == []

    def test_multimodal_content_with_non_dict_items(self):
        """Test handling of malformed multimodal_content items."""
        result = json.dumps(
            {
                "multimodal_content": [
                    "not a dict",
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                ],
                "file_path": "/path/to/image.png",
            }
        )
        display, multimodal = extract_multimodal_content(result)

        # Should extract the array as-is
        assert len(multimodal) == 2


class TestMultimodalContentIntegration:
    """Integration tests for multimodal content handling."""

    def test_realistic_image_read_output(self):
        """Test with realistic output from read_file with media_type='auto'."""
        # Simulate what ReadFileTool._read_as_multimodal would return
        result = json.dumps(
            {
                "multimodal_content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhK4miQAAAABJRU5ErkJggg=="
                        },
                    }
                ],
                "media_type": "image/png",
                "file_path": ".wichy/test_multimodal_ai_wiki.png",
                "file_size_bytes": 444308,
                "note": "This content can be passed directly to vision-capable LLMs",
            }
        )

        display, multimodal = extract_multimodal_content(result)

        assert "Image loaded" in display
        assert ".wichy/test_multimodal_ai_wiki.png" in display
        assert "444308 bytes" in display
        assert multimodal is not None
        assert multimodal[0]["type"] == "image_url"
        assert "base64," in multimodal[0]["image_url"]["url"]


class TestErrorIndicatesMultimodalNotSupported:
    """Tests for the error_indicates_multimodal_not_supported function."""

    def test_image_url_error(self):
        """Test detection of image_url error."""
        error = Exception("Invalid content type: image_url not supported")
        assert error_indicates_multimodal_not_supported(error) is True

    def test_generic_image_error(self):
        """Test detection of generic image error."""
        error = Exception("Model does not support image content")
        assert error_indicates_multimodal_not_supported(error) is True

    def test_unrelated_error(self):
        """Test that unrelated errors are not detected as multimodal errors."""
        error = Exception("Rate limit exceeded")
        assert error_indicates_multimodal_not_supported(error) is False

    def test_context_length_error(self):
        """Test that context length errors are not detected as multimodal errors."""
        error = Exception("maximum context length exceeded")
        assert error_indicates_multimodal_not_supported(error) is False

    def test_vision_keyword_error(self):
        """Test detection of vision-related error."""
        error = Exception("This model does not have vision capabilities")
        assert error_indicates_multimodal_not_supported(error) is True

    def test_content_type_error(self):
        """Test detection of content type error."""
        error = Exception("unsupported content type")
        assert error_indicates_multimodal_not_supported(error) is True

    def test_openrouter_no_endpoints_error(self):
        """Test detection of OpenRouter-style 'no endpoints found' error."""
        # This is the error from: openai.NotFoundError: Error code: 404
        # {'error': {'message': 'No endpoints found that support image input', 'code': 404}}
        error = Exception(
            "Error code: 404 - {'error': {'message': 'No endpoints found that support image input', 'code': 404}}"
        )
        assert error_indicates_multimodal_not_supported(error) is True

    def test_openrouter_no_endpoints_error_case_insensitive(self):
        """Test that the detection is case-insensitive."""
        error = Exception("NO ENDPOINTS FOUND THAT SUPPORT IMAGE INPUT")
        assert error_indicates_multimodal_not_supported(error) is True

    def test_image_input_not_supported_error(self):
        """Test detection of 'image input not supported' error."""
        error = Exception("image input not supported for this model")
        assert error_indicates_multimodal_not_supported(error) is True


class TestLLMBackendMultimodalNotSupported:
    """Tests for the LLMBackendMultimodalNotSupported exception."""

    def test_exception_creation(self):
        """Test creating the exception."""
        exc = LLMBackendMultimodalNotSupported("Model does not support images")
        assert str(exc) == "Model does not support images"
        assert exc.message == "Model does not support images"

    def test_exception_default_message(self):
        """Test exception with default message."""
        exc = LLMBackendMultimodalNotSupported()
        assert "multimodal" in str(exc).lower()
        assert "does not support" in str(exc).lower()
