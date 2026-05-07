"""
Query Result Tool for the result offload feature.

This tool allows agents to query stored (offloaded) tool results using
natural language. An LLM-powered summarizer analyzes the stored content
and returns relevant information based on the query, with validation.
"""

from typing import List, Optional

from pydantic import Field

from wichy.config import settings
from wichy.constants import HIDE_FROM_LLM_PREFIX, ROLE_SYSTEM, ROLE_USER
from wichy.llm_backend import call
from wichy.result_offload.hijack import format_results_for_summarizer
from wichy.result_offload.store import StoredResult, get_result_store
from wichy.result_offload.validation import validate_summarizer_response
from wichy.tools.base import BaseTool, ParametersModel


class QueryResultParameters(ParametersModel):
    """Parameters for the query_result tool."""

    ref_ids: List[str] = Field(
        description="List of reference IDs from offloaded results to query. "
        "Can be a single ID or multiple IDs to query together.",
        min_length=1,
    )

    query: str = Field(
        description="The question to answer using the stored result(s). "
        "Be specific about what you're looking for."
    )

    # Hidden parameter - injected by agent, not shown to LLM
    model_str: str = Field(
        default="",
        description=HIDE_FROM_LLM_PREFIX + " Model string from the calling agent",
    )

    def info(self) -> str:
        """Return a human-readable string describing this query."""
        ids = ""
        if self.ref_ids:
            ids = ",".join(self.ref_ids)
        return f'query="{self.query}" ref_ids="{ids}"'


class QueryResultTool(BaseTool):
    """Query stored tool results using natural language."""

    name = "query_result"
    description = "Query an offloaded tool result using natural language"
    description_long = """
Use this tool when you receive a [RESULT_OFFLOADED] reference.

Provide one or more reference IDs and your question about the data.
An LLM will analyze the stored result(s) and answer your question.

The response is validated by a separate LLM to ensure accuracy.
If validation fails, the query is retried with feedback.

Examples:
  query_result(ref_ids=["res_abc123"], query="What are the main functions?")
  query_result(ref_ids=["res_abc123", "res_def456"], query="Compare these results")

Avoid:
  # The following query questions should be avoided. Use them ONLY if it is vital to get the actual full content, otherwise, try to get summaries.
  query_result(ref_ids=["res_abc123"], query="Show me the full file content")
  query_result(ref_ids=["res_abc567"], query="Show me the exact output")
"""

    parameters_model = QueryResultParameters

    def execute(self, **kwargs) -> str:
        """Execute the query result tool."""
        params = self.parameters_model(**kwargs)

        ref_ids = params.ref_ids
        query = params.query
        model_str = params.model_str

        if settings.query_result_tool_model_str:
            model_str = settings.query_result_tool_model_str

        # Validate model_str
        if not model_str:
            return "Error: model_str is required (internal error - should be injected, surface this to the user.)"

        # Load all requested results
        store = get_result_store()
        results: List[StoredResult] = []

        for ref_id in ref_ids:
            stored = store.load(ref_id)
            if stored is None:
                return f"Error: Result '{ref_id}' not found or has expired."
            results.append(stored)

        # Run summarizer + validator loop
        response = self._summarize_with_validation(
            results=results,
            query=query,
            model_str=model_str,
        )

        return response

    def _summarize_with_validation(
        self,
        results: List[StoredResult],
        query: str,
        model_str: str,
    ) -> str:
        """
        Run the summarize -> validate -> retry loop.

        Flow:
        1. Call summarizer with results + query
        2. Call validator with results + query + response
        3. If invalid, retry with feedback (max retries from settings)
        4. Return final response
        """
        max_retries = settings.result_offload_max_validation_retries

        # Initial summarization
        response = self._call_summarizer(
            results=results,
            query=query,
            model_str=model_str,
        )

        # Validation loop
        for attempt in range(max_retries + 1):
            validation = validate_summarizer_response(
                results=results,
                query=query,
                response=response,
                model_str=model_str,
            )

            if validation.is_valid:
                return response

            # Retry with feedback
            if attempt < max_retries:
                response = self._call_summarizer(
                    results=results,
                    query=query,
                    model_str=model_str,
                    previous_feedback=validation.feedback,
                )

        # Max retries reached - return with warning
        return f"[Validation warning: {validation.feedback}]\n\n{response}"

    def _call_summarizer(
        self,
        results: List[StoredResult],
        query: str,
        model_str: str,
        previous_feedback: Optional[str] = None,
    ) -> str:
        """Call the summarizer LLM with error handling."""
        # Build prompt
        prompt = format_results_for_summarizer(results=results, query=query)

        if previous_feedback:
            prompt += f"""

Note: A previous attempt at answering was flagged as incomplete:
{previous_feedback}

Please address this feedback in your response.
"""

        # Build context for LLM call
        context = [
            {
                "role": ROLE_SYSTEM,
                "content": "You are a helpful assistant that analyzes data and answers questions accurately.",
            },
            {"role": ROLE_USER, "content": prompt},
        ]

        # Call LLM with error handling
        try:
            llm_response = call(
                context=context,
                tool_defs=None,
                model_str=model_str,
            )

            if llm_response is None or llm_response.message is None:
                return f"Error: LLM returned no response for query '{query}'"

            return llm_response.message.content

        except Exception as e:
            return f"Error querying stored result: {e}"
