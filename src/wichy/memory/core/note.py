from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

# Import the ID generator
from wichy.helpers.gen_id import gen_id


class MemoryNote(BaseModel):
    """
    A single memory/note - the data transfer object for the Memory interface.
    """

    content: str
    """The actual text/content to remember"""

    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    """Arbitrary metadata (source, tags, entity, etc.)"""

    memory_id: str = Field(default_factory=gen_id)
    """Unique identifier (auto-generated using gen_id if not provided)"""

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    """Timestamp when memory was created (auto-set to current UTC time if not provided)"""

    retrieval_count: int = 0
    """Number of times this memory has been retrieved/accessed"""

    last_accessed: Optional[datetime] = None
    """Timestamp of last retrieval/access"""

    score: Optional[float] = Field(default=None, ge=0.0)
    """Relevance score from search results only (higher = more relevant)"""

    @field_validator("created_at", "last_accessed", mode="before")
    @classmethod
    def _coerce_timestamp(cls, v):
        """
        Handle various timestamp formats:
        - int/float (epoch timestamp) → convert to datetime
        - ISO string → parse to datetime
        - datetime → keep as is
        - None → keep as None
        """
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)):
            # Convert epoch timestamp to datetime (handle seconds or milliseconds)
            if v > 1e12:  # Likely milliseconds
                v = v / 1000
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, str):
            # Try ISO format first
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                pass
        raise ValueError(f"Cannot parse timestamp: {v}")

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, v):
        """Ensure score is a float or None."""
        if v is None:
            return None
        return float(v)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (all values JSON-compatible)"""
        return self.model_dump(exclude_none=True)

    def to_memory_string(self) -> str:
        """
        Generate a compact, human-readable string representation.
        Useful for logging, debugging, LLM context, etc.

        Example:
            [MEMORY abc123 | 2024-01-15 10:30:00 | retrieved 5 times | score:0.87]
            User's favorite color is blue (source: conversation)

        Returns:
            str: Formatted memory string
        """
        parts = []

        # Header with ID and stats
        header = f"[MEMORY {self.memory_id}"
        if self.created_at:
            dt_str = self.created_at.strftime("%Y-%m-%d %H:%M:%S")
            header += f" | {dt_str}"
        if self.retrieval_count > 0:
            header += f" | retrieved {self.retrieval_count} time"
            if self.retrieval_count != 1:
                header += "s"
        if self.score is not None:
            header += f" | score:{self.score:.3f}"
        header += "]"
        parts.append(header)

        # Content
        parts.append(self.content)

        # Metadata (if any)
        if self.metadata:
            meta_parts = [f"{k}: {v}" for k, v in self.metadata.items()]
            if meta_parts:
                parts.append(f"({', '.join(meta_parts)})")

        return "\n".join(parts)

    @property
    def importance(self) -> float:
        """
        Derived importance score 0.0-1.0 (higher = more important).

        Importance is calculated from usage patterns:
        - More retrievals → higher score (logarithmic, diminishing returns)
        - Recent access → slight boost (within last 24h)
        - Very old memories → slight decay (optional, can be extended)

        Returns:
            float: Importance score in range [0.0, 1.0]
        """
        # Base: logarithmic retrieval count (diminishing returns)
        base = min(1.0, (self.retrieval_count + 1) ** 0.5 / 10.0)

        # Recency boost (0-0.2) if accessed within last 24h
        recency_boost = 0.0
        if self.last_accessed:
            now = datetime.now(timezone.utc)
            hours_since_access = (now - self.last_accessed).total_seconds() / 3600
            if hours_since_access < 24:
                recency_boost = 0.2 * (1 - hours_since_access / 24)

        return min(1.0, base + recency_boost)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryNote":
        """Deserialize from dict (validates and parses automatically)"""
        return cls.model_validate(data)

    class Config:
        json_schema_extra = {
            "example": {
                "content": "User's favorite color is blue",
                "metadata": {"source": "conversation"},
                "memory_id": "abc123",
                "created_at": "2024-01-15T10:30:00Z",
                "retrieval_count": 5,
                "last_accessed": "2024-01-16T14:20:00Z",
                "score": 0.87,
            }
        }
