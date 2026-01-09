import uuid

from .helpers import console
from .tools import NewArtifactTool

# Generate once per module load
SESSION_ID = str(uuid.uuid4())


__all__ = ["SESSION_ID", "NewArtifactTool", "console"]
