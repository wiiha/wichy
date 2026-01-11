import uuid

from .helpers import console
from .tools import ArtifactByIDTool, ArtifactByQueryTool, NewArtifactTool

# Generate once per module load
SESSION_ID = str(uuid.uuid4())


def instantiate_artifact_tools_with_current_session_id():
    tools = [
        NewArtifactTool(session_id=SESSION_ID),
        ArtifactByIDTool(session_id=SESSION_ID),
        ArtifactByQueryTool(session_id=SESSION_ID),
    ]
    return tools


def new_artifact_tool_with_current_session():
    return NewArtifactTool(session_id=SESSION_ID)


__all__ = ["SESSION_ID", "console"]
