import uuid
from datetime import datetime
from typing import List

from pydantic import BaseModel, Field, field_validator


class MemoryNote(BaseModel):
    """A memory note that represents a single unit of information in the memory system.

    This class encapsulates all metadata associated with a memory, including:
    - Core content and identifiers
    - Temporal information (creation and access times)
    - Semantic metadata (keywords, context, tags)
    - Relationship data (links to other memories)
    - Usage statistics (retrieval count)
    - Evolution tracking (history of changes)
    """

    content: str
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    keywords: List[str] = Field(default_factory=list)
    links: List = Field(default_factory=list)
    retrieval_count: int = 0
    timestamp: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M")
    )
    last_accessed: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y%m%d%H%M")
    )
    context: str = "General"
    evolution_history: List = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    class Config:
        """Pydantic configuration."""

        validate_assignment = True

    @field_validator("timestamp", "last_accessed", mode="before")
    @classmethod
    def _coerce_to_str(cls, v):
        """Coerce numeric timestamps to strings."""
        if isinstance(v, (int, float)):
            return str(int(v))
        return v

    def needs_analysis(self) -> bool:
        """Check if the note needs analysis based on its metadata completeness.

        Returns:
            bool: True if the note has empty keywords, default context, or empty tags.
        """
        return (
            not self.keywords  # keywords is empty list
            or self.context
            == MemoryNote(content="").context  # context is default value
            or not self.tags  # tags is empty list
        )

    def to_memory_string(self) -> str:
        """Generate a tab-separated string representation of the memory note.

        Returns:
            str: Formatted string with memory_id, timestamp, content, context, keywords, and tags.
        """
        return (
            f"memory_id:{self.id}\n"
            f"start time:{self.timestamp}\n"
            f"keywords: {str(self.keywords)}\n"
            f"tags: {str(self.tags)}\n"
            f"context: {self.context}\n"
            f"content: {self.content}\n"
        )
