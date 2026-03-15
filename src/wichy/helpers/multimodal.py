"""
Multimodal content handling utilities.

Provides functions for extracting multimodal content from tool results
and fixing context when models don't support multimodal input.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from wichy.constants import ROLE_USER


def extract_multimodal_content(
    result: str,
) -> Tuple[str, Optional[List[Dict[str, Any]]]]:
    """
    Check if a tool result contains multimodal content.

    Args:
        result: The tool result string (may be JSON with multimodal_content)

    Returns:
        Tuple of (display_content, multimodal_content_parts or None)
    """
    try:
        data = json.loads(result)
        if isinstance(data, dict) and "multimodal_content" in data:
            # Extract the multimodal content parts
            multimodal_parts = data.get("multimodal_content")
            file_path = data.get("file_path", "unknown")
            media_type = data.get("media_type", "unknown")
            file_size = data.get("file_size_bytes", 0)

            # Create a text summary for the tool result
            display_content = (
                f"Image loaded: {file_path} ({media_type}, {file_size} bytes). "
                "The image is now available in the conversation for analysis."
            )

            return display_content, multimodal_parts
    except (json.JSONDecodeError, TypeError):
        pass

    return result, None


def build_multimodal_user_message(
    multimodal_parts: List[Dict[str, Any]],
    text_prompt: str = "Here is the image that was loaded:",
) -> Dict[str, Any]:
    """
    Build a user message containing multimodal content.

    Args:
        multimodal_parts: List of multimodal content parts (e.g., image_url blocks)
        text_prompt: Optional text to prepend before the images

    Returns:
        A user message dict with multimodal content
    """
    return {
        "role": ROLE_USER,
        "content": [
            {"type": "text", "text": text_prompt},
            *multimodal_parts,
        ],
    }


def fix_multimodal_context(context_handler) -> bool:
    """
    Find and replace multimodal content in context with text placeholders.
    Uses the ContextHandler's update_message method to properly persist changes.

    Args:
        context_handler: A ContextHandler instance with .context and .update_message()

    Returns:
        True if any multimodal content was found and replaced, False otherwise.
    """
    found_multimodal = False

    # Iterate in reverse to handle indices safely when updating
    for i in range(len(context_handler.context) - 1, -1, -1):
        msg = context_handler.context[i]
        content = msg.get("content")

        # Check if content is a list (multimodal format)
        if isinstance(content, list):
            # Find text parts and image parts
            text_parts = []
            image_count = 0

            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        image_count += 1

            # Replace with text-only message
            if image_count > 0:
                text_content = " ".join(text_parts) if text_parts else ""
                if image_count == 1:
                    text_content += " [Image content cannot be displayed - model does not support images]"
                else:
                    text_content += f" [{image_count} images cannot be displayed - model does not support images]"

                # Create new message with text content
                new_msg = {
                    "role": msg.get("role", ROLE_USER),
                    "content": text_content.strip(),
                }
                # Preserve tool_call_id if present
                if "tool_call_id" in msg:
                    new_msg["tool_call_id"] = msg["tool_call_id"]

                # Use ContextHandler's update_message to properly persist
                context_handler.update_message(i, new_msg)
                found_multimodal = True

    return found_multimodal
