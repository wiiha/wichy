import base64
import json
import mimetypes
from typing import Optional

from pydantic import Field

from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error

# Supported image MIME types for multimodal LLM APIs
SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


class ReadFileParameters(ParametersModel):
    path: str = Field(
        ...,
        description="path to file for which to look at content of",
    )
    offset: int = Field(
        1,
        description="starting line number (1-indexed), default=1",
    )
    limit: int = Field(
        2000,
        description="maximum number of lines to read, default=2000",
    )
    show_none_printable_chars: bool = Field(
        False,
        description="show all non-printing characters (like cat -A): show $ at line ends, TAB as ^I, and other non-printables in ^ notation",
    )
    media_type: Optional[str] = Field(
        None,
        description="For binary/image files: request the file be returned as base64-encoded data. Set to 'auto' to auto-detect based on file extension, or specify a MIME type like 'image/png'. When set, returns a JSON object with 'multimodal_content' containing base64 data suitable for vision-capable LLMs.",
    )

    def info(self):
        if (
            self.offset == 1
            and self.limit == 2000
            and not self.show_none_printable_chars
            and not self.media_type
        ):
            return self.path
        parts = [self.path]
        if self.media_type:
            parts.append(f"media_type={self.media_type}")
        elif self.offset != 1 or self.limit != 2000:
            parts.append(f"lines {self.offset}-{self.offset + self.limit - 1}")
        return " ".join(parts)


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Get the content of a file."
    description_long = """
Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:

- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Any lines longer than 2000 characters will be truncated
- Results are returned using cat -n format, with line numbers starting at 1
- This tool can only read files, not directories. To read a directory, use ls tool.
- You can call multiple tools in a single response. It is always better to speculatively read multiple potentially useful files in parallel.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents.

Multimodal Support (images):

- For image files, set media_type='auto' to return base64-encoded image data
- The returned JSON contains a 'multimodal_content' field with OpenAI-compatible content blocks
- Supported formats: JPEG, PNG, GIF, WebP
- Use this when the user wants to show an image to a vision-capable LLM"""
    enable_result_offload = True
    parameters_model = ReadFileParameters

    MAX_LINE_LENGTH = 2000

    def _detect_mime_type(self, path: str) -> Optional[str]:
        """Detect MIME type from file extension."""
        mime_type, _ = mimetypes.guess_type(path)
        return mime_type

    def _read_as_multimodal(self, path: str, media_type: Optional[str]) -> str:
        """Read a binary file and return multimodal JSON content."""
        # Determine MIME type
        if media_type and media_type != "auto":
            mime_type = media_type
        else:
            mime_type = self._detect_mime_type(path)

        if not mime_type:
            return json.dumps(
                {
                    "error": f"Could not determine MIME type for '{path}'. Specify media_type explicitly."
                }
            )

        if mime_type not in SUPPORTED_IMAGE_TYPES:
            return json.dumps(
                {
                    "error": f"Unsupported media type '{mime_type}'. Supported types: {', '.join(sorted(SUPPORTED_IMAGE_TYPES))}"
                }
            )

        try:
            with open(path, "rb") as f:
                binary_data = f.read()

            base64_data = base64.b64encode(binary_data).decode("utf-8")

            # Return OpenAI-compatible multimodal content structure
            result = {
                "multimodal_content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"},
                    }
                ],
                "media_type": mime_type,
                "file_path": path,
                "file_size_bytes": len(binary_data),
                "note": "This content can be passed directly to vision-capable LLMs",
            }
            return json.dumps(result, indent=2)

        except FileNotFoundError:
            return json.dumps({"error": f"File not found: {path}"})
        except PermissionError:
            return json.dumps({"error": f"Permission denied: {path}"})
        except Exception as e:
            return json.dumps({"error": f"Error reading file: {e}"})

    def _visualize_line(self, line: str) -> str:
        """Visualize non-printing characters like cat -A"""
        # Show $ at the end to mark line ending
        result = line + "$"
        # Convert tabs to ^I
        result = result.replace("\t", "^I")
        # Convert other non-printing characters to ^ notation
        # Characters below 32 (except tab, newline, carriage return) and DEL (127)
        visualized = []
        for char in result:
            code = ord(char)
            if code < 32 and char not in ("\n", "\r", "\t"):
                visualized.append(f"^{chr(code + 64)}")
            elif code == 127:
                visualized.append("^?")
            else:
                visualized.append(char)
        return "".join(visualized)

    def execute(
        self,
        path,
        offset=1,
        limit=2000,
        show_none_printable_chars=False,
        media_type=None,
    ) -> str:
        """Execute read_file with optional offset, limit, show none printable chars, and media_type options."""
        # Handle multimodal request for binary/image files
        if media_type:
            return self._read_as_multimodal(path, media_type)

        # Try to detect if this is a binary file that should be read as multimodal
        detected_type = self._detect_mime_type(path)
        if detected_type in SUPPORTED_IMAGE_TYPES:
            # Auto-suggest using media_type parameter
            return json.dumps(
                {
                    "info": f"File '{path}' appears to be an image ({detected_type}). Set media_type='auto' to read as base64 image data for vision-capable LLMs.",
                    "detected_type": detected_type,
                    "hint": "Use media_type='auto' to get multimodal content",
                }
            )

        try:
            # Read the file
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Check if file is empty
            if not lines:
                return f"warning: file '{path}' exists but is empty"

            # Apply offset and limit (offset is 1-indexed)
            start_idx = offset - 1
            end_idx = start_idx + limit

            if start_idx >= len(lines):
                return format_error(
                    f"offset {offset} exceeds file length ({len(lines)} lines)"
                )

            selected_lines = lines[start_idx:end_idx]

            # Format with line numbers (cat -n style) and truncate long lines
            output_lines = []
            truncated_line_warning = False
            for i, line in enumerate(selected_lines, start=offset):
                # Remove trailing newline for processing
                line = line.rstrip("\n")

                # Apply -A option: visualize non-printing characters
                if show_none_printable_chars:
                    line = self._visualize_line(line)
                else:
                    # Truncate if longer than MAX_LINE_LENGTH (only when not using -A)
                    if len(line) > self.MAX_LINE_LENGTH:
                        line = line[: self.MAX_LINE_LENGTH] + "... [truncated]"
                        truncated_line_warning = True

                if show_none_printable_chars:
                    # In -A mode, show as-is without line numbers
                    output_lines.append(line)
                else:
                    # Format with line number (6 spaces for alignment like cat -n)
                    output_lines.append(f"{i:6d}  {line}")

            result = "\n".join(output_lines)

            # Add warning if file is longer than what we read
            if end_idx < len(lines):
                result += f"\n\nwarning: file has {len(lines)} lines total, but only showing lines {offset}-{min(end_idx, len(lines))}, do consecutive reads to get the rest if needed."

            # Add warning if any lines were truncated
            if truncated_line_warning:
                result += f"\n\nwarning: some lines exceeded {self.MAX_LINE_LENGTH} characters and were truncated"

            return result

        except FileNotFoundError:
            return format_error(f"file not found: {path}")
        except IsADirectoryError:
            return format_error(
                f"'{path}' is a directory, not a file. Use ls tool to read directories."
            )
        except PermissionError:
            return format_error(f"permission denied: {path}")
        except UnicodeDecodeError:
            return format_error(
                f"file '{path}' appears to be binary and cannot be read as text"
            )
        except Exception as e:
            return format_error(str(e))
