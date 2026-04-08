"""
Offload decision logic for tool results.

This module provides the `result_or_ref` function which determines whether
a tool result should be offloaded to disk (to prevent context rot) or
passed through to the agent.

Usage:
    from wichy.result_offload.hijack import result_or_ref

    result = result_or_ref(
        result=large_tool_output,
        tool_name="read_file",
        input_args={"path": "some/file.py"},
        model_str="openai:gpt-4",
        enable_offload=True,
    )
"""

from typing import Any, Dict, Optional

from wichy.config import settings
from wichy.helpers.multimodal import extract_multimodal_content
from wichy.result_offload.store import get_result_store


def result_or_ref(
    result: str,
    tool_name: str,
    input_args: Dict[str, Any],
    model_str: Optional[str] = None,
    enable_offload: bool = True,
    can_query_results: bool = True,
) -> str:
    """
    Decide whether to offload a result and return either the original
    or a formatted reference response.

    The logic:
    1. Handle edge cases (None, non-string, empty) → pass through
    2. If result contains multimodal content → pass through (LLM needs actual data)
    3. If agent can't query results (can_query_results=False) → pass through
    4. If offloading disabled for this tool → pass through
    5. If result size <= threshold → pass through
    6. If result size <= threshold * (1 + tolerance) → pass through
    7. Otherwise → offload (store + return reference)

    Args:
        result: The tool result string
        tool_name: Name of the tool (for metadata)
        input_args: Input arguments to the tool (for metadata)
        model_str: Optional model string (for metadata)
        enable_offload: Whether offloading is enabled for this tool
        can_query_results: If False, skip offload entirely (agent lacks query_result tool)

    Returns:
        Either the original result (pass-through) or a formatted
        reference response with preview and instructions.
    """
    # -------------------------------------------------------------------------
    # Handle edge cases
    # -------------------------------------------------------------------------
    # None result → return empty string
    if result is None:
        return ""

    # Non-string result → convert to string (e.g., bytes from read_file)
    if not isinstance(result, str):
        result = str(result)

    # Empty result → nothing to offload
    if not result:
        return result

    # -------------------------------------------------------------------------
    # Check 0: Don't offload multimodal content (images, etc.)
    # -------------------------------------------------------------------------
    # Multimodal content must reach the LLM intact for vision processing.
    # If we offload, the LLM receives a reference string instead of the image.
    _, multimodal_parts = extract_multimodal_content(result)
    if multimodal_parts is not None:
        return result

    # -------------------------------------------------------------------------
    # Check 1: If agent can't query results, skip offload entirely
    # -------------------------------------------------------------------------
    # Without query_result tool, the agent cannot retrieve offloaded results
    if not can_query_results:
        return result

    # -------------------------------------------------------------------------
    # Get settings
    # -------------------------------------------------------------------------
    threshold = settings.result_offload_threshold
    tolerance = settings.result_offload_tolerance
    preview_chars = settings.result_offload_preview_chars

    # Cap preview at 1000 chars
    preview_chars = min(preview_chars, 1000)

    result_len = len(result)

    # Check 1: Offloading disabled for this tool
    if not enable_offload:
        return result

    # Check 2: Result within threshold
    if result_len <= threshold:
        return result

    # Check 3: Result within tolerance (threshold * (1 + tolerance))
    tolerance_threshold = int(threshold * (1 + tolerance))
    if result_len <= tolerance_threshold:
        return result

    # Check 4: Offload!
    store = get_result_store()
    ref_id = store.save(
        content=result,
        tool_name=tool_name,
        input_args=input_args,
        model_str=model_str,
    )

    # Format reference response
    return _format_offload_response(
        ref_id=ref_id,
        result=result,
        tool_name=tool_name,
        preview_chars=preview_chars,
    )


def _format_offload_response(
    ref_id: str,
    result: str,
    tool_name: str,
    preview_chars: int,
) -> str:
    """
    Format the offload response with preview and instructions.

    Args:
        ref_id: The stored reference ID
        result: The full result (for preview)
        tool_name: Name of the tool (for context)
        preview_chars: Number of preview characters

    Returns:
        Formatted response string
    """
    result_len = len(result)

    # Create preview (first N chars)
    preview = result[:preview_chars]
    if len(result) > preview_chars:
        preview += "..."

    return f"""[RESULT_OFFLOADED]
Reference ID: {ref_id}
Tool: {tool_name}
Size: {result_len:,} characters

--- PREVIEW ({preview_chars} chars) ---
{preview}

--- END PREVIEW ---

This result was too large for the context window and has been stored.

To query this result, use the `query_result` tool:
  query_result(ref_ids=["{ref_id}"], query="your question here")

You can pass multiple reference IDs to query multiple stored results at once.
"""


def format_stored_results(results: list) -> str:
    """
    Format stored results as a simple string with metadata and content.

    This is a low-level helper that returns ONLY the raw formatted results,
    with no prompt wrapper, instructions, or query. Suitable for inclusion
    in prompts by both summarizer and validator.

    Args:
        results: List of StoredResult objects to format

    Returns:
        Formatted string with results metadata and content
    """
    return "\n\n".join(
        f"""--- RESULT {i} ---
Reference ID: {r.ref_id}
Tool: {r.tool_name}
Size: {r.char_count:,} characters
Created: {r.created_at.isoformat()}

{r.content}"""
        for i, r in enumerate(results, 1)
    )


def format_results_for_summarizer(results: list, query: str) -> str:
    """
    Format a system prompt for the summarizer LLM.

    This is used internally by the query_result tool.

    Args:
        results: List of StoredResult objects to format
        query: The user's query about the results

    Returns:
        Formatted prompt string for the summarizer LLM
    """
    formatted_results = format_stored_results(results)

    return f"""You are analyzing stored tool results to answer a query.

## Stored Results

{formatted_results}

## Query

{query}

## Instructions

1. Analyze the stored results carefully
2. Answer the query using only information from the stored results
3. Be specific and reference relevant parts of the data
4. If the query cannot be answered from the data, say so clearly
5. Do not hallucinate or make up information

Note: It is only your response that will be passed back. Do not make reference to the result it self in phrases like "the result can be read above", "...is fully included above." or similar.

Provide your response below:
"""
