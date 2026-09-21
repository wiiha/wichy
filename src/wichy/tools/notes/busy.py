"""Wiring that connects the notes feature to generic agent lifecycle events.

The notes UI shows an "agent is working" indicator. That requires knowing when
an agent turn is in flight, which is a generic concern -- so the agent base
class exposes a turn-observer hook and this module subscribes to it. No agent
code knows the notes feature exists; the dependency points this way only.

Registered from :func:`wichy.tools.notes.register`, which runs at app setup in
every mode, well before any turn can start.
"""

from __future__ import annotations

from wichy.agent.core import observe_turns
from wichy.tools.notes.state import set_agent_busy

#: Guards against double installation. Registration is idempotent, which
#: matters because the server can be created more than once in one process
#: (a REPL starts a background Flask app), and each creation re-registers
#: blueprints.
_installed = False


def _turn_started(_agent) -> None:
    """Mark the agent busy when any agent turn begins."""
    set_agent_busy(True)


def _turn_ended(_agent) -> None:
    """Mark the agent idle when any agent turn ends, however it ended."""
    set_agent_busy(False)


def install_busy_observer() -> None:
    """Subscribe the busy indicator to agent turn boundaries.

    Safe to call repeatedly; only the first call registers. The observer fires
    for every agent that runs a turn, including the side-channel agent behind
    `/btw`, because "an agent is working" is true regardless of which agent it
    is.
    """
    global _installed
    if _installed:
        return
    observe_turns(on_start=_turn_started, on_end=_turn_ended)
    _installed = True
