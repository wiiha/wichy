"""LLM-based session map extraction."""

import json
from datetime import datetime

from wichy.config import settings
from wichy.console import user_console
from wichy.llm_backend import call

from .models import Edge, EdgeType, Node, NodeType, SessionMap, generate_node_id


class ExtractionParseError(Exception):
    """Raised when LLM response cannot be parsed as valid JSON."""

    pass


# Maximum length for message content before truncation
MAX_MESSAGE_LENGTH = 8000

# =============================================================================
# Prompts
# =============================================================================

EXTRACTION_PROMPT = """You are analyzing a conversation to extract a structured session map.

## Existing Session Map
{existing_map_summary}

## New Conversation (since last extraction)
{new_messages}

## Your Task
Extract ONLY NEW and SIGNIFICANT items from the conversation. Do NOT re-extract items that already exist in the map.

### Node Types
- QUESTION: Questions asked by user or agent to investigate something
- FINDING: Facts, discoveries, or observations made during investigation
- DECISION: Choices or decisions made (what to do, what approach to take)
- FILE: Files explored with brief summary of what was found/relevant
- DEAD_END: Paths explored that were abandoned or didn't work out

### Edge Types (connections between nodes)
- LED_TO: A question led to a finding
- ANSWERED_BY: A question was answered by a finding
- EXPLORED: A finding came from exploring a file
- RULED_OUT: A decision ruled out a path
- RELATED: General relationship
- FOLLOWS: Temporal sequence (one thing followed another)

## Output Format
Output valid JSON with this structure:
{{
  "nodes": [
    {{
      "type": "question|finding|decision|file|dead_end",
      "content": "The actual content - be concise but complete",
      "turn": <conversation turn number>,
      "connects_to": ["existing_node_id_1", "existing_node_id_2"]
    }}
  ],
  "edges": [
    {{
      "from": "node content or new node index (0, 1, 2...)",
      "to": "node content or id",
      "type": "led_to|answered_by|explored|ruled_out|related|follows"
    }}
  ]
}}

## Guidelines
1. Be selective - only extract truly significant items
2. For FILE nodes, include filename AND brief summary
3. For DECISION nodes, include the rationale
4. For DEAD_END nodes, include why it was abandoned
5. Connect to existing nodes when there's a clear relationship
6. Use existing node IDs from the existing map when connecting
7. For new nodes, you can reference them by index (0, 1, 2...) in edges
8. Skip trivial exchanges, greetings, and small talk
"""

VALIDATION_PROMPT = """You are validating a session map extraction.

## Existing Session Map
{existing_map_summary}

## New Conversation Excerpt
{new_messages}

## Proposed Extraction
{proposed_extraction}

## Validation Criteria
1. RELEVANCE: Are all extracted nodes actually significant to the conversation?
2. TYPE CORRECTNESS: Are node types correct?
   - QUESTION should be actual questions
   - FINDING should be facts/discoveries
   - DECISION should be choices made
   - FILE should include filename AND summary
   - DEAD_END should explain why abandoned
3. EDGE SANITY: Do edges connect logically? Are relationships correct?
4. ID VALIDITY: Do references to existing node IDs actually exist in the map?
5. COMPLETENESS: Is anything significant missing?
6. NO DUPLICATES: Are new nodes truly new (not duplicates of existing nodes)?

## Response Format
- If valid: "VALID: <brief confirmation>"
- If invalid: "INVALID: <specific issues and corrections needed>"

Do NOT re-output the extraction. Only state VALID or INVALID with explanation.
"""


# =============================================================================
# Formatting Functions
# =============================================================================


def format_messages_for_extraction(messages: list[dict], start_turn: int = 0) -> str:
    """Format conversation messages for extraction prompt."""
    lines = []
    turn = start_turn

    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        # Skip system messages
        if role == "system":
            continue

        # Track turns (user + assistant = one turn)
        if role == "user":
            turn += 1

        # Truncate very long messages
        if len(content) > MAX_MESSAGE_LENGTH:
            content = content[:MAX_MESSAGE_LENGTH] + "...[truncated]"

        lines.append(f"[Turn {turn}] {role.upper()}: {content}")

    return "\n\n".join(lines)


def format_extraction_for_display(nodes: list[dict], edges: list[dict]) -> str:
    """Format proposed extraction for validation prompt."""
    lines = ["### Nodes"]
    for i, node in enumerate(nodes):
        lines.append(f"  [{i}] {node.get('type')}: {node.get('content', '')[:100]}")

    lines.append("\n### Edges")
    for edge in edges:
        lines.append(f"  {edge.get('from')} --[{edge.get('type')}]--> {edge.get('to')}")

    return "\n".join(lines)


# =============================================================================
# Parsing Functions
# =============================================================================


def parse_extraction_response(response_content: str) -> tuple[list[dict], list[dict]]:
    """Parse LLM JSON response into nodes and edges.

    Raises:
        ExtractionParseError: If response cannot be parsed as valid JSON.
    """
    try:
        data = json.loads(response_content)
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        return nodes, edges
    except json.JSONDecodeError:
        # Try to extract JSON from response
        import re

        json_match = re.search(r"\{[\s\S]*\}", response_content)
        if json_match:
            try:
                data = json.loads(json_match.group())
                nodes = data.get("nodes", [])
                edges = data.get("edges", [])
                return nodes, edges
            except json.JSONDecodeError:
                pass

        # Raise exception instead of silently returning empty arrays
        raise ExtractionParseError(
            f"Could not parse extraction response as JSON: {response_content[:200]}"
        )


def parse_validation_response(response_content: str) -> tuple[bool, str]:
    """Parse validation response into (is_valid, feedback)."""
    content = response_content.strip().upper()

    if content.startswith("VALID"):
        # Extract explanation
        feedback = response_content.strip()[5:].strip().strip(":")
        return True, feedback or "Extraction validated successfully"

    elif content.startswith("INVALID"):
        # Extract issues
        feedback = response_content.strip()[7:].strip().strip(":")
        return False, feedback or "Validation failed"

    else:
        # Ambiguous response - treat as valid with warning
        return True, f"Ambiguous validation response: {response_content[:100]}"


# =============================================================================
# Main Extraction Class
# =============================================================================


class SessionMapExtractor:
    """Extracts session map from conversation using LLM."""

    def __init__(self, model_str: str | None = None):
        self.model_str = model_str

    def _get_model_str(self) -> str:
        """Get effective model string."""
        if self.model_str:
            return self.model_str
        raise ValueError(
            "No model_str provided for session map extraction. "
            "Please pass a model_str when creating SessionMapExtractor."
        )

    def extract(
        self,
        messages: list[dict],
        existing_map: SessionMap | None,
        start_turn: int = 0,
    ) -> tuple[list[Node], list[Edge], list[dict]]:
        """Extract nodes and edges from conversation messages.

        Returns:
            Tuple of (nodes, edges, skipped_edges) where skipped_edges contains
            edges that referenced non-existent node IDs.
        """

        # Format existing map summary
        existing_summary = "None (empty map)"
        if existing_map and existing_map.nodes:
            existing_summary = existing_map.get_summary()

        # Format messages
        formatted_messages = format_messages_for_extraction(messages, start_turn)

        # Build prompt
        prompt = EXTRACTION_PROMPT.format(
            existing_map_summary=existing_summary,
            new_messages=formatted_messages,
        )

        # Call LLM
        response = call(
            context=[{"role": "user", "content": prompt}],
            model_str=self._get_model_str(),
            extra_args={"response_format": {"type": "json_object"}},
        )

        # Parse response
        proposed_nodes, proposed_edges = parse_extraction_response(
            response.message.content
        )

        # Convert to Node and Edge objects
        nodes, edges, skipped_edges = self._convert_to_objects(
            proposed_nodes, proposed_edges, start_turn, existing_map
        )

        return nodes, edges, skipped_edges

    def extract_with_validation(
        self,
        messages: list[dict],
        existing_map: SessionMap | None,
        start_turn: int = 0,
        max_retries: int | None = None,
    ) -> tuple[bool, list[Node], list[Edge], str]:
        """Extract with validation retry loop.

        Returns:
            (is_valid, nodes, edges, feedback)
        """
        max_retries = max_retries or settings.session_map_validation_retries
        existing_summary = "None (empty map)"
        if existing_map and existing_map.nodes:
            existing_summary = existing_map.get_summary()

        formatted_messages = format_messages_for_extraction(messages, start_turn)

        feedback = None
        proposed_nodes: list[dict] = []
        proposed_edges: list[dict] = []
        previous_response = None

        for attempt in range(max_retries + 1):
            try:
                # Build prompt - with feedback if retrying
                if feedback is None:
                    prompt = EXTRACTION_PROMPT.format(
                        existing_map_summary=existing_summary,
                        new_messages=formatted_messages,
                    )
                else:
                    prompt = f"""Previous extraction failed:

```
{feedback}
```

Your previous extraction was:

```
{previous_response}
```

Please extract again, addressing these issues.

{EXTRACTION_PROMPT.format(
    existing_map_summary=existing_summary,
    new_messages=formatted_messages,
)}"""

                # Call LLM
                response = call(
                    context=[{"role": "user", "content": prompt}],
                    model_str=self._get_model_str(),
                    extra_args={"response_format": {"type": "json_object"}},
                )

                # Parse response - can raise ExtractionParseError
                proposed_nodes, proposed_edges = parse_extraction_response(
                    response.message.content
                )

                # Validate
                validation_prompt = VALIDATION_PROMPT.format(
                    existing_map_summary=existing_summary,
                    new_messages=formatted_messages,
                    proposed_extraction=format_extraction_for_display(
                        proposed_nodes, proposed_edges
                    ),
                )

                validation_response = call(
                    context=[{"role": "user", "content": validation_prompt}],
                    model_str=self._get_model_str(),
                )

                is_valid, validation_feedback = parse_validation_response(
                    validation_response.message.content
                )

                # Convert to objects and check for skipped edges
                nodes, edges, skipped_edges = self._convert_to_objects(
                    proposed_nodes, proposed_edges, start_turn, existing_map
                )

                # Build combined feedback from skipped edges and validation
                skipped_feedback = ""
                if skipped_edges:
                    skipped_refs = [
                        f"{e.get('from')} -> {e.get('to')}" for e in skipped_edges
                    ]
                    skipped_feedback = (
                        f"Some edges referenced non-existent nodes: {skipped_refs}. "
                        "Ensure all edge references point to valid node indices or existing node IDs. "
                    )

                if is_valid and not skipped_edges:
                    return True, nodes, edges, validation_feedback
                else:
                    # Combine feedback for retry
                    combined_feedback = ""
                    if skipped_feedback:
                        combined_feedback += skipped_feedback
                    if not is_valid and validation_feedback:
                        combined_feedback += validation_feedback
                    feedback = combined_feedback or "Validation failed"
                    previous_response = response.message.content
                    user_console.print(
                        "[yellow] session map extraction failed[/yellow]\n\n"
                        + f"## response\n\n```\n{previous_response}\n```\n\n"
                        + f"## feedback\n\n{feedback}"
                    )

            except ExtractionParseError as e:
                # Parsing failed - treat like validation failure
                feedback = str(e)
                previous_response = response.message.content

                user_console.print(
                    "[yellow] session map extraction failed[/yellow]\n\n"
                    + f"## response\n\n```\n{previous_response}\n```\n\n"
                    + f"## feedback\n\n{feedback}"
                )

        # All retries exhausted - return gracefully
        return False, [], [], feedback or "Extraction failed after retries"

    def _convert_to_objects(
        self,
        proposed_nodes: list[dict],
        proposed_edges: list[dict],
        start_turn: int,
        existing_map: SessionMap | None = None,
    ) -> tuple[list[Node], list[Edge], list[dict]]:
        """Convert parsed dicts to Node and Edge objects.

        Returns:
            Tuple of (nodes, edges, skipped_edges) where skipped_edges contains
            edges that referenced non-existent node IDs.
        """
        nodes = []
        node_id_map = {}

        # Get existing node IDs
        existing_node_ids = set()
        if existing_map:
            existing_node_ids = {n.id for n in existing_map.nodes}

        for i, node_data in enumerate(proposed_nodes):
            try:
                node_type = NodeType(node_data.get("type", "finding"))
            except ValueError:
                raise ExtractionParseError(
                    f"Invalid node type: {node_data.get('type')}"
                )
            content = node_data.get("content", "")

            if not content:
                continue

            node = Node(
                id=generate_node_id(),
                type=node_type,
                content=content,
                created_at=datetime.now(),
                turn=start_turn + node_data.get("turn", i),
                source_msg_idx=node_data.get("source_msg_idx"),
                connects_to=node_data.get("connects_to", []),
            )

            node_id_map[str(i)] = node.id
            nodes.append(node)

        edges = []
        skipped_edges = []
        node_ids = set(node_id_map.values())

        for edge_data in proposed_edges:
            from_ref = edge_data.get("from", "")
            to_ref = edge_data.get("to", "")
            try:
                edge_type = EdgeType(edge_data.get("type", "related"))
            except ValueError:
                raise ExtractionParseError(
                    f"Invalid edge type: {edge_data.get('type')}"
                )

            from_id = node_id_map.get(str(from_ref), from_ref)
            to_id = node_id_map.get(str(to_ref), to_ref)

            # Check if both endpoints exist (either newly created or referenced by ID)
            from_exists = (
                from_id in node_ids
                or from_id in existing_node_ids
                or str(from_ref) in node_id_map
            )
            to_exists = (
                to_id in node_ids
                or to_id in existing_node_ids
                or str(to_ref) in node_id_map
            )

            if not from_id or not to_id or not from_exists or not to_exists:
                # FIXME: warnings should be printed using a rich console, or using the user_console object, not logger
                # logging.warning(
                #     f"Skipping edge with unresolved reference: {from_ref} -> {to_ref} "
                #     f"(type: {edge_data.get('type')})"
                # )
                skipped_edges.append(edge_data)
                continue

            edges.append(Edge(from_id=from_id, to_id=to_id, type=edge_type))

        return nodes, edges, skipped_edges
