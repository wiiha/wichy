from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

ARTIFACT_TYPES = Literal["plan", "research", "analysis", "raw_data"]


class Artifact(BaseModel):
    """
    Immutable artifact representing persistent knowledge created by agents.

    Artifacts enable context handoff between specialized agents without
    using the RootAgent as a message broker.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "artifact_a1b2c3d4e5f6",
                "type": "plan",
                "title": "JWT Authentication Implementation Plan",
                "description": "Detailed implementation plan for adding JWT-based authentication to the API endpoints.",
                "creator": "code_planner_agent",
                "created_at": "2025-12-21T10:30:00Z",
                "version": 1,
                "replaced_by": None,
                "content": "# Implementation Plan\n\n## Overview\n...",
                "metadata": {
                    "related_files": ["auth.py", "models.py"],
                    "confidence": 0.85,
                },
            }
        },
        frozen=True,  # make instances immutable (similar to allow_mutation=False in v1)
    )

    id: str = Field(
        default_factory=lambda: f"artifact_{uuid4().hex[:12]}",
        description="Unique identifier for this artifact",
    )

    type: ARTIFACT_TYPES = Field(
        description="Type of artifact determining its purpose and content structure"
    )

    title: str = Field(
        description="Human-readable title for the artifact",
        min_length=1,
        max_length=200,
    )

    description: str = Field(
        description="Short 1-2 sentence summary of the artifact content",
        min_length=1,
        max_length=500,
    )

    creator: str = Field(description="Name of the agent that created this artifact")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp when artifact was created",
    )

    version: int = Field(default=1, ge=1, description="Version number of this artifact")

    replaced_by: Optional[str] = Field(
        default=None,
        description="ID of the artifact that supersedes/replaces this one (null for latest version)",
    )

    content: str = Field(
        description="Full content of the artifact (markdown, JSON, CSV, etc.)",
        min_length=1,
    )

    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata like related_files, confidence, etc.",
    )

    def as_text(self) -> str:
        """
        Generate a string representation of the artifact for LLM consumption.

        Returns:
            Formatted string containing all relevant artifact information.
        """
        out = f"# Artifact: {self.title}\n\n"
        out += f"**ID:** {self.id}\n"
        out += f"**Type:** {self.type}\n"
        out += f"**Creator:** {self.creator}\n"
        out += f"**Version:** {self.version}\n"
        out += f"**Created:** {self.created_at.isoformat()}\n"

        if self.replaced_by:
            out += f"**Replaced By:** {self.replaced_by}\n"

        out += f"\n**Description:** {self.description}\n"

        if self.metadata:
            out += f"\n**Metadata:**\n"
            for key, value in self.metadata.items():
                out += f"- {key}: {value}\n"

        out += f"\n---\n\n**Content:**\n\n{self.content}\n"

        return out


class ArtifactReference(BaseModel):
    """
    Lightweight reference to an artifact for discovery/listing.
    Used in system prompts to avoid injecting full content.
    """

    id: str = Field(description="Artifact identifier")
    type: Literal["plan", "research", "analysis", "raw_data"]
    title: str
    description: str
    version: int
    created_at: datetime
    creator: str

    @classmethod
    def from_artifact(cls, artifact: Artifact) -> "ArtifactReference":
        """Create a reference from a full artifact"""
        return cls(
            id=artifact.id,
            type=artifact.type,
            title=artifact.title,
            description=artifact.description,
            version=artifact.version,
            created_at=artifact.created_at,
            creator=artifact.creator,
        )

    def format_for_prompt(self) -> str:
        """Format reference for inclusion in system prompt"""
        return f"- [ID: {self.id}; type: {self.type}; creator: {self.creator}] {self.title}: {self.description}"
