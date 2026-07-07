"""Event record schema and payload preview helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

MAX_PREVIEW_LEN = 200
MAX_ARG_VALUE_LEN = 80


@dataclass(frozen=True)
class EventRecord:
    """A single event record persisted to the event log."""

    id: int
    timestamp: str
    session_id: str
    event_type: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        """Serialize the record to a single JSON line."""
        return json.dumps(asdict(self), default=str)


def preview_content(content: Any, max_len: int = MAX_PREVIEW_LEN) -> str:
    """Return a short preview of message content."""
    if isinstance(content, str):
        if len(content) <= max_len:
            return content
        return content[: max_len - 3] + "..."
    if isinstance(content, list):
        types = sorted({str(type(item).__name__).lower() for item in content})
        return "{" + ", ".join(types) + "}"
    text = json.dumps(content, default=str)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def preview_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shallow preview of tool arguments."""
    if not args:
        return {}
    preview: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, dict):
            preview[key] = {"nested": True}
        elif isinstance(value, list):
            preview[key] = f"[len={len(value)}]"
        elif isinstance(value, str):
            if len(value) <= MAX_ARG_VALUE_LEN:
                preview[key] = value
            else:
                preview[key] = value[: MAX_ARG_VALUE_LEN - 3] + "..."
        else:
            preview[key] = value
    return preview


def preview_text(text: Any, max_len: int = MAX_PREVIEW_LEN) -> str:
    """Return a generic text preview."""
    s = str(text)
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def now_iso() -> str:
    """Return the current wall-clock time as ISO-8601."""
    return datetime.now().isoformat()
