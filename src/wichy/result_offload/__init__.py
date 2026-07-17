"""
Result offloading system for preventing context rot.

This module provides:
- ResultStore: SQLite-backed storage for large tool results
- result_or_ref: Decision logic for offloading results
- QueryResultTool: Tool for querying stored results via LLM
"""

from wichy.result_offload.store import ResultStore, StoredResult, get_result_store
from wichy.result_offload.hijack import (
    result_or_ref,
    format_stored_results,
    format_results_for_summarizer,
)
from wichy.result_offload.query_tool import QueryResultTool
from wichy.result_offload.validation import (
    ValidationResult,
    validate_summarizer_response,
)

__all__ = [
    "ResultStore",
    "StoredResult",
    "get_result_store",
    "result_or_ref",
    "format_stored_results",
    "format_results_for_summarizer",
    "QueryResultTool",
    "ValidationResult",
    "validate_summarizer_response",
]
