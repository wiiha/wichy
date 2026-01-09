from typing import Optional

from pydantic import BaseModel, Field

from wichy.tools.base import BaseTool

from .artifact import ARTIFACT_TYPES, Artifact
from .store import ArtifactStore

artifact_store = ArtifactStore()


class NewArtifactParameters(BaseModel):

    type: ARTIFACT_TYPES = Field(
        ...,
        description="Type of artifact determining its purpose and content structure",
    )

    title: str = Field(
        ...,
        description="Human-readable title for the artifact",
        min_length=1,
        max_length=200,
    )

    description: str = Field(
        ...,
        description="Short 1-2 sentence summary of the artifact content",
        min_length=1,
        max_length=500,
    )

    content: str = Field(
        ...,
        description="Full content of the artifact (markdown, JSON, CSV, etc.)",
        min_length=1,
    )

    metadata: dict = Field(
        {},
        description="Additional metadata like related_files, confidence, etc.",
    )

    creator: str = Field(
        "",
        description="HIDE_FROM_LLM Name of the agent that created this artifact",
    )


class NewArtifactTool(BaseTool):
    name = "artifact_create"
    description = "An artifact represents knowledge that will be stored over time and can be shared between different entities in a larger project context. Useful for tracking knowledge and findings over time."
    parameters_model = NewArtifactParameters

    def execute(
        self,
        type,
        title,
        description,
        content,
        metadata: Optional[dict],
        creator="",
    ) -> str:
        """Add new artifact"""
        try:
            a = Artifact(
                type=type,
                title=title,
                description=description,
                content=content,
                metadata=metadata,
                creator=creator,
            )

            artifact_store.add(a=a)

            # this little dance is needed since the version number
            # will be modified by the add method.
            n = artifact_store.get(a.id)
            if n.version > 1:
                return f"Successfully updated artifact"

            return f"Successfully created new artifact"
        except Exception as e:
            return f"error: {e}"
