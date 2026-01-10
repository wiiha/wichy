import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from wichy.artifact.artifact import Artifact, ArtifactReference


class TestArtifact:
    """Test suite for Artifact class"""

    def test_artifact_creation_with_defaults(self):
        """Test creating an artifact with minimal required fields"""
        artifact = Artifact(
            type="plan",
            title="Test Plan",
            description="A test implementation plan",
            creator="test_agent",
            content="# Plan Content\n\nSome content here",
        )

        assert artifact.id.startswith("artifact_")
        assert len(artifact.id) == 21  # "artifact_" + 12 hex chars
        assert artifact.type == "plan"
        assert artifact.title == "Test Plan"
        assert artifact.description == "A test implementation plan"
        assert artifact.creator == "test_agent"
        assert artifact.version == 1
        assert artifact.replaced_by is None
        assert artifact.metadata == {}
        assert isinstance(artifact.created_at, datetime)

    def test_artifact_creation_with_all_fields(self):
        """Test creating an artifact with all fields specified"""
        created_at = datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
        metadata = {"confidence": 0.95, "related_files": ["file1.py", "file2.py"]}

        artifact = Artifact(
            id="artifact_custom123",
            type="research",
            title="Research Findings",
            description="Analysis of authentication methods",
            creator="research_agent",
            created_at=created_at,
            version=2,
            replaced_by="artifact_newer456",
            content="# Research\n\nDetailed findings...",
            metadata=metadata,
        )

        assert artifact.id == "artifact_custom123"
        assert artifact.type == "research"
        assert artifact.version == 2
        assert artifact.replaced_by == "artifact_newer456"
        assert artifact.created_at == created_at
        assert artifact.metadata == metadata

    def test_artifact_types(self):
        """Test all valid artifact types"""
        valid_types = ["plan", "research", "analysis", "raw_data"]

        for artifact_type in valid_types:
            artifact = Artifact(
                type=artifact_type,
                title="Test",
                description="Test description",
                creator="test_agent",
                content="Content",
            )
            assert artifact.type == artifact_type

    def test_artifact_immutability(self):
        """Test that artifacts are immutable (frozen)"""
        artifact = Artifact(
            type="plan",
            title="Test Plan",
            description="A test plan",
            creator="test_agent",
            content="Content",
        )

        with pytest.raises(Exception):  # Pydantic raises ValidationError or similar
            artifact.title = "Modified Title"

        with pytest.raises(Exception):
            artifact.version = 2

    def test_title_validation(self):
        """Test title length validation"""
        # Title too short
        with pytest.raises(Exception):
            Artifact(
                type="plan",
                title="",
                description="A test plan",
                creator="test_agent",
                content="Content",
            )

        # Title too long (> 200 chars)
        with pytest.raises(Exception):
            Artifact(
                type="plan",
                title="x" * 201,
                description="A test plan",
                creator="test_agent",
                content="Content",
            )

        # Valid title at boundary
        artifact = Artifact(
            type="plan",
            title="x" * 200,
            description="A test plan",
            creator="test_agent",
            content="Content",
        )
        assert len(artifact.title) == 200

    def test_description_validation(self):
        """Test description length validation"""
        # Description too short
        with pytest.raises(Exception):
            Artifact(
                type="plan",
                title="Test",
                description="",
                creator="test_agent",
                content="Content",
            )

        # Description too long (> 500 chars)
        with pytest.raises(Exception):
            Artifact(
                type="plan",
                title="Test",
                description="x" * 501,
                creator="test_agent",
                content="Content",
            )

        # Valid description at boundary
        artifact = Artifact(
            type="plan",
            title="Test",
            description="x" * 500,
            creator="test_agent",
            content="Content",
        )
        assert len(artifact.description) == 500

    def test_content_validation(self):
        """Test content cannot be empty"""
        with pytest.raises(Exception):
            Artifact(
                type="plan",
                title="Test",
                description="A test plan",
                creator="test_agent",
                content="",
            )

    def test_version_validation(self):
        """Test version must be >= 1"""
        with pytest.raises(Exception):
            Artifact(
                type="plan",
                title="Test",
                description="A test plan",
                creator="test_agent",
                content="Content",
                version=0,
            )

        # Valid version
        artifact = Artifact(
            type="plan",
            title="Test",
            description="A test plan",
            creator="test_agent",
            content="Content",
            version=1,
        )
        assert artifact.version == 1

    def test_as_text_basic(self):
        """Test as_text method with basic artifact"""
        artifact = Artifact(
            id="artifact_test123",
            type="plan",
            title="Test Plan",
            description="A test implementation plan",
            creator="test_agent",
            created_at=datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc),
            version=1,
            content="# Plan Content\n\nSome content here",
        )

        text = artifact.as_text()

        assert "# Artifact: Test Plan" in text
        assert "**ID:** artifact_test123" in text
        assert "**Type:** plan" in text
        assert "**Creator:** test_agent" in text
        assert "**Version:** 1" in text
        assert "**Created:** 2025-01-10T12:00:00+00:00" in text
        assert "**Description:** A test implementation plan" in text
        assert "**Content:**" in text
        assert "# Plan Content" in text
        assert "Some content here" in text

    def test_as_text_with_replaced_by(self):
        """Test as_text includes replaced_by when present"""
        artifact = Artifact(
            type="plan",
            title="Old Plan",
            description="Superseded plan",
            creator="test_agent",
            content="Old content",
            replaced_by="artifact_newer456",
        )

        text = artifact.as_text()
        assert "**Replaced By:** artifact_newer456" in text

    def test_as_text_with_metadata(self):
        """Test as_text includes metadata when present"""
        artifact = Artifact(
            type="research",
            title="Research",
            description="Research findings",
            creator="research_agent",
            content="Findings...",
            metadata={
                "confidence": 0.95,
                "related_files": ["auth.py", "models.py"],
                "tags": ["security", "authentication"],
            },
        )

        text = artifact.as_text()
        assert "**Metadata:**" in text
        assert "- confidence: 0.95" in text
        assert "- related_files: ['auth.py', 'models.py']" in text
        assert "- tags: ['security', 'authentication']" in text

    def test_as_text_without_metadata(self):
        """Test as_text doesn't include metadata section when empty"""
        artifact = Artifact(
            type="plan",
            title="Test",
            description="Test description",
            creator="test_agent",
            content="Content",
        )

        text = artifact.as_text()
        assert "**Metadata:**" not in text

    def test_serialization_json(self):
        """Test artifact can be serialized to JSON"""
        artifact = Artifact(
            type="plan",
            title="Test Plan",
            description="A test plan",
            creator="test_agent",
            content="Content",
            metadata={"key": "value"},
        )

        json_str = artifact.model_dump_json()
        data = json.loads(json_str)

        assert data["type"] == "plan"
        assert data["title"] == "Test Plan"
        assert data["metadata"]["key"] == "value"

    def test_deserialization_json(self):
        """Test artifact can be deserialized from JSON"""
        json_data = {
            "id": "artifact_abc123",
            "type": "analysis",
            "title": "Test Analysis",
            "description": "An analysis",
            "creator": "analyzer_agent",
            "created_at": "2025-01-10T12:00:00Z",
            "version": 1,
            "replaced_by": None,
            "content": "Analysis content",
            "metadata": {},
        }

        artifact = Artifact.model_validate(json_data)

        assert artifact.id == "artifact_abc123"
        assert artifact.type == "analysis"
        assert artifact.title == "Test Analysis"


class TestArtifactReference:
    """Test suite for ArtifactReference class"""

    def test_from_artifact(self):
        """Test creating a reference from a full artifact"""
        artifact = Artifact(
            id="artifact_test123",
            type="plan",
            title="Test Plan",
            description="A test implementation plan",
            creator="test_agent",
            created_at=datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc),
            version=2,
            content="# Plan Content",
            metadata={"key": "value"},
        )

        ref = ArtifactReference.from_artifact(artifact)

        assert ref.id == "artifact_test123"
        assert ref.type == "plan"
        assert ref.title == "Test Plan"
        assert ref.description == "A test implementation plan"
        assert ref.creator == "test_agent"
        assert ref.version == 2
        assert ref.created_at == datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
        # Note: metadata and content are NOT included in reference

    def test_format_for_prompt(self):
        """Test formatting reference for prompt inclusion"""
        ref = ArtifactReference(
            id="artifact_abc123",
            type="research",
            title="JWT Research",
            description="Analysis of JWT authentication methods",
            version=1,
            created_at=datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc),
            creator="research_agent",
        )

        formatted = ref.format_for_prompt()

        assert formatted == (
            "- [ID: artifact_abc123; type: research; creator: research_agent] "
            "JWT Research: Analysis of JWT authentication methods"
        )

    def test_artifact_reference_is_lightweight(self):
        """Test that reference doesn't include heavy fields"""
        artifact = Artifact(
            type="raw_data",
            title="Large Dataset",
            description="A large CSV dataset",
            creator="data_agent",
            content="x" * 10000,  # Large content
            metadata={"rows": 100000, "columns": 50},
        )

        ref = ArtifactReference.from_artifact(artifact)

        # Reference shouldn't have content or metadata
        assert not hasattr(ref, "content")
        assert not hasattr(ref, "metadata")

        # But should have all identifying info
        assert ref.id == artifact.id
        assert ref.title == artifact.title
        assert ref.description == artifact.description
