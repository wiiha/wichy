"""
Test cases for the session map feature.
Tests cover models, validation, store, extractor, and API.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from wichy.session_map.extractor import (
    ExtractionParseError,
    format_extraction_for_display,
    format_messages_for_extraction,
    parse_extraction_response,
    parse_validation_response,
)
from wichy.session_map.models import (
    Edge,
    EdgeType,
    Node,
    NodeType,
    SessionMap,
    generate_node_id,
)
from wichy.session_map.store import SessionMapStore
from wichy.session_map.validation import (
    ValidationResult,
    validate_edge_types,
    validate_node_types,
    validate_references,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path():
    """Create a temporary database path for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_session_maps.db"
        yield db_path


@pytest.fixture
def temp_db_path_2():
    """Second temporary database path for singleton tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_session_maps_2.db"
        yield db_path


@pytest.fixture
def store(temp_db_path):
    """Create a fresh SessionMapStore instance for testing."""
    # Clear any existing singleton instances for this path
    SessionMapStore._instances.pop(temp_db_path, None)
    return SessionMapStore(db_path=temp_db_path)


@pytest.fixture
def sample_node():
    """Create a sample Node for testing."""
    return Node(
        id="node_123",
        type=NodeType.QUESTION,
        content="What is the root cause?",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        turn=1,
        source_msg_idx=0,
        connects_to=["node_456"],
    )


@pytest.fixture
def sample_edge():
    """Create a sample Edge for testing."""
    return Edge(
        from_id="node_123",
        to_id="node_456",
        type=EdgeType.LED_TO,
    )


@pytest.fixture
def sample_session_map():
    """Create a sample SessionMap for testing."""
    node1 = Node(
        id="node_1",
        type=NodeType.QUESTION,
        content="What is causing the error?",
        created_at=datetime(2024, 1, 1, 12, 0, 0),
        turn=1,
    )
    node2 = Node(
        id="node_2",
        type=NodeType.FINDING,
        content="Found syntax error in config file",
        created_at=datetime(2024, 1, 1, 12, 30, 0),
        turn=2,
    )
    edge = Edge(from_id="node_1", to_id="node_2", type=EdgeType.LED_TO)

    return SessionMap(
        context_id="test_context_1",
        nodes=[node1, node2],
        edges=[edge],
        last_extracted_turn=2,
        updated_at=datetime(2024, 1, 1, 13, 0, 0),
    )


@pytest.fixture
def fresh_app(temp_db_path):
    """Create a Flask app with actual session map API blueprint for testing.

    This fixture imports and uses the real API from wichy.session_map.api,
    ensuring tests validate the actual implementation rather than duplicating code.
    """
    from flask import Blueprint

    from wichy.session_map.api import (
        register_routes,
        set_context_handler,
        set_session_map_model_str,
        set_session_map_store,
    )

    # Create fresh store
    SessionMapStore._instances.pop(temp_db_path, None)
    store = SessionMapStore(db_path=temp_db_path)

    # Set up the store in the API module
    set_session_map_store(store)

    # Create mock context handler
    mock_context = MagicMock()
    mock_context.path = "test_context"
    mock_context.context = []

    # Set context handler in the API module
    set_context_handler(mock_context)

    # Set a model string for extraction (simulating --session-map being enabled)
    set_session_map_model_str("test/model")

    # Create Flask app
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Create a fresh blueprint for this test (Blueprint must be fresh, not reused)
    bp = Blueprint("session_map", __name__, url_prefix="/tools/session-map")

    # Register routes on the fresh blueprint using the same route handlers
    register_routes(bp)
    app.register_blueprint(bp)

    yield app, store, mock_context, temp_db_path

    # Cleanup: reset the API module globals for next test
    set_session_map_store(None)
    set_context_handler(None)
    set_session_map_model_str(None)


# =============================================================================
# Models Tests
# =============================================================================


class TestNodeType:
    """Tests for NodeType enum."""

    def test_node_type_values(self):
        """Test that NodeType has expected values."""
        assert NodeType.QUESTION.value == "question"
        assert NodeType.FINDING.value == "finding"
        assert NodeType.DECISION.value == "decision"
        assert NodeType.FILE.value == "file"
        assert NodeType.DEAD_END.value == "dead_end"
        assert NodeType.NOTE.value == "note"


class TestEdgeType:
    """Tests for EdgeType enum."""

    def test_edge_type_values(self):
        """Test that EdgeType has expected values."""
        assert EdgeType.LED_TO.value == "led_to"
        assert EdgeType.ANSWERED_BY.value == "answered_by"
        assert EdgeType.EXPLORED.value == "explored"
        assert EdgeType.RULED_OUT.value == "ruled_out"
        assert EdgeType.RELATED.value == "related"
        assert EdgeType.FOLLOWS.value == "follows"


class TestNode:
    """Tests for Node dataclass."""

    def test_node_creation(self, sample_node):
        """Test Node creation with all fields."""
        assert sample_node.id == "node_123"
        assert sample_node.type == NodeType.QUESTION
        assert sample_node.content == "What is the root cause?"
        assert sample_node.turn == 1
        assert sample_node.source_msg_idx == 0
        assert sample_node.connects_to == ["node_456"]

    def test_node_to_dict(self, sample_node):
        """Test Node serialization to dict."""
        result = sample_node.to_dict()

        assert result["id"] == "node_123"
        assert result["type"] == "question"
        assert result["content"] == "What is the root cause?"
        assert result["turn"] == 1
        assert result["source_msg_idx"] == 0
        assert result["connects_to"] == ["node_456"]
        assert "created_at" in result

    def test_node_from_dict(self):
        """Test Node deserialization from dict."""
        data = {
            "id": "node_abc",
            "type": "finding",
            "content": "Discovered bug in parser",
            "created_at": "2024-01-15T10:30:00",
            "turn": 5,
            "source_msg_idx": 10,
            "connects_to": ["node_xyz"],
        }

        node = Node.from_dict(data)

        assert node.id == "node_abc"
        assert node.type == NodeType.FINDING
        assert node.content == "Discovered bug in parser"
        assert node.turn == 5
        assert node.source_msg_idx == 10
        assert node.connects_to == ["node_xyz"]

    def test_node_from_dict_defaults(self):
        """Test Node from_dict with missing optional fields."""
        data = {
            "id": "node_123",
            "type": "decision",
            "content": "Refactor the module",
            "created_at": "2024-01-15T10:30:00",
            "turn": 3,
        }

        node = Node.from_dict(data)

        assert node.source_msg_idx is None
        assert node.connects_to == []

    def test_node_roundtrip(self, sample_node):
        """Test that to_dict and from_dict are inverse operations."""
        data = sample_node.to_dict()
        restored = Node.from_dict(data)

        assert restored.id == sample_node.id
        assert restored.type == sample_node.type
        assert restored.content == sample_node.content
        assert restored.turn == sample_node.turn
        assert restored.source_msg_idx == sample_node.source_msg_idx
        assert restored.connects_to == sample_node.connects_to


class TestEdge:
    """Tests for Edge dataclass."""

    def test_edge_creation(self, sample_edge):
        """Test Edge creation."""
        assert sample_edge.from_id == "node_123"
        assert sample_edge.to_id == "node_456"
        assert sample_edge.type == EdgeType.LED_TO

    def test_edge_to_dict(self, sample_edge):
        """Test Edge serialization to dict."""
        result = sample_edge.to_dict()

        assert result["from"] == "node_123"
        assert result["to"] == "node_456"
        assert result["type"] == "led_to"

    def test_edge_from_dict(self):
        """Test Edge deserialization from dict."""
        data = {
            "from": "node_a",
            "to": "node_b",
            "type": "related",
        }

        edge = Edge.from_dict(data)

        assert edge.from_id == "node_a"
        assert edge.to_id == "node_b"
        assert edge.type == EdgeType.RELATED

    def test_edge_roundtrip(self, sample_edge):
        """Test that to_dict and from_dict are inverse operations."""
        data = sample_edge.to_dict()
        restored = Edge.from_dict(data)

        assert restored.from_id == sample_edge.from_id
        assert restored.to_id == sample_edge.to_id
        assert restored.type == sample_edge.type


class TestSessionMap:
    """Tests for SessionMap dataclass."""

    def test_session_map_creation(self, sample_session_map):
        """Test SessionMap creation."""
        assert sample_session_map.context_id == "test_context_1"
        assert len(sample_session_map.nodes) == 2
        assert len(sample_session_map.edges) == 1
        assert sample_session_map.last_extracted_turn == 2

    def test_session_map_to_dict(self, sample_session_map):
        """Test SessionMap serialization to dict."""
        result = sample_session_map.to_dict()

        assert result["context_id"] == "test_context_1"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["last_extracted_turn"] == 2
        assert "updated_at" in result

    def test_session_map_to_json(self, sample_session_map):
        """Test SessionMap serialization to JSON."""
        json_str = sample_session_map.to_json()

        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data["context_id"] == "test_context_1"

    def test_session_map_from_dict(self):
        """Test SessionMap deserialization from dict."""
        data = {
            "context_id": "ctx_123",
            "nodes": [
                {
                    "id": "n1",
                    "type": "question",
                    "content": "Test question",
                    "created_at": "2024-01-01T00:00:00",
                    "turn": 1,
                }
            ],
            "edges": [
                {
                    "from": "n1",
                    "to": "n2",
                    "type": "led_to",
                }
            ],
            "last_extracted_turn": 5,
            "updated_at": "2024-01-01T12:00:00",
        }

        session_map = SessionMap.from_dict(data)

        assert session_map.context_id == "ctx_123"
        assert len(session_map.nodes) == 1
        assert len(session_map.edges) == 1
        assert session_map.last_extracted_turn == 5

    def test_session_map_from_json(self):
        """Test SessionMap deserialization from JSON."""
        json_str = json.dumps(
            {
                "context_id": "ctx_456",
                "nodes": [],
                "edges": [],
                "last_extracted_turn": 0,
                "updated_at": "2024-01-01T12:00:00",
            }
        )

        session_map = SessionMap.from_json(json_str)

        assert session_map.context_id == "ctx_456"
        assert session_map.nodes == []
        assert session_map.edges == []

    def test_session_map_roundtrip(self, sample_session_map):
        """Test that to_json and from_json are inverse operations."""
        json_str = sample_session_map.to_json()
        restored = SessionMap.from_json(json_str)

        assert restored.context_id == sample_session_map.context_id
        assert len(restored.nodes) == len(sample_session_map.nodes)
        assert len(restored.edges) == len(sample_session_map.edges)

    def test_session_map_get_summary_empty(self):
        """Test get_summary with empty map."""
        session_map = SessionMap(context_id="empty_context")
        summary = session_map.get_summary()

        assert "Empty session map" in summary

    def test_session_map_get_summary_with_nodes(self, sample_session_map):
        """Test get_summary with nodes."""
        summary = sample_session_map.get_summary()

        assert "QUESTION:" in summary
        assert "FINDING:" in summary
        assert "node_1" in summary
        assert "node_2" in summary

    def test_session_map_get_summary_max_nodes(self):
        """Test get_summary respects max_nodes parameter."""
        nodes = [
            Node(
                id=f"node_{i}",
                type=NodeType.FINDING,
                content=f"Finding {i}",
                created_at=datetime(2024, 1, 1, i, 0, 0),
                turn=i,
            )
            for i in range(10)
        ]
        session_map = SessionMap(context_id="test", nodes=nodes)
        summary = session_map.get_summary(max_nodes=3)

        # Should only include first 3 nodes of FINDING type
        assert "node_0" in summary
        assert "node_1" in summary
        assert "node_2" in summary
        assert "node_3" not in summary

    def test_session_map_get_summary_content_truncation(self):
        """Test get_summary truncates long content."""
        long_content = "x" * 200
        node = Node(
            id="node_1",
            type=NodeType.FINDING,
            content=long_content,
            created_at=datetime(2024, 1, 1, 0, 0, 0),
            turn=1,
        )
        session_map = SessionMap(context_id="test", nodes=[node])
        summary = session_map.get_summary(max_content_len=50)

        # Should truncate to 50 chars + "..."
        assert len([line for line in summary.split("\n") if "node_1" in line][0]) < 80

    def test_session_map_defaults(self):
        """Test SessionMap default values."""
        session_map = SessionMap(context_id="test")

        assert session_map.nodes == []
        assert session_map.edges == []
        assert session_map.last_extracted_turn == 0
        assert isinstance(session_map.updated_at, datetime)


class TestGenerateNodeId:
    """Tests for generate_node_id function."""

    def test_generate_node_id_format(self):
        """Test that generated ID has correct format."""
        node_id = generate_node_id()

        assert node_id.startswith("node_")
        assert len(node_id) == 13  # "node_" + 8 hex chars

    def test_generate_node_id_uniqueness(self):
        """Test that generated IDs are unique."""
        ids = {generate_node_id() for _ in range(100)}

        assert len(ids) == 100  # All unique


# =============================================================================
# Validation Tests
# =============================================================================


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_validation_result_creation(self):
        """Test ValidationResult creation."""
        result = ValidationResult(
            is_valid=True,
            nodes=[{"type": "question", "content": "Test"}],
            edges=[],
            feedback="Valid extraction",
        )

        assert result.is_valid is True
        assert len(result.nodes) == 1
        assert result.edges == []
        assert result.feedback == "Valid extraction"

    def test_validation_result_without_feedback(self):
        """Test ValidationResult without optional feedback."""
        result = ValidationResult(
            is_valid=False,
            nodes=[],
            edges=[],
        )

        assert result.feedback is None


class TestValidateNodeTypes:
    """Tests for validate_node_types function."""

    def test_validate_node_types_valid(self):
        """Test validation with all valid node types."""
        valid_types = {"question", "finding", "decision", "file", "dead_end", "note"}
        nodes = [
            {"type": "question", "content": "Test question"},
            {"type": "finding", "content": "Test finding"},
            {"type": "decision", "content": "Test decision"},
        ]

        issues = validate_node_types(nodes, valid_types)

        assert issues == []

    def test_validate_node_types_invalid(self):
        """Test validation with invalid node types."""
        valid_types = {"question", "finding", "decision"}
        nodes = [
            {"type": "question", "content": "Test"},
            {"type": "invalid_type", "content": "Invalid"},
            {"type": "finding", "content": "Test"},
        ]

        issues = validate_node_types(nodes, valid_types)

        assert len(issues) == 1
        assert "invalid_type" in issues[0]

    def test_validate_node_types_missing_type(self):
        """Test validation with missing type field."""
        valid_types = {"question", "finding"}
        nodes = [
            {"content": "No type"},
        ]

        issues = validate_node_types(nodes, valid_types)

        assert len(issues) == 1
        assert "missing" in issues[0].lower()

    def test_validate_node_types_empty_list(self):
        """Test validation with empty node list."""
        valid_types = {"question", "finding"}
        nodes = []

        issues = validate_node_types(nodes, valid_types)

        assert issues == []


class TestValidateEdgeTypes:
    """Tests for validate_edge_types function."""

    def test_validate_edge_types_valid(self):
        """Test validation with all valid edge types."""
        valid_types = {
            "led_to",
            "answered_by",
            "explored",
            "ruled_out",
            "related",
            "follows",
        }
        edges = [
            {"from": "n1", "to": "n2", "type": "led_to"},
            {"from": "n2", "to": "n3", "type": "related"},
        ]

        issues = validate_edge_types(edges, valid_types)

        assert issues == []

    def test_validate_edge_types_invalid(self):
        """Test validation with invalid edge types."""
        valid_types = {"led_to", "related"}
        edges = [
            {"from": "n1", "to": "n2", "type": "led_to"},
            {"from": "n2", "to": "n3", "type": "invalid_edge"},
        ]

        issues = validate_edge_types(edges, valid_types)

        assert len(issues) == 1
        assert "invalid_edge" in issues[0]

    def test_validate_edge_types_missing_type(self):
        """Test validation with missing type field."""
        valid_types = {"led_to"}
        edges = [
            {"from": "n1", "to": "n2"},
        ]

        issues = validate_edge_types(edges, valid_types)

        assert len(issues) == 1
        assert "missing" in issues[0].lower()


class TestValidateReferences:
    """Tests for validate_references function."""

    def test_validate_references_valid(self):
        """Test validation with all valid references."""
        node_ids = {"node_1", "node_2", "node_3"}
        edges = [
            {"from": "node_1", "to": "node_2"},
            {"from": "node_2", "to": "node_3"},
        ]

        issues = validate_references(edges, node_ids)

        assert issues == []

    def test_validate_references_invalid_from(self):
        """Test validation with invalid from reference."""
        node_ids = {"node_1", "node_2"}
        edges = [
            {"from": "nonexistent", "to": "node_1"},
        ]

        issues = validate_references(edges, node_ids)

        assert len(issues) == 1
        assert "nonexistent" in issues[0]
        assert "'from'" in issues[0]

    def test_validate_references_invalid_to(self):
        """Test validation with invalid to reference."""
        node_ids = {"node_1", "node_2"}
        edges = [
            {"from": "node_1", "to": "nonexistent"},
        ]

        issues = validate_references(edges, node_ids)

        assert len(issues) == 1
        assert "nonexistent" in issues[0]
        assert "'to'" in issues[0]

    def test_validate_references_both_invalid(self):
        """Test validation with both references invalid."""
        node_ids = {"node_1"}
        edges = [
            {"from": "bad_from", "to": "bad_to"},
        ]

        issues = validate_references(edges, node_ids)

        assert len(issues) == 2

    def test_validate_references_missing_fields(self):
        """Test validation with missing from/to fields."""
        node_ids = {"node_1"}
        edges = [
            {"type": "led_to"},  # No from or to
        ]

        issues = validate_references(edges, node_ids)

        # Should not raise errors for missing None fields
        assert issues == []


# =============================================================================
# Store Tests
# =============================================================================


class TestSessionMapStore:
    """Tests for SessionMapStore class."""

    def test_store_creation(self, store, temp_db_path):
        """Test that store is created properly."""
        assert store is not None
        assert store._db_path == temp_db_path

    def test_save_and_get(self, store, sample_session_map):
        """Test saving and retrieving a session map."""
        store.save(sample_session_map)

        retrieved = store.get(sample_session_map.context_id)

        assert retrieved is not None
        assert retrieved.context_id == sample_session_map.context_id
        assert len(retrieved.nodes) == 2
        assert len(retrieved.edges) == 1

    def test_get_nonexistent(self, store):
        """Test getting a nonexistent session map."""
        result = store.get("nonexistent_context")

        assert result is None

    def test_save_overwrites(self, store):
        """Test that save overwrites existing map."""
        session_map = SessionMap(
            context_id="test_context",
            nodes=[
                Node(
                    id="n1",
                    type=NodeType.QUESTION,
                    content="Q1",
                    created_at=datetime.now(),
                    turn=1,
                )
            ],
            edges=[],
            last_extracted_turn=1,
        )
        store.save(session_map)

        # Update map
        session_map.nodes.append(
            Node(
                id="n2",
                type=NodeType.FINDING,
                content="F1",
                created_at=datetime.now(),
                turn=2,
            )
        )
        session_map.last_extracted_turn = 2
        store.save(session_map)

        retrieved = store.get("test_context")
        assert len(retrieved.nodes) == 2
        assert retrieved.last_extracted_turn == 2

    def test_merge_nodes_new_map(self, store):
        """Test merging nodes into a new (nonexistent) map."""
        new_nodes = [
            Node(
                id="new_1",
                type=NodeType.QUESTION,
                content="New question",
                created_at=datetime.now(),
                turn=1,
            )
        ]
        new_edges = [Edge(from_id="new_1", to_id="new_2", type=EdgeType.LED_TO)]

        store.merge_nodes("new_context", new_nodes, new_edges, turn=1)

        retrieved = store.get("new_context")
        assert retrieved is not None
        assert len(retrieved.nodes) == 1
        assert len(retrieved.edges) == 1

    def test_merge_nodes_existing_map(self, store, sample_session_map):
        """Test merging nodes into an existing map."""
        store.save(sample_session_map)

        new_nodes = [
            Node(
                id="node_3",
                type=NodeType.DECISION,
                content="Decided to refactor",
                created_at=datetime.now(),
                turn=3,
            )
        ]
        new_edges = [Edge(from_id="node_2", to_id="node_3", type=EdgeType.RELATED)]

        store.merge_nodes(sample_session_map.context_id, new_nodes, new_edges, turn=3)

        retrieved = store.get(sample_session_map.context_id)
        assert len(retrieved.nodes) == 3
        assert len(retrieved.edges) == 2

    def test_merge_nodes_avoids_duplicates(self, store, sample_session_map):
        """Test that merge_nodes doesn't add duplicate nodes."""
        store.save(sample_session_map)

        # Try to add the same node again
        duplicate_node = Node(
            id="node_1",
            type=NodeType.QUESTION,
            content="Duplicate question",
            created_at=datetime.now(),
            turn=1,
        )

        store.merge_nodes(sample_session_map.context_id, [duplicate_node], [], turn=2)

        retrieved = store.get(sample_session_map.context_id)
        assert len(retrieved.nodes) == 2  # Still 2, not 3

    def test_get_last_turn(self, store, sample_session_map):
        """Test getting last extracted turn."""
        sample_session_map.last_extracted_turn = 5
        store.save(sample_session_map)

        result = store.get_last_turn(sample_session_map.context_id)

        assert result == 5

    def test_get_last_turn_nonexistent(self, store):
        """Test getting last turn for nonexistent context."""
        result = store.get_last_turn("nonexistent")

        assert result == 0

    def test_set_last_turn(self, store, sample_session_map):
        """Test setting last extracted turn."""
        store.save(sample_session_map)

        store.set_last_turn(sample_session_map.context_id, 10)

        result = store.get_last_turn(sample_session_map.context_id)
        assert result == 10

    def test_delete_node(self, store, sample_session_map):
        """Test deleting a node."""
        store.save(sample_session_map)

        result = store.delete_node(sample_session_map.context_id, "node_1")

        assert result is True
        retrieved = store.get(sample_session_map.context_id)
        assert len(retrieved.nodes) == 1
        assert retrieved.nodes[0].id == "node_2"

    def test_delete_node_removes_edges(self, store, sample_session_map):
        """Test that deleting a node also removes related edges."""
        store.save(sample_session_map)

        # Delete node_2 which has an edge to it
        store.delete_node(sample_session_map.context_id, "node_2")

        retrieved = store.get(sample_session_map.context_id)
        assert len(retrieved.edges) == 0

    def test_delete_nonexistent_node(self, store, sample_session_map):
        """Test deleting a node that doesn't exist."""
        store.save(sample_session_map)

        result = store.delete_node(sample_session_map.context_id, "nonexistent")

        assert result is False

    def test_delete_node_nonexistent_map(self, store):
        """Test deleting a node from nonexistent map."""
        result = store.delete_node("nonexistent_context", "node_1")

        assert result is False

    def test_clear(self, store, sample_session_map):
        """Test clearing a session map."""
        store.save(sample_session_map)

        store.clear(sample_session_map.context_id)

        result = store.get(sample_session_map.context_id)
        assert result is None

    def test_clear_nonexistent(self, store):
        """Test clearing a nonexistent map (should not raise)."""
        # Should not raise
        store.clear("nonexistent_context")

    def test_singleton_behavior(self, temp_db_path, temp_db_path_2):
        """Test that SessionMapStore is a singleton per db path."""
        # Clear singleton instances first
        SessionMapStore._instances.clear()

        store1 = SessionMapStore(db_path=temp_db_path)
        store2 = SessionMapStore(db_path=temp_db_path)

        assert store1 is store2

        # Different path should create different instance
        store3 = SessionMapStore(db_path=temp_db_path_2)
        assert store1 is not store3


# =============================================================================
# Extractor Tests
# =============================================================================


class TestFormatMessagesForExtraction:
    """Tests for format_messages_for_extraction function."""

    def test_format_messages_basic(self):
        """Test basic message formatting."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        result = format_messages_for_extraction(messages, start_turn=0)

        assert "[Turn 1] USER: Hello" in result
        assert "[Turn 1] ASSISTANT: Hi there!" in result

    def test_format_messages_skips_system(self):
        """Test that system messages are skipped."""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]

        result = format_messages_for_extraction(messages, start_turn=0)

        assert "system" not in result.lower()
        assert "[Turn 1] USER: Question" in result

    def test_format_messages_turn_counting(self):
        """Test that turns are counted correctly."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]

        result = format_messages_for_extraction(messages, start_turn=0)

        assert "[Turn 1]" in result
        assert "[Turn 2]" in result

    def test_format_messages_start_turn(self):
        """Test that start_turn parameter works."""
        messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]

        result = format_messages_for_extraction(messages, start_turn=5)

        assert "[Turn 6]" in result

    def test_format_messages_truncation(self):
        """Test that long messages are truncated."""
        long_content = "x" * 9000  # More than MAX_MESSAGE_LENGTH (8000)
        messages = [
            {"role": "user", "content": long_content},
        ]

        result = format_messages_for_extraction(messages, start_turn=0)

        assert "[truncated]" in result
        # Verify truncation happened - result should be shorter than original
        assert len(result) < len(long_content) + 100  # +100 for formatting


class TestParseExtractionResponse:
    """Tests for parse_extraction_response function."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON response."""
        response = json.dumps(
            {
                "nodes": [
                    {"type": "question", "content": "What is this?", "turn": 1},
                    {"type": "finding", "content": "Found X", "turn": 2},
                ],
                "edges": [{"from": "0", "to": "1", "type": "led_to"}],
            }
        )

        nodes, edges = parse_extraction_response(response)

        assert len(nodes) == 2
        assert len(edges) == 1
        assert nodes[0]["type"] == "question"
        assert edges[0]["type"] == "led_to"

    def test_parse_empty_response(self):
        """Test parsing empty JSON response."""
        response = json.dumps({"nodes": [], "edges": []})

        nodes, edges = parse_extraction_response(response)

        assert nodes == []
        assert edges == []

    def test_parse_malformed_json_with_embedded_json(self):
        """Test parsing malformed response with embedded JSON."""
        response = 'Here is the extraction: {"nodes": [{"type": "finding", "content": "Test"}], "edges": []}'

        nodes, edges = parse_extraction_response(response)

        assert len(nodes) == 1
        assert nodes[0]["type"] == "finding"

    def test_parse_completely_malformed_json(self):
        """Test parsing completely malformed JSON raises ExtractionParseError."""
        response = "This is not JSON at all, just plain text."

        with pytest.raises(ExtractionParseError):
            parse_extraction_response(response)

    def test_parse_missing_fields(self):
        """Test parsing JSON with missing fields."""
        response = json.dumps(
            {
                "nodes": [{"type": "question"}],  # Missing content
            }
        )

        nodes, edges = parse_extraction_response(response)

        assert len(nodes) == 1
        # Missing fields should be handled by caller


class TestParseValidationResponse:
    """Tests for parse_validation_response function."""

    def test_parse_valid_response(self):
        """Test parsing VALID response."""
        response = "VALID: All extracted items are relevant and correct."

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is True
        assert "relevant" in feedback

    def test_parse_valid_response_case_insensitive(self):
        """Test parsing valid response (lowercase)."""
        response = "valid: items look good"

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is True

    def test_parse_invalid_response(self):
        """Test parsing INVALID response."""
        response = "INVALID: Node 2 has wrong type, should be FILE not FINDING."

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is False
        assert "wrong type" in feedback

    def test_parse_invalid_response_case_insensitive(self):
        """Test parsing invalid response (lowercase)."""
        response = "invalid: issues found"

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is False

    def test_parse_ambiguous_response(self):
        """Test parsing ambiguous response."""
        response = "I'm not sure about this extraction."

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is True  # Ambiguous treated as valid
        assert "Ambiguous" in feedback

    def test_parse_valid_without_explanation(self):
        """Test parsing VALID without explanation."""
        response = "VALID"

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is True
        assert "validated successfully" in feedback.lower()

    def test_parse_invalid_without_explanation(self):
        """Test parsing INVALID without explanation."""
        response = "INVALID"

        is_valid, feedback = parse_validation_response(response)

        assert is_valid is False
        assert "failed" in feedback.lower()


class TestFormatExtractionForDisplay:
    """Tests for format_extraction_for_display function."""

    def test_format_extraction_for_display(self):
        """Test formatting extraction for display."""
        nodes = [
            {"type": "question", "content": "What is this?"},
            {"type": "finding", "content": "Found the bug"},
        ]
        edges = [{"from": "node_1", "to": "node_2", "type": "led_to"}]

        result = format_extraction_for_display(nodes, edges)

        assert "### Nodes" in result
        assert "### Edges" in result
        assert "[0]" in result
        assert "led_to" in result

    def test_format_extraction_empty(self):
        """Test formatting empty extraction."""
        result = format_extraction_for_display([], [])

        assert "### Nodes" in result
        assert "### Edges" in result


# =============================================================================
# API Tests
# =============================================================================


class TestSessionMapAPI:
    """Tests for session map API endpoints.

    These tests use the actual API blueprint from wichy.session_map.api,
    ensuring the tests validate the real implementation rather than duplicated code.
    """

    def test_get_map_empty(self, fresh_app):
        """Test GET /api/map returns empty map when not found."""
        app, store, mock_context, _ = fresh_app

        # Set up the mock context for this test
        mock_context.path = "test_context"
        mock_context.context = []

        with app.test_client() as client:
            response = client.get("/tools/session-map/api/map")

        assert response.status_code == 200
        data = response.json
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_get_map_with_data(self, fresh_app, sample_session_map):
        """Test GET /api/map returns map data."""
        app, store, mock_context, _ = fresh_app

        # Save sample map
        store.save(sample_session_map)

        # Set context path to match the sample session map
        mock_context.path = sample_session_map.context_id

        with app.test_client() as client:
            response = client.get("/tools/session-map/api/map")

        assert response.status_code == 200
        data = response.json
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

    def test_get_status(self, fresh_app):
        """Test GET /api/status endpoint."""
        app, store, mock_context, _ = fresh_app

        # Set up context for status test
        mock_context.path = "test_context"
        mock_context.context = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]

        with app.test_client() as client:
            response = client.get("/tools/session-map/api/status")

        assert response.status_code == 200
        data = response.json
        assert "current_turn" in data
        assert "last_extracted_turn" in data
        assert "next_extraction_in" in data
        assert data["enabled"] is True

    def test_add_node(self, fresh_app):
        """Test POST /api/node endpoint."""
        app, store, mock_context, _ = fresh_app

        # Set up context for add node test
        mock_context.path = "test_context"
        mock_context.context = [{"role": "user", "content": "Test"}]

        with app.test_client() as client:
            response = client.post(
                "/tools/session-map/api/node",
                json={"type": "note", "content": "Test note"},
            )

        assert response.status_code == 200
        data = response.json
        assert data["type"] == "note"
        assert data["content"] == "Test note"

    def test_add_node_invalid_type(self, fresh_app):
        """Test POST /api/node with invalid type."""
        app, store, mock_context, _ = fresh_app

        # Set up context
        mock_context.path = "test_context"
        mock_context.context = []

        with app.test_client() as client:
            response = client.post(
                "/tools/session-map/api/node",
                json={"type": "invalid_type", "content": "Test"},
            )

        assert response.status_code == 400
        assert "Invalid node type" in response.json["error"]

    def test_add_node_missing_content(self, fresh_app):
        """Test POST /api/node with missing content."""
        app, store, mock_context, _ = fresh_app

        # Set up context
        mock_context.path = "test_context"
        mock_context.context = []

        with app.test_client() as client:
            response = client.post(
                "/tools/session-map/api/node", json={"type": "note", "content": ""}
            )

        assert response.status_code == 400
        assert "Content is required" in response.json["error"]

    def test_delete_node(self, fresh_app, sample_session_map):
        """Test DELETE /api/node/<node_id> endpoint."""
        app, store, mock_context, _ = fresh_app

        # Save sample map
        store.save(sample_session_map)

        # Set context to match sample map
        mock_context.path = sample_session_map.context_id

        with app.test_client() as client:
            response = client.delete("/tools/session-map/api/node/node_1")

        assert response.status_code == 200
        assert response.json["success"] is True

    def test_delete_node_not_found(self, fresh_app):
        """Test DELETE /api/node/<node_id> with nonexistent node."""
        app, store, mock_context, _ = fresh_app

        # Set up context
        mock_context.path = "test_context"
        mock_context.context = []

        with app.test_client() as client:
            response = client.delete("/tools/session-map/api/node/nonexistent")

        assert response.status_code == 404

    def test_clear_map(self, fresh_app, sample_session_map):
        """Test POST /api/clear endpoint."""
        app, store, mock_context, _ = fresh_app

        # Save sample map
        store.save(sample_session_map)

        # Set context to match sample map
        mock_context.path = sample_session_map.context_id

        with app.test_client() as client:
            response = client.post("/tools/session-map/api/clear")

        assert response.status_code == 200
        assert response.json["success"] is True

        # Verify map is cleared
        result = store.get(sample_session_map.context_id)
        assert result is None

    def test_api_not_initialized(self):
        """Test API endpoints return error when not initialized.

        This test verifies the actual API behavior when store/context
        are not set, using the real API blueprint.
        """
        from flask import Blueprint

        from wichy.session_map.api import register_routes

        app = Flask(__name__)
        app.config["TESTING"] = True

        # Ensure module-level globals are None (uninitialized state)
        import wichy.session_map.api as api_module

        api_module._session_map_store = None
        api_module._context_handler = None
        api_module._session_map_model_str = None

        # Create a fresh blueprint for this test
        bp = Blueprint("session_map", __name__, url_prefix="/tools/session-map")
        register_routes(bp)
        app.register_blueprint(bp)

        with app.test_client() as client:
            response = client.get("/tools/session-map/api/map")

        assert response.status_code == 500
        assert "Not initialized" in response.json["error"]

    def test_trigger_extraction_mocked(self, fresh_app):
        """Test POST /api/extract endpoint with mocked extractor."""
        app, store, mock_context, _ = fresh_app

        # Set up context for extraction test
        mock_context.path = "test_context"
        mock_context.context = [
            {"role": "user", "content": "Test question?"},
            {"role": "assistant", "content": "Test answer."},
        ]

        # Mock extraction result
        mock_extractor = MagicMock()
        mock_extractor.extract_with_validation.return_value = (
            True,
            [
                Node(
                    id="node_1",
                    type=NodeType.QUESTION,
                    content="Test question?",
                    created_at=datetime.now(),
                    turn=1,
                )
            ],
            [],
            "Extraction successful",
        )

        with app.test_client() as client:
            with patch(
                "wichy.session_map.extractor.SessionMapExtractor",
                return_value=mock_extractor,
            ):
                # The blueprint imports SessionMapExtractor inside the route
                # We need to patch where it's used
                import wichy.session_map.extractor as extractor_module

                original_extractor = extractor_module.SessionMapExtractor
                # Use a lambda that accepts model_str argument (can be ignored since we're mocking)
                extractor_module.SessionMapExtractor = (
                    lambda model_str=None: mock_extractor
                )

                try:
                    response = client.post("/tools/session-map/api/extract")
                finally:
                    extractor_module.SessionMapExtractor = original_extractor

        assert response.status_code == 200
        data = response.json
        assert data["success"] is True
        assert data["nodes_added"] == 1
        assert data["edges_added"] == 0
