"""Path helpers for event log storage."""
from pathlib import Path

from wichy.config import settings


def _events_base_dir() -> Path:
    """Return the configured base directory for event logs."""
    # TODO: read from settings once setting exists; default to .wichy/sessions
    return getattr(settings, "events_dir", Path(".wichy")) / "sessions"


def session_dir(session_id: str) -> Path:
    """Return the directory for a session's event logs."""
    path = _events_base_dir() / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_events_path(session_id: str) -> Path:
    """Return the path to the root session event log."""
    return session_dir(session_id) / "session.events.jsonl"


def agent_events_path(session_id: str, agent_id: str) -> Path:
    """Return the path to a task-agent event log."""
    agents_dir = session_dir(session_id) / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    return agents_dir / f"{agent_id}.events.jsonl"
