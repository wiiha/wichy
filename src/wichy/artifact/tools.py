from typing import Optional

from pydantic import BaseModel, Field

from wichy.tools.base import BaseTool

from .artifact import ARTIFACT_TYPES, Artifact
from .store import ArtifactStore


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
        description="Additional metadata like related_files, etc.",
    )

    creator: str = Field(
        "",
        description="HIDE_FROM_LLM Name of the agent that created this artifact",
    )


class NewArtifactTool(BaseTool):
    name = "artifact_create"
    description = "An artifact represents knowledge that will be stored over time and can be shared between different entities in a larger project context. Useful for tracking knowledge and findings over time."
    parameters_model = NewArtifactParameters

    def __init__(self, session_id: str):
        super().__init__()
        self.artifact_store = ArtifactStore(session_id=session_id)

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

            self.artifact_store.add(a=a)
            # this little dance is needed since the version number
            # will be modified by the add method.
            n = self.artifact_store.get(a.id)
            if n.version > 1:
                return f"Successfully created new version of artifact. ID: {a.id}"

            return f"Successfully created new artifact. ID: {a.id}"
        except Exception as e:
            return f"error: {e}"


class ArtifactByIDParameters(BaseModel):

    id: str = Field(
        ...,
        description="ID for artifact to fetch",
    )


class ArtifactByIDTool(BaseTool):
    name = "artifact_by_id"
    description = "Fetch an artifact by it's ID."
    parameters_model = ArtifactByIDParameters

    def __init__(self, session_id: str):
        super().__init__()
        self.artifact_store = ArtifactStore(session_id=session_id)

    def execute(self, id: str) -> str:
        """Get artifact by ID"""
        try:
            a = self.artifact_store.get(id)
            if a is None:
                return f"no artifact with id {id} found"

            return a.as_text()

        except Exception as e:
            return f"error: {e}"


class ArtifactByQueryParameters(BaseModel):

    query: str = Field(
        ...,
        description="Query (question, subject, or topic) to search for relevant artifacts",
        min_length=1,
    )

    recipient: str = Field(
        "",
        description="HIDE_FROM_LLM Name of the intended recipient (usually an agent) for the query results",
    )


class ArtifactByQueryTool(BaseTool):
    name = "artifact_search"
    description = "Search for artifacts by query. Finds artifacts containing information, data, analysis, or context relevant to the given query, question, or subject."
    parameters_model = ArtifactByQueryParameters

    def __init__(self, session_id: str):
        super().__init__()
        self.artifact_store = ArtifactStore(session_id=session_id)

    def execute(self, query: str, recipient: str = "") -> str:
        """Search artifacts by query"""
        try:
            artifacts = self.artifact_store.artifacts_for_query(
                query=query, intended_recipient=recipient
            )

            if len(artifacts) < 1:
                return "No relevant artifacts found for the given query."

            result = f"Found {len(artifacts)} relevant artifact(s):\n\n"
            for artifact in artifacts:
                result += "=" * 10 + "\n"
                result += artifact.as_text()
                result += "\n\n"

            return result

        except Exception as e:
            return f"error: {e}"
