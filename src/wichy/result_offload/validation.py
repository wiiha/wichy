"""
Validation module for result offload feature.

This module provides validation logic to ensure summarizer responses
are accurate and complete when querying stored tool results.
"""

from dataclasses import dataclass
from typing import List

from wichy.result_offload.store import StoredResult
from wichy.result_offload.hijack import format_stored_results
from wichy.llm_backend import call
from wichy.constants import ROLE_SYSTEM, ROLE_USER


@dataclass
class ValidationResult:
    """Result of validating a summarizer response."""

    is_valid: bool
    feedback: str


def validate_summarizer_response(
    results: List[StoredResult],
    query: str,
    response: str,
    model_str: str,
) -> ValidationResult:
    """
    Validate a summarizer response against the data and query.

    Args:
        results: The stored results being queried
        query: The original query
        response: The summarizer's response to validate
        model_str: Model string for LLM call

    Returns:
        ValidationResult with is_valid and feedback
    """
    # Format results for validation prompt (raw results only, no instructions)
    formatted_results = format_stored_results(results)

    validation_prompt = f"""You are a validator. Your job is to determine if a response reasonably answers a query given the available data.

## Stored Results

{formatted_results}

## Query
{query}

## Response to Validate
{response}

## Task
Answer: Is this response reasonable given the data and query?

Consider:
1. Does the response address the query?
2. Is the response supported by the data?
3. Are there significant omissions or hallucinations?

Respond in this exact format:
VALID: <brief justification>
OR
INVALID: <specific feedback for improvement>
"""

    context = [
        {
            "role": ROLE_SYSTEM,
            "content": "You are a validator that ensures responses are accurate and complete.",
        },
        {"role": ROLE_USER, "content": validation_prompt},
    ]

    # Call LLM with error handling
    try:
        llm_response = call(
            context=context,
            tool_defs=None,
            model_str=model_str,
            extra_args={"max_tokens": 500},
        )

        if llm_response is None or llm_response.message is None:
            # LLM call failed - treat as valid to avoid blocking
            return ValidationResult(
                is_valid=True, feedback="Validation skipped due to LLM error"
            )

        response_text = llm_response.message.content.strip()

    except Exception as e:
        # Error during validation - treat as valid to avoid blocking
        return ValidationResult(
            is_valid=True, feedback=f"Validation skipped due to error: {e}"
        )

    # Parse validation response
    if response_text.startswith("VALID:"):
        return ValidationResult(is_valid=True, feedback=response_text[6:].strip())
    elif response_text.startswith("INVALID:"):
        return ValidationResult(is_valid=False, feedback=response_text[8:].strip())
    else:
        # Ambiguous response - treat as valid with caution
        return ValidationResult(is_valid=True, feedback="Ambiguous validation response")
