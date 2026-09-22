"""Wiring that connects the notes feature to generic agent lifecycle events.

The notes UI shows an "agent is working" indicator. That requires knowing when
an agent turn is in flight, which is a generic concern -- so the agent base
class exposes a turn-observer hook and this module subscribes to it. No agent
code knows the notes feature exists; the dependency points this way only.

Registered from :func:`wichy.tools.notes.register`, which runs at app setup in
every mode, well before any turn can start.
"""

from __future__ import annotations

from wichy.agent.core import clear_turn_observers, observe_turns
from wichy.tools.notes.state import turn_begun, turn_ended

#: Guards against double installation. Registration is idempotent, which
#: matters because the server can be created more than once in one process
#: (a REPL starts a background Flask app), and each creation re-registers
#: blueprints.
_installed = False


def _turn_started(_agent) -> None:
    """Count a turn in, marking the agent busy."""
    turn_begun()


def _turn_ended(_agent) -> None:
    """Count a turn out, clearing busy only when none are left.

    A COUNT, not a boolean: agent turns nest (a sub-agent runs inside the outer
    turn), so the first inner turn to finish would clear the flag while the
    outer one was still running.
    """
    turn_ended()


def install_busy_observer() -> None:
    """Subscribe the busy indicator to agent turn boundaries.

    Safe to call repeatedly; only the first call registers. The observer fires
    for every agent that runs a turn, including the side-channel agent behind
    `/btw`, because "an agent is working" is true regardless of which agent it
    is.

    `TaskAgent` turns are covered too, through the same ``turn_scope`` on the
    shared base class. Any observer that means "any turn" would otherwise be
    wrong for sub-agents, which are exactly the turns that run while the user is
    waiting.

    **Accepted limitation: the message-acceptance window.** The indicator turns
    on when a TURN starts, and a user message is accepted (`POST` on the server
    API, which returns 200 immediately) somewhat before that -- the request is
    queued and the turn begins when the agent picks it up. In that gap, work has
    been asked for but this indicator still reads idle. It is deliberate rather
    than overlooked: setting the flag at acceptance would mean a second writer
    racing the turn observers, and closing the gap correctly needs the
    acceptance path to hand off to the turn rather than to set state a turn will
    later clear. The window is short, and what it costs is a slightly late
    spinner, not a wrong answer to "did my message register".
    """
    global _installed
    if _installed:
        return
    observe_turns(on_start=_turn_started, on_end=_turn_ended)
    _installed = True


def uninstall_busy_observer() -> None:
    """Remove the busy observer and allow a later reinstall.

    Without resetting ``_installed``, a test or a re-setup that called
    ``clear_turn_observers()`` left the indicator permanently disarmed: the
    observers were gone but this module still believed it had subscribed, so
    ``install_busy_observer`` returned early and the UI never reported busy
    again for the life of the process.
    """
    global _installed
    clear_turn_observers()
    _installed = False
