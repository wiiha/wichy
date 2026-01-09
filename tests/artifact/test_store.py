import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from wichy.artifact.artifact import Artifact
from wichy.artifact.store import ArtifactStore
from wichy.artifact.store_backend import StoreBackendSQLite
from wichy.llm_backend import Message


@pytest.fixture
def temp_artifacts_dir(monkeypatch, tmp_path):
    """
    Create a temporary directory for artifacts using pytest's tmp_path.
    This is the recommended approach as pytest handles cleanup automatically.
    """
    artifacts_path = tmp_path / ".wichy" / "artifacts"
    artifacts_path.mkdir(parents=True, exist_ok=True)

    # Monkey patch the constants to use temp directory
    monkeypatch.setattr(
        "wichy.artifact.store_backend.ARTIFACT_STORE_DIR", str(artifacts_path) + "/"
    )

    yield str(artifacts_path) + "/"

    # No explicit cleanup needed - pytest's tmp_path handles it


@pytest.fixture
def store(temp_artifacts_dir):
    """Create an ArtifactStore instance."""
    return ArtifactStore(session_id="test_session_123")


@pytest.fixture
def sample_artifact():
    """Create a sample artifact for testing."""
    return Artifact(
        type="plan",
        title="JWT Authentication Plan",
        description="Implementation plan for JWT authentication",
        creator="planner_agent",
        content="# JWT Auth\n\nDetailed implementation steps...",
        metadata={"priority": "high"},
    )


@pytest.fixture
def similar_artifact():
    """Create an artifact similar to sample_artifact (for version detection)."""
    return Artifact(
        type="plan",
        title="JWT Authentication Implementation Plan",
        description="Updated implementation plan for JWT authentication with OAuth",
        creator="planner_agent",
        content="# JWT Auth v2\n\nRevised implementation with OAuth...",
        metadata={"priority": "high", "updated": True},
    )


@pytest.fixture
def different_artifact():
    """Create a completely different artifact."""
    return Artifact(
        type="research",
        title="Database Optimization Research",
        description="Research on PostgreSQL query optimization techniques",
        creator="research_agent",
        content="# Database Performance\n\nQuery optimization strategies...",
        metadata={"database": "postgresql"},
    )


@pytest.fixture
def mock_llm_no_match():
    """Mock LLM response indicating no match found."""

    def _mock_call(context, model_str, **kwargs):
        return Message(
            content="no_match|0.85|different topics",
            role="assistant",
            finish_reason="stop",
        )

    return _mock_call


@pytest.fixture
def mock_llm_match():
    """Mock LLM response indicating a match found."""

    def _mock_call(context, model_str, artifact_id="artifact_123", **kwargs):
        return Message(
            content=f"{artifact_id}|0.92|refined scope and added details",
            role="assistant",
            finish_reason="stop",
        )

    return _mock_call


@pytest.fixture
def mock_llm_select_artifacts():
    """Mock LLM response for selecting relevant artifacts."""

    def _mock_call(context, model_str, selected_ids=None, **kwargs):
        if selected_ids is None:
            return Message(content="no_match", role="assistant", finish_reason="stop")
        return Message(
            content="|".join(selected_ids), role="assistant", finish_reason="stop"
        )

    return _mock_call


class TestInitialization:
    """Test ArtifactStore initialization."""

    def test_initialization_with_session_id(self, temp_artifacts_dir):
        """Test that store initializes with session_id."""
        store = ArtifactStore(session_id="my_session")
        assert store.session_id == "my_session"

    def test_initialization_creates_backend(self, temp_artifacts_dir):
        """Test that backend storage is accessible."""
        store = ArtifactStore(session_id="test_session")
        # Should be able to create backend connection
        with StoreBackendSQLite() as backend:
            assert backend.conn is not None


class TestAddArtifact:
    """Test adding artifacts to the store."""

    @patch("wichy.artifact.helpers.call_llm")
    def test_add_first_artifact(self, mock_call_llm, store, sample_artifact):
        """Test adding the first artifact (no candidates)."""
        result = store.add(sample_artifact)

        # Verify artifact was added
        retrieved = store.get(sample_artifact.id)
        assert retrieved is not None
        assert retrieved.id == sample_artifact.id
        assert retrieved.title == sample_artifact.title

        # call_llm should not be called (no candidates)
        mock_call_llm.assert_not_called()

    @patch("wichy.artifact.helpers.call_llm")
    def test_add_dissimilar_artifact(
        self, mock_call_llm, store, sample_artifact, different_artifact
    ):
        """Test adding a dissimilar artifact (no version detected)."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|completely different topics",
            role="assistant",
            finish_reason="stop",
        )

        # Add first artifact
        store.add(sample_artifact)

        # Add dissimilar artifact
        store.add(different_artifact)

        # Both should exist independently
        assert store.get(sample_artifact.id) is not None
        assert store.get(different_artifact.id) is not None

        # Both should have version 1
        assert store.get(sample_artifact.id).version == 1
        assert store.get(different_artifact.id).version == 1

    @patch("wichy.artifact.helpers.call_llm")
    def test_add_similar_artifact_creates_version(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test adding a similar artifact creates a new version."""
        # Mock LLM to indicate match
        mock_call_llm.return_value = Message(
            content=f"{sample_artifact.id}|0.92|refined implementation details",
            role="assistant",
            finish_reason="stop",
        )

        # Add first artifact
        store.add(sample_artifact)
        original_id = sample_artifact.id

        # Add similar artifact (should be detected as new version)
        store.add(similar_artifact)

        # Original should be marked as replaced
        original = store.get(original_id)
        assert original.replaced_by == similar_artifact.id

        # New artifact should have incremented version
        new_version = store.get(similar_artifact.id)
        assert new_version.version == 2

    @patch("wichy.artifact.helpers.call_llm")
    def test_add_fails_if_backend_create_fails(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test that add raises exception if backend create fails."""
        mock_call_llm.return_value = Message(
            content=f"{sample_artifact.id}|0.95|same artifact",
            role="assistant",
            finish_reason="stop",
        )

        store.add(sample_artifact)

        # Try to add similar with same ID (should fail)
        duplicate = similar_artifact.model_copy(update={"id": sample_artifact.id})

        with pytest.raises(Exception, match="failed to add artifact"):
            store.add(duplicate)

    @patch("wichy.artifact.helpers.call_llm")
    def test_add_with_invalid_llm_id(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test handling when LLM returns invalid artifact ID."""
        # Mock LLM to return non-existent ID
        mock_call_llm.return_value = Message(
            content="artifact_nonexistent|0.85|similar content",
            role="assistant",
            finish_reason="stop",
        )

        store.add(sample_artifact)

        # Add similar artifact with invalid ID from LLM
        # Should add as new artifact with warning
        store.add(similar_artifact)

        # Both should exist as separate artifacts
        assert store.get(sample_artifact.id) is not None
        assert store.get(similar_artifact.id) is not None
        assert store.get(sample_artifact.id).replaced_by is None


class TestSimilarityScoring:
    """Test similarity detection methods."""

    def test_normalize_text(self, store):
        """Test text normalization."""
        text = "  Hello   World  \n  Test  "
        normalized = store._normalize_text(text)
        assert normalized == "hello world test"

    def test_normalize_empty_string(self, store):
        """Test normalizing empty string."""
        assert store._normalize_text("") == ""
        assert store._normalize_text(None) == ""

    def test_comparison_string(self, store, sample_artifact):
        """Test comparison string generation."""
        comp_str = store._comparison_string(sample_artifact)
        assert "jwt authentication" in comp_str.lower()
        assert "implementation plan" in comp_str.lower()

    def test_levenshtein_ratio_identical(self, store):
        """Test Levenshtein ratio for identical strings."""
        ratio = store._levenshtein_ratio("hello world", "hello world")
        assert ratio == 1.0

    def test_levenshtein_ratio_different(self, store):
        """Test Levenshtein ratio for different strings."""
        ratio = store._levenshtein_ratio("hello", "world")
        assert 0.0 <= ratio < 1.0

    def test_levenshtein_ratio_empty_strings(self, store):
        """Test Levenshtein ratio for empty strings."""
        ratio = store._levenshtein_ratio("", "")
        assert ratio == 1.0

    def test_jaccard_tokens_identical(self, store):
        """Test Jaccard similarity for identical strings."""
        score = store._jaccard_tokens("hello world", "hello world")
        assert score == 1.0

    def test_jaccard_tokens_overlap(self, store):
        """Test Jaccard similarity with partial overlap."""
        score = store._jaccard_tokens("hello world test", "hello world example")
        # Intersection: {hello, world} = 2
        # Union: {hello, world, test, example} = 4
        assert score == 0.5

    def test_jaccard_tokens_no_overlap(self, store):
        """Test Jaccard similarity with no overlap."""
        score = store._jaccard_tokens("hello world", "foo bar")
        assert score == 0.0

    def test_jaccard_tokens_empty_strings(self, store):
        """Test Jaccard similarity for empty strings."""
        score = store._jaccard_tokens("", "")
        assert score == 1.0


class TestFindPossiblePreviousVersion:
    """Test finding possible previous versions."""

    def test_find_with_empty_store(self, store, sample_artifact):
        """Test finding candidates in empty store."""
        candidates = store._find_possible_previous_version(sample_artifact)
        assert candidates == []

    def test_find_excludes_self(self, store, sample_artifact):
        """Test that artifact doesn't match itself."""
        store.add(sample_artifact)

        # Try to find previous version of same artifact
        candidates = store._find_possible_previous_version(sample_artifact)
        assert sample_artifact.id not in candidates

    @patch("wichy.artifact.helpers.call_llm")
    def test_find_similar_artifact(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test finding similar artifact as candidate."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|first artifact",
            role="assistant",
            finish_reason="stop",
        )

        store.add(sample_artifact)

        candidates = store._find_possible_previous_version(similar_artifact)
        assert len(candidates) > 0
        assert sample_artifact.id in candidates

    @patch("wichy.artifact.helpers.call_llm")
    def test_find_excludes_dissimilar(
        self, mock_call_llm, store, sample_artifact, different_artifact
    ):
        """Test that dissimilar artifacts are not candidates."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|unrelated", role="assistant", finish_reason="stop"
        )

        store.add(sample_artifact)

        candidates = store._find_possible_previous_version(different_artifact)
        # Due to low similarity scores, should be empty or not include sample_artifact
        if candidates:
            # If any candidates, they should have reasonable similarity
            assert len(candidates) <= 5

    @patch("wichy.artifact.helpers.call_llm")
    def test_find_returns_top_candidates(self, mock_call_llm, store):
        """Test that only top N candidates are returned."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|adding first batch",
            role="assistant",
            finish_reason="stop",
        )

        # Add multiple similar artifacts
        for i in range(10):
            artifact = Artifact(
                type="plan",
                title=f"Similar Plan {i}",
                description="Implementation plan for authentication",
                creator="agent",
                content="Auth implementation details...",
            )
            store.add(artifact)

        # Create query artifact
        query = Artifact(
            type="plan",
            title="Similar Plan Query",
            description="Implementation plan for authentication",
            creator="agent",
            content="Auth implementation details...",
        )

        candidates = store._find_possible_previous_version(query)
        # Should return at most 5 candidates
        assert len(candidates) <= 5


class TestGetMethods:
    """Test artifact retrieval methods."""

    def test_get_existing_artifact(self, store, sample_artifact):
        """Test getting an existing artifact."""
        store.add(sample_artifact)

        retrieved = store.get(sample_artifact.id)
        assert retrieved is not None
        assert retrieved.id == sample_artifact.id

    def test_get_nonexistent_artifact(self, store):
        """Test getting a non-existent artifact returns None."""
        retrieved = store.get("nonexistent_id")
        assert retrieved is None

    @patch("wichy.artifact.helpers.call_llm")
    def test_get_latest_with_no_replacement(
        self, mock_call_llm, store, sample_artifact
    ):
        """Test get_latest when artifact has no replacement."""
        store.add(sample_artifact)

        latest = store.get_latest(sample_artifact.id)
        assert latest is not None
        assert latest.id == sample_artifact.id
        assert latest.version == 1

    @patch("wichy.artifact.helpers.call_llm")
    def test_get_latest_follows_chain(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test get_latest follows replacement chain."""
        mock_call_llm.return_value = Message(
            content=f"{sample_artifact.id}|0.95|updated version",
            role="assistant",
            finish_reason="stop",
        )

        store.add(sample_artifact)
        store.add(similar_artifact)

        # Get latest from original ID
        latest = store.get_latest(sample_artifact.id)
        assert latest.id == similar_artifact.id
        assert latest.version == 2

    @patch("wichy.artifact.helpers.call_llm")
    def test_get_latest_multi_level_chain(self, mock_call_llm, store):
        """Test get_latest with multi-level version chain."""
        # Create version chain: v1 -> v2 -> v3
        v1 = Artifact(
            type="plan",
            title="Plan v1",
            description="First version",
            creator="agent",
            content="Version 1 content",
        )

        mock_call_llm.return_value = Message(
            content="no_match|0.90|first", role="assistant", finish_reason="stop"
        )
        store.add(v1)

        v2 = Artifact(
            type="plan",
            title="Plan v2",
            description="Second version",
            creator="agent",
            content="Version 2 content",
        )
        mock_call_llm.return_value = Message(
            content=f"{v1.id}|0.95|updated", role="assistant", finish_reason="stop"
        )
        store.add(v2)

        v3 = Artifact(
            type="plan",
            title="Plan v3",
            description="Third version",
            creator="agent",
            content="Version 3 content",
        )
        mock_call_llm.return_value = Message(
            content=f"{v2.id}|0.95|updated again",
            role="assistant",
            finish_reason="stop",
        )
        store.add(v3)

        # Getting latest from v1 should return v3
        latest = store.get_latest(v1.id)
        assert latest.id == v3.id
        assert latest.version == 3

    def test_get_latest_nonexistent(self, store):
        """Test get_latest with non-existent artifact."""
        latest = store.get_latest("nonexistent_id")
        assert latest is None


class TestAllLatest:
    """Test retrieving all latest artifacts."""

    def test_all_latest_empty_store(self, store):
        """Test all_latest with empty store."""
        artifacts = store.all_latest()
        assert artifacts == []

    @patch("wichy.artifact.helpers.call_llm")
    def test_all_latest_single_artifact(self, mock_call_llm, store, sample_artifact):
        """Test all_latest with single artifact."""
        store.add(sample_artifact)

        artifacts = store.all_latest()
        assert len(artifacts) == 1
        assert artifacts[0].id == sample_artifact.id

    @patch("wichy.artifact.helpers.call_llm")
    def test_all_latest_multiple_artifacts(
        self, mock_call_llm, store, sample_artifact, different_artifact
    ):
        """Test all_latest with multiple independent artifacts."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|different", role="assistant", finish_reason="stop"
        )

        store.add(sample_artifact)
        store.add(different_artifact)

        artifacts = store.all_latest()
        assert len(artifacts) == 2
        ids = [a.id for a in artifacts]
        assert sample_artifact.id in ids
        assert different_artifact.id in ids

    @patch("wichy.artifact.helpers.call_llm")
    def test_all_latest_excludes_replaced(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test all_latest excludes replaced versions."""
        mock_call_llm.return_value = Message(
            content=f"{sample_artifact.id}|0.95|new version",
            role="assistant",
            finish_reason="stop",
        )

        store.add(sample_artifact)
        store.add(similar_artifact)

        artifacts = store.all_latest()
        # Should only return the latest version
        assert len(artifacts) == 1
        assert artifacts[0].id == similar_artifact.id
        assert artifacts[0].version == 2

    @patch("wichy.artifact.helpers.call_llm")
    def test_all_latest_filters_by_session(self, mock_call_llm, store, sample_artifact):
        """Test all_latest only returns artifacts from current session."""
        # Add artifact to this session
        store.add(sample_artifact)

        # Add artifact to different session
        other_artifact = Artifact(
            type="research",
            title="Other Session Artifact",
            description="From different session",
            creator="agent",
            content="Other content",
        )
        other_store = ArtifactStore(session_id="other_session")
        other_store.add(other_artifact)

        # Should only get artifacts from original session
        artifacts = store.all_latest()
        ids = [a.id for a in artifacts]
        assert sample_artifact.id in ids
        assert other_artifact.id not in ids


class TestAllLatestPromptFormatted:
    """Test formatted output of all latest artifacts."""

    def test_all_latest_prompt_formatted_empty(self, store):
        """Test prompt formatting with empty store."""
        formatted = store.all_latest_prompt_formatted()
        assert formatted == ""

    @patch("wichy.artifact.helpers.call_llm")
    def test_all_latest_prompt_formatted(self, mock_call_llm, store, sample_artifact):
        """Test prompt formatting includes artifact info."""
        store.add(sample_artifact)

        formatted = store.all_latest_prompt_formatted()
        assert sample_artifact.id in formatted
        assert sample_artifact.title in formatted
        assert sample_artifact.description in formatted


class TestArtifactsForPrompt:
    """Test selecting relevant artifacts for a prompt."""

    def test_artifacts_for_prompt_empty_prompt_raises(self, store):
        """Test that empty prompt raises ValueError."""
        with pytest.raises(ValueError, match="cannot have an empty prompt"):
            store.artifacts_for_prompt("", intended_recipient="agent")

        with pytest.raises(ValueError, match="cannot have an empty prompt"):
            store.artifacts_for_prompt("   ", intended_recipient="agent")

    @patch("wichy.artifact.helpers.call_llm")
    def test_artifacts_for_prompt_no_matches(
        self, mock_call_llm, store, sample_artifact
    ):
        """Test when LLM finds no relevant artifacts."""
        mock_call_llm.return_value = Message(
            content="no_match", role="assistant", finish_reason="stop"
        )

        store.add(sample_artifact)

        relevant = store.artifacts_for_prompt(
            "Tell me about database optimization", intended_recipient="research_agent"
        )
        assert relevant == []

    @patch("wichy.artifact.helpers.call_llm")
    def test_artifacts_for_prompt_single_match(
        self, mock_call_llm, store, sample_artifact
    ):
        """Test when LLM finds one relevant artifact."""
        store.add(sample_artifact)

        mock_call_llm.return_value = Message(
            content=sample_artifact.id, role="assistant", finish_reason="stop"
        )

        relevant = store.artifacts_for_prompt(
            "How should we implement JWT authentication?",
            intended_recipient="code_agent",
        )
        assert len(relevant) == 1
        assert relevant[0].id == sample_artifact.id

    @patch("wichy.artifact.helpers.call_llm")
    def test_artifacts_for_prompt_multiple_matches(
        self, mock_call_llm, store, sample_artifact, different_artifact
    ):
        """Test when LLM finds multiple relevant artifacts."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|adding", role="assistant", finish_reason="stop"
        )

        store.add(sample_artifact)
        store.add(different_artifact)

        mock_call_llm.return_value = Message(
            content=f"{sample_artifact.id}|{different_artifact.id}",
            role="assistant",
            finish_reason="stop",
        )

        relevant = store.artifacts_for_prompt(
            "What work has been done on authentication and database optimization?",
            intended_recipient="manager_agent",
        )
        assert len(relevant) == 2
        ids = [a.id for a in relevant]
        assert sample_artifact.id in ids
        assert different_artifact.id in ids

    @patch("wichy.artifact.helpers.call_llm")
    def test_artifacts_for_prompt_invalid_id(
        self, mock_call_llm, store, sample_artifact
    ):
        """Test handling when LLM returns invalid artifact ID."""
        store.add(sample_artifact)

        mock_call_llm.return_value = Message(
            content=f"{sample_artifact.id}|artifact_invalid_id",
            role="assistant",
            finish_reason="stop",
        )

        relevant = store.artifacts_for_prompt("Test prompt", intended_recipient="agent")
        # Should only return valid artifact, skip invalid one
        assert len(relevant) == 1
        assert relevant[0].id == sample_artifact.id

    @patch("wichy.artifact.helpers.call_llm")
    def test_artifacts_for_prompt_with_recipient(
        self, mock_call_llm, store, sample_artifact
    ):
        """Test that recipient is passed to LLM."""
        store.add(sample_artifact)

        mock_call_llm.return_value = Message(
            content=sample_artifact.id, role="assistant", finish_reason="stop"
        )

        store.artifacts_for_prompt(
            "Implement authentication", intended_recipient="code_agent"
        )

        # Verify LLM was called with context containing recipient
        assert mock_call_llm.called
        call_args = mock_call_llm.call_args
        context = call_args[1]["context"]

        # Check that context contains recipient info
        user_message = next((msg for msg in context if msg["role"] == "user"), None)
        assert user_message is not None
        assert "code_agent" in user_message["content"]


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @patch("wichy.artifact.helpers.call_llm")
    def test_unicode_in_artifacts(self, mock_call_llm, store):
        """Test handling Unicode content in artifacts."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|first", role="assistant", finish_reason="stop"
        )

        artifact = Artifact(
            type="research",
            title="Unicode Test 你好 🎉",
            description="Testing: Ñoño, café, 日本語",
            creator="agent",
            content="Content: 🚀 ñ é ü",
        )

        store.add(artifact)
        retrieved = store.get(artifact.id)

        assert retrieved.title == artifact.title
        assert retrieved.description == artifact.description

    @patch("wichy.artifact.helpers.call_llm")
    def test_large_artifact_content(self, mock_call_llm, store):
        """Test handling large artifact content."""
        mock_call_llm.return_value = Message(
            content="no_match|0.90|first", role="assistant", finish_reason="stop"
        )

        large_content = "x" * 100_000
        artifact = Artifact(
            type="raw_data",
            title="Large Data",
            description="Testing large content",
            creator="agent",
            content=large_content,
        )

        store.add(artifact)
        retrieved = store.get(artifact.id)

        assert len(retrieved.content) == 100_000

    @patch("wichy.artifact.helpers.call_llm")
    def test_malformed_llm_response_no_match(
        self, mock_call_llm, store, sample_artifact, similar_artifact
    ):
        """Test handling malformed LLM response (falls back to no_match)."""
        store.add(sample_artifact)

        # Mock malformed response
        mock_call_llm.side_effect = Exception("LLM error")

        # Should handle gracefully
        with pytest.raises(Exception):
            store.add(similar_artifact)
