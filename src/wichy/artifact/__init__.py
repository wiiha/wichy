import uuid

from .helpers import console
from .tools import ArtifactByIDTool, ArtifactByQueryTool, NewArtifactTool

# Generate once per module load
SESSION_ID = str(uuid.uuid4())

ARTIFACT_TOOLS = [
    NewArtifactTool(session_id=SESSION_ID),
    ArtifactByIDTool(session_id=SESSION_ID),
    ArtifactByQueryTool(session_id=SESSION_ID),
]


def new_artifact_tool_with_current_session():
    return NewArtifactTool(session_id=SESSION_ID)


__all__ = ["SESSION_ID", "ARTIFACT_TOOLS ", "console"]
