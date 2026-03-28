"""Tests for error handling utilities in wichy.tools.errors."""

from wichy.tools.errors import format_error, format_error_with_context


class TestFormatError:
    """Test suite for format_error function."""

    def test_format_error_simple_message(self):
        """Test format_error with a simple message."""
        result = format_error("file not found")
        assert result == "error: file not found"

    def test_format_error_returns_error_prefix(self):
        """Test that format_error returns string with 'error: ' prefix."""
        result = format_error("something went wrong")
        assert result.startswith("error: ")

    def test_format_error_preserves_message(self):
        """Test that format_error preserves the original message content."""
        message = "failed to read file: permission denied"
        result = format_error(message)
        assert message in result
        assert result == f"error: {message}"

    def test_format_error_with_exception_object(self):
        """Test format_error with an exception message."""
        try:
            raise ValueError("invalid value")
        except ValueError as e:
            result = format_error(str(e))
            assert result == "error: invalid value"

    def test_format_error_empty_string(self):
        """Test format_error with empty string."""
        result = format_error("")
        assert result == "error: "


class TestFormatErrorWithContext:
    """Test suite for format_error_with_context function."""

    def test_format_error_with_context_basic(self):
        """Test format_error_with_context with basic context and message."""
        result = format_error_with_context("my_file.txt", "file not found")
        assert result == "error: my_file.txt: file not found"

    def test_format_error_with_context_file_path(self):
        """Test format_error_with_context with file path."""
        result = format_error_with_context("/path/to/file.py", "permission denied")
        assert result == "error: /path/to/file.py: permission denied"
        assert "/path/to/file.py" in result

    def test_format_error_with_context_preserves_both(self):
        """Test that format_error_with_context preserves both context and message."""
        context = "https://example.com/api"
        message = "connection timeout"
        result = format_error_with_context(context, message)
        assert context in result
        assert message in result
        assert result == f"error: {context}: {message}"
