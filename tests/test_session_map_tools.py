"""
Test cases for ReadSessionMapTool.

Tests cover parameters model validation, tool execution, and edge cases.
"""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from wichy.session_map.store import SessionMapStore
from wichy.session_map.models import (
    Node,
    Edge,
    NodeType,
    EdgeType,
    SessionMap,
)
from wichy.tools.session_map_tools import (
    ReadSessionMapTool,
    ReadSessionMapParameters,
    set_session_map_globals,
)

# --- Fixtures ---


@pytest.fixture
def temp_db_path():
    """Create a temporary database path for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_session_maps.db"
        yield db_path


@pytest.fixture
def store(temp_db_path):
    """Create a fresh SessionMapStore instance for testing."""
    SessionMapStore._instances.pop(temp_db_path, None)
    return SessionMapStore(db_path=temp_db_path)


@pytest.fixture
def mock_context_handler():
    """Create a mock context handler for testing."""
    mock_context = MagicMock()
    mock_context.path = "test_context_123"
    return mock_context


@pytest.fixture
def tool():
    """Create a ReadSessionMapTool instance for testing."""
    return ReadSessionMapTool()


@pytest.fixture
def sample_nodes():
    """Create a list of sample nodes for testing."""
    return [
        Node(
            id="node_q1",
            type=NodeType.QUESTION,
            content="What is the root cause of the error?",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            turn=1,
        ),
        Node(
            id="node_f1",
            type=NodeType.FINDING,
            content="Found a syntax error in the config file on line 42",
            created_at=datetime(2024, 1, 1, 12, 30, 0),
            turn=2,
            source_msg_idx=5,
        ),
        Node(
            id="node_d1",
            type=NodeType.DECISION,
            content="Use Redis for session storage instead of in-memory",
            created_at=datetime(2024, 1, 1, 13, 0, 0),
            turn=3,
        ),
        Node(
            id="node_file1",
            type=NodeType.FILE,
            content="config.yaml - main configuration file",
            created_at=datetime(2024, 1, 1, 13, 30, 0),
            turn=4,
        ),
        Node(
            id="node_dead1",
            type=NodeType.DEAD_END,
            content="The logging library does not support this feature",
            created_at=datetime(2024, 1, 1, 14, 0, 0),
            turn=5,
        ),
        Node(
            id="node_note1",
            type=NodeType.NOTE,
            content="Remember to check environment variables",
            created_at=datetime(2024, 1, 1, 14, 30, 0),
            turn=6,
        ),
    ]


@pytest.fixture
def sample_edges():
    """Create a list of sample edges for testing."""
    return [
        Edge(from_id="node_q1", to_id="node_f1", type=EdgeType.ANSWERED_BY),
        Edge(from_id="node_f1", to_id="node_d1", type=EdgeType.LED_TO),
        Edge(from_id="node_file1", to_id="node_f1", type=EdgeType.EXPLORED),
    ]


@pytest.fixture
def sample_session_map(sample_nodes, sample_edges):
    """Create a sample SessionMap for testing."""
    return SessionMap(
        context_id="test_context_123",
        nodes=sample_nodes,
        edges=sample_edges,
        last_extracted_turn=6,
        updated_at=datetime(2024, 1, 1, 15, 0, 0),
    )


@pytest.fixture
def empty_session_map(mock_context_handler):
    """Create an empty SessionMap for testing with matching context ID."""
    return SessionMap(
        context_id=str(mock_context_handler.path),
        nodes=[],
        edges=[],
        last_extracted_turn=0,
        updated_at=datetime(2024, 1, 1, 12, 0, 0),
    )


@pytest.fixture
def many_nodes():
    """Create many nodes for testing limit behavior."""
    nodes = []
    for i in range(150):
        nodes.append(
            Node(
                id=f"node_{i}",
                type=NodeType.FINDING,
                content=f"Finding number {i} with some content",
                created_at=datetime(2024, 1, 1, i % 24, i % 60, 0),
                turn=i,
            )
        )
    return nodes


# --- Parameters Model Tests ---


class TestReadSessionMapParametersDefaults:
    """Test default parameter values."""

    def test_default_node_types_is_none(self):
        """node_types should default to None."""
        params = ReadSessionMapParameters()
        assert params.node_types is None

    def test_default_detail_is_quick(self):
        """detail should default to 'quick'."""
        params = ReadSessionMapParameters()
        assert params.detail == "quick"

    def test_default_limit_is_100(self):
        """limit should default to 100."""
        params = ReadSessionMapParameters()
        assert params.limit == 100


class TestReadSessionMapParametersCustomValues:
    """Test custom parameter values work correctly."""

    def test_custom_node_types(self):
        """Custom node_types should be accepted."""
        params = ReadSessionMapParameters(node_types=["question", "finding"])
        assert params.node_types == ["question", "finding"]

    def test_custom_detail_full(self):
        """Custom detail 'full' should be accepted."""
        params = ReadSessionMapParameters(detail="full")
        assert params.detail == "full"

    def test_custom_limit(self):
        """Custom limit values should be accepted."""
        params = ReadSessionMapParameters(limit=50)
        assert params.limit == 50

    def test_limit_minimum_value(self):
        """Limit of 1 should be valid."""
        params = ReadSessionMapParameters(limit=1)
        assert params.limit == 1

    def test_limit_maximum_value(self):
        """Limit of 500 should be valid."""
        params = ReadSessionMapParameters(limit=500)
        assert params.limit == 500


class TestReadSessionMapParametersValidation:
    """Test parameter validation rules."""

    def test_invalid_detail_raises_validation_error(self):
        """Invalid detail value should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ReadSessionMapParameters(detail="invalid")
        assert "detail" in str(exc_info.value)

    def test_limit_zero_raises_validation_error(self):
        """Limit < 1 should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ReadSessionMapParameters(limit=0)
        assert "limit" in str(exc_info.value)

    def test_limit_negative_raises_validation_error(self):
        """Negative limit should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ReadSessionMapParameters(limit=-5)
        assert "limit" in str(exc_info.value)

    def test_limit_over_500_raises_validation_error(self):
        """Limit > 500 should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            ReadSessionMapParameters(limit=501)
        assert "limit" in str(exc_info.value)

    def test_limit_501_raises_validation_error(self):
        """Limit of 501 should be rejected."""
        with pytest.raises(ValidationError):
            ReadSessionMapParameters(limit=501)


class TestReadSessionMapParametersInfo:
    """Test the info() method output."""

    def test_info_with_defaults(self):
        """info() should show default values."""
        params = ReadSessionMapParameters()
        info = params.info()
        assert "detail=quick" in info
        assert "limit=100" in info
        assert "types" not in info  # node_types is None, not included

    def test_info_with_node_types(self):
        """info() should include node_types when provided."""
        params = ReadSessionMapParameters(node_types=["question", "finding"])
        info = params.info()
        assert "types=['question', 'finding']" in info
        assert "detail=quick" in info
        assert "limit=100" in info

    def test_info_with_all_custom_values(self):
        """info() should show all custom values."""
        params = ReadSessionMapParameters(
            node_types=["decision"], detail="full", limit=50
        )
        info = params.info()
        assert "types=['decision']" in info
        assert "detail=full" in info
        assert "limit=50" in info


# --- Tool Execution Tests ---


class TestReadSessionMapToolUninitializedGlobals:
    """Test behavior when globals are not initialized."""

    def test_uninitialized_store_returns_error(self, tool):
        """Uninitialized session map store should return proper error."""
        # Ensure globals are None
        set_session_map_globals(None, None)

        result = tool.execute()

        assert result.startswith("error:")
        assert "not initialized" in result.lower()

    def test_uninitialized_context_handler_returns_error(self, tool, store):
        """Uninitialized context handler should return proper error."""
        set_session_map_globals(store, None)

        result = tool.execute()

        assert result.startswith("error:")
        assert "not initialized" in result.lower()

    def test_both_globals_none_returns_error(self, tool):
        """Both globals being None should return error."""
        set_session_map_globals(None, None)

        result = tool.execute()

        assert result.startswith("error:")


class TestReadSessionMapToolEmptySessionMap:
    """Test behavior with empty session map."""

    def test_empty_session_map_returns_friendly_message(
        self, tool, store, mock_context_handler, empty_session_map
    ):
        """Empty session map should return friendly message."""
        set_session_map_globals(store, mock_context_handler)

        # Store empty session map
        store.save(empty_session_map)

        result = tool.execute()

        # Should show empty state
        assert "Session Map" in result
        assert "Total nodes: 0" in result


class TestReadSessionMapToolQuickMode:
    """Test quick mode output format."""

    def test_quick_mode_shows_summary_header(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Quick mode should show summary header."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="quick")

        assert "# Session Map Summary" in result

    def test_quick_mode_shows_total_counts(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Quick mode should show total node and edge counts."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="quick")

        assert "Total nodes:" in result
        assert "Total edges:" in result

    def test_quick_mode_shows_node_type_counts(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Quick mode should show counts by node type."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="quick")

        assert "## Node Counts by Type" in result
        assert "- question:" in result
        assert "- finding:" in result
        assert "- decision:" in result
        assert "- file:" in result
        assert "- dead_end:" in result
        assert "- note:" in result

    def test_quick_mode_shows_filtered_nodes(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Quick mode should list filtered nodes with truncated content."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="quick")

        # Should show nodes with truncated content (80 chars max)
        assert "## Nodes" in result
        assert "[node_q1]" in result
        assert "[node_f1]" in result

    def test_quick_mode_truncates_long_content(self, tool, store, mock_context_handler):
        """Quick mode should truncate content longer than 80 characters."""
        set_session_map_globals(store, mock_context_handler)

        # Create a session map with long content
        long_content = "This is a very long content that should be truncated because it exceeds eighty characters"
        node = Node(
            id="long_node",
            type=NodeType.FINDING,
            content=long_content,
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            turn=1,
        )
        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=[node],
            edges=[],
            last_extracted_turn=1,
        )
        store.save(session_map)

        result = tool.execute(detail="quick")

        assert "..." in result
        assert len(long_content) > 80  # Original content is longer


class TestReadSessionMapToolFullMode:
    """Test full mode output format."""

    def test_full_mode_shows_full_details_header(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show full details header."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        assert "# Session Map (Full Details)" in result

    def test_full_mode_shows_context_id(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show context ID."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        assert "Context:" in result
        assert "test_context_123" in result

    def test_full_mode_shows_complete_node_details(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show complete node details."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        # Should show full content, not truncated
        assert "### [node_q1] question" in result
        assert "What is the root cause of the error?" in result
        assert "Turn:" in result

    def test_full_mode_shows_edges(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show edges between nodes."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        assert "## Edges" in result

    def test_full_mode_shows_edge_type(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show edge types correctly."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        # Edge types should be displayed
        assert "answered_by" in result or "led_to" in result or "explored" in result

    def test_full_mode_shows_source_message_index(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show source message index when present."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        # node_f1 has source_msg_idx=5
        assert "Source message index: 5" in result

    def test_full_mode_shows_connected_nodes(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Full mode should show connected nodes."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(detail="full")

        assert "Connects to:" in result


class TestReadSessionMapToolNodeTypeFilter:
    """Test node_types filter functionality."""

    def test_filter_question_nodes(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Filtering by 'question' should only return question nodes."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["question"])

        assert "[question]" in result
        assert "[finding]" not in result
        assert "[decision]" not in result

    def test_filter_finding_nodes(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Filtering by 'finding' should only return finding nodes."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["finding"])

        assert "[finding]" in result
        assert "[question]" not in result

    def test_filter_multiple_node_types(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Filtering by multiple types should return all matching nodes."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["question", "finding"])

        assert "[question]" in result
        assert "[finding]" in result
        # Other types should not appear in node list
        assert "[decision]" not in result

    def test_filter_case_insensitive(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Node type filter should be case-insensitive."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["QUESTION", "FINDING"])

        assert "[question]" in result
        assert "[finding]" in result

    def test_filter_nonexistent_type_returns_empty(
        self, tool, store, mock_context_handler
    ):
        """Filtering for type with no nodes should return empty filtered list."""
        set_session_map_globals(store, mock_context_handler)

        # Create session map with only questions
        node = Node(
            id="q_only",
            type=NodeType.QUESTION,
            content="Only question here",
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            turn=1,
        )
        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=[node],
            edges=[],
            last_extracted_turn=1,
        )
        store.save(session_map)

        result = tool.execute(node_types=["finding"])

        # Should show 0 findings
        assert "finding: 0" in result


class TestReadSessionMapToolLimit:
    """Test limit parameter functionality."""

    def test_limit_truncates_nodes(self, tool, store, mock_context_handler, many_nodes):
        """Limit should truncate node list."""
        set_session_map_globals(store, mock_context_handler)

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=many_nodes,
            edges=[],
            last_extracted_turn=150,
        )
        store.save(session_map)

        result = tool.execute(limit=10)

        # Should show truncation notice
        assert "Limited to 10 nodes" in result or "Showing 10" in result

    def test_limit_shows_actual_count(
        self, tool, store, mock_context_handler, many_nodes
    ):
        """Output should show actual vs filtered count."""
        set_session_map_globals(store, mock_context_handler)

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=many_nodes,
            edges=[],
            last_extracted_turn=150,
        )
        store.save(session_map)

        result = tool.execute(limit=50)

        # Should indicate truncation
        assert "50" in result

    def test_limit_default_is_100(self, tool, store, mock_context_handler):
        """Default limit of 100 should be respected."""
        set_session_map_globals(store, mock_context_handler)

        # Create exactly 100 nodes
        nodes = [
            Node(
                id=f"node_{i}",
                type=NodeType.FINDING,
                content=f"Finding {i}",
                created_at=datetime(2024, 1, 1, 12, 0, 0),
                turn=i,
            )
            for i in range(100)
        ]
        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=nodes,
            edges=[],
            last_extracted_turn=100,
        )
        store.save(session_map)

        result = tool.execute()  # Uses default limit of 100

        # Should show all 100 nodes without truncation
        assert "Limited to" not in result or "100" in result


class TestReadSessionMapToolInvalidNodeTypes:
    """Test invalid node types handling."""

    def test_invalid_node_type_returns_error(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Invalid node types should return error with valid types listed."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["invalid_type"])

        assert result.startswith("error:")
        assert "Invalid node types" in result
        assert "valid types" in result.lower() or "question" in result

    def test_invalid_node_type_shows_valid_types(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Error should list all valid node types."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["unknown", "invalid"])

        assert "question" in result
        assert "finding" in result
        assert "decision" in result
        assert "file" in result
        assert "dead_end" in result
        assert "note" in result

    def test_mixed_valid_invalid_node_types(
        self, tool, store, mock_context_handler, sample_session_map
    ):
        """Mixed valid/invalid types should return error for invalid ones."""
        set_session_map_globals(store, mock_context_handler)
        store.save(sample_session_map)

        result = tool.execute(node_types=["question", "invalid_type"])

        assert result.startswith("error:")
        assert "Invalid node types" in result
        assert "invalid_type" in result


# --- Edge Cases ---


class TestReadSessionMapToolEdgeCases:
    """Test edge cases."""

    def test_session_map_with_no_edges(self, tool, store, mock_context_handler):
        """Session map with no edges should still display correctly."""
        set_session_map_globals(store, mock_context_handler)

        nodes = [
            Node(
                id="isolated_node",
                type=NodeType.QUESTION,
                content="A question with no edges",
                created_at=datetime(2024, 1, 1, 12, 0, 0),
                turn=1,
            )
        ]
        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=nodes,
            edges=[],
            last_extracted_turn=1,
        )
        store.save(session_map)

        result = tool.execute(detail="full")

        assert "# Session Map" in result
        assert "Total edges: 0" in result
        assert "isolated_node" in result

    def test_all_node_types_recognized(self, tool, store, mock_context_handler):
        """All node types should be recognized and displayed."""
        set_session_map_globals(store, mock_context_handler)

        # Create a node for each type
        node_types = [
            (NodeType.QUESTION, "question"),
            (NodeType.FINDING, "finding"),
            (NodeType.DECISION, "decision"),
            (NodeType.FILE, "file"),
            (NodeType.DEAD_END, "dead_end"),
            (NodeType.NOTE, "note"),
        ]

        nodes = [
            Node(
                id=f"node_{ntype.value}",
                type=ntype,
                content=f"Content for {ntype.value}",
                created_at=datetime(2024, 1, 1, i + 1, 0, 0),
                turn=i + 1,
            )
            for i, (ntype, _) in enumerate(node_types)
        ]

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=nodes,
            edges=[],
            last_extracted_turn=6,
        )
        store.save(session_map)

        result = tool.execute(detail="quick")

        # All node types should appear in type counts
        assert "question: 1" in result
        assert "finding: 1" in result
        assert "decision: 1" in result
        assert "file: 1" in result
        assert "dead_end: 1" in result
        assert "note: 1" in result

    def test_all_edge_types_displayed_correctly(
        self, tool, store, mock_context_handler
    ):
        """All edge types should be displayed correctly in full mode."""
        set_session_map_globals(store, mock_context_handler)

        # Create nodes for edges
        nodes = [
            Node(
                id="n1",
                type=NodeType.QUESTION,
                content="Question",
                created_at=datetime(2024, 1, 1, 1, 0, 0),
                turn=1,
            ),
            Node(
                id="n2",
                type=NodeType.FINDING,
                content="Finding",
                created_at=datetime(2024, 1, 1, 2, 0, 0),
                turn=2,
            ),
            Node(
                id="n3",
                type=NodeType.DECISION,
                content="Decision",
                created_at=datetime(2024, 1, 1, 3, 0, 0),
                turn=3,
            ),
            Node(
                id="n4",
                type=NodeType.FILE,
                content="File",
                created_at=datetime(2024, 1, 1, 4, 0, 0),
                turn=4,
            ),
            Node(
                id="n5",
                type=NodeType.DEAD_END,
                content="Dead End",
                created_at=datetime(2024, 1, 1, 5, 0, 0),
                turn=5,
            ),
            Node(
                id="n6",
                type=NodeType.NOTE,
                content="Note",
                created_at=datetime(2024, 1, 1, 6, 0, 0),
                turn=6,
            ),
        ]

        # Create edges of different types
        edges = [
            Edge(from_id="n1", to_id="n2", type=EdgeType.LED_TO),
            Edge(from_id="n2", to_id="n3", type=EdgeType.ANSWERED_BY),
            Edge(from_id="n1", to_id="n4", type=EdgeType.EXPLORED),
            Edge(from_id="n3", to_id="n5", type=EdgeType.RULED_OUT),
            Edge(from_id="n2", to_id="n6", type=EdgeType.RELATED),
            Edge(from_id="n4", to_id="n5", type=EdgeType.FOLLOWS),
        ]

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=nodes,
            edges=edges,
            last_extracted_turn=6,
        )
        store.save(session_map)

        result = tool.execute(detail="full")

        # All edge types should be mentioned
        assert "led_to" in result
        assert "answered_by" in result
        assert "explored" in result
        assert "ruled_out" in result
        assert "related" in result
        assert "follows" in result

    def test_session_map_with_many_nodes_shows_truncation(
        self, tool, store, mock_context_handler, many_nodes
    ):
        """Many nodes should show truncation notice when limit is applied."""
        set_session_map_globals(store, mock_context_handler)

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=many_nodes,
            edges=[],
            last_extracted_turn=150,
        )
        store.save(session_map)

        result = tool.execute(limit=50)

        # Should indicate truncation
        assert "50" in result  # Limit is 50
        assert "150" in result  # Total nodes is 150

    def test_filter_with_limit_combined(self, tool, store, mock_context_handler):
        """Combining filter and limit should work correctly."""
        set_session_map_globals(store, mock_context_handler)

        # Create nodes of different types
        nodes = []
        for i in range(100):
            node_type = NodeType.QUESTION if i % 2 == 0 else NodeType.FINDING
            nodes.append(
                Node(
                    id=f"node_{i}",
                    type=node_type,
                    content=f"Content {i}",
                    created_at=datetime(2024, 1, 1, i % 24, i % 60, 0),
                    turn=i,
                )
            )

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=nodes,
            edges=[],
            last_extracted_turn=100,
        )
        store.save(session_map)

        result = tool.execute(node_types=["question"], limit=10)

        # Should only show questions
        assert "[question]" in result

    def test_full_mode_with_limit(self, tool, store, mock_context_handler, many_nodes):
        """Full mode with limit should truncate correctly."""
        set_session_map_globals(store, mock_context_handler)

        session_map = SessionMap(
            context_id=str(mock_context_handler.path),
            nodes=many_nodes,
            edges=[],
            last_extracted_turn=150,
        )
        store.save(session_map)

        result = tool.execute(detail="full", limit=20)

        assert "Showing" in result or "nodes" in result


# --- Tool Attributes Tests ---


class TestReadSessionMapToolAttributes:
    """Test tool attributes and class properties."""

    def test_tool_name(self, tool):
        """Tool should have correct name."""
        assert tool.name == "read_session_map"

    def test_tool_description(self, tool):
        """Tool should have description."""
        assert tool.description is not None
        assert len(tool.description) > 0

    def test_tool_description_long(self, tool):
        """Tool should have long description."""
        assert tool.description_long is not None
        assert len(tool.description_long) > 0

    def test_tool_parameters_model(self, tool):
        """Tool should have parameters_model set."""
        assert tool.parameters_model == ReadSessionMapParameters

    def test_valid_node_types_constant(self, tool):
        """Tool should have VALID_NODE_TYPES constant."""
        assert hasattr(tool, "VALID_NODE_TYPES")
        assert "question" in tool.VALID_NODE_TYPES
        assert "finding" in tool.VALID_NODE_TYPES
        assert "decision" in tool.VALID_NODE_TYPES
        assert "file" in tool.VALID_NODE_TYPES
        assert "dead_end" in tool.VALID_NODE_TYPES
        assert "note" in tool.VALID_NODE_TYPES


# --- Parameter Model Tests ---


class TestReadSessionMapParametersModel:
    """Additional tests for ParametersModel behavior."""

    def test_parameters_model_inherits_from_base_model(self):
        """ReadSessionMapParameters should inherit from ParametersModel."""
        from wichy.tools.base import ParametersModel

        assert issubclass(ReadSessionMapParameters, ParametersModel)

    def test_info_returns_string(self):
        """info() method should return a string."""
        params = ReadSessionMapParameters()
        assert isinstance(params.info(), str)

    def test_info_is_not_empty(self):
        """info() method should not return empty string."""
        params = ReadSessionMapParameters()
        # Even with defaults, info should return something meaningful
        info = params.info()
        assert len(info) > 0
