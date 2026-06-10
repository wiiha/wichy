"""Minimal chat state: JSONL history only. No classification. No filtering."""

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wichy.config import settings

# ---------------------------------------------------------------------------
# Persistent history — JSONL in .wichy/chat/history.jsonl
# ---------------------------------------------------------------------------

HISTORY_DIR = Path(".wichy") / "chat"
HISTORY_FILE = HISTORY_DIR / "history.jsonl"
MAX_HISTORY = 10_000

_file_lock = threading.Lock()
_append_count = 0


def _ensure_dir() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def load_history() -> list[dict[str, Any]]:
    """Read history from disk."""
    _ensure_dir()
    if not HISTORY_FILE.exists():
        return []
    entries = []
    with HISTORY_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if len(entries) > MAX_HISTORY:
        entries = entries[-MAX_HISTORY:]
        _rewrite(entries)
    return entries


def _rewrite(entries: list[dict[str, Any]]) -> None:
    _ensure_dir()
    with _file_lock:
        with HISTORY_FILE.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


def append(entry: dict[str, Any]) -> None:
    """Append a single entry to history file."""
    global _append_count
    _ensure_dir()
    with _file_lock:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _append_count += 1
        if _append_count % 100 == 0:
            entries = load_history()
            if len(entries) > MAX_HISTORY:
                _rewrite(entries[-MAX_HISTORY:])


def create_entry(role: str, content: str, msg_type: str = "message") -> dict[str, Any]:
    """Create a new history entry."""
    return {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content,
        "type": msg_type,
    }
