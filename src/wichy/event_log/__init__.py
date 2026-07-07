"""Event log public facade for wichy.

All other modules should import only from this package to keep the dependency
graph clean:

    from wichy.event_log import log_event, get_event_store
"""

from __future__ import annotations

from typing import Any

from .store import (
    EventStore,
    close_all,
    get_agent_event_store,
    get_event_store,
)


def log_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
) -> int:
    """
    Emit an event to the appropriate event store.

    Args:
        event_type: Name of the event (e.g. ``tool_call_started``).
        payload: Serializable event payload.
        session_id: Required for root/session events. If omitted and an agent_id
            is provided, the store must be retrieved separately.
        agent_id: If provided, the event is written to that agent's log.

    Returns:
        The assigned event id.
    """
    if agent_id is not None:
        if session_id is None:
            raise ValueError("session_id is required when agent_id is provided")
        store = get_agent_event_store(session_id, agent_id)
    else:
        if session_id is None:
            raise ValueError("session_id is required for root/session events")
        store = get_event_store(session_id)
    return store.emit(event_type, payload)


__all__ = [
    "EventStore",
    "close_all",
    "get_agent_event_store",
    "get_event_store",
    "log_event",
]
