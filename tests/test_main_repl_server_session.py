"""The REPL mode's background server must expose the agent to the notes API.

Wichy runs in one of two modes: REPL or server. In server mode the
ChatSession is registered with the Flask server, so the browser's notes page
can notify the agent of user edits through POST /tools/notes/api/changes.
The REPL path never registered anything: the notes GUI worked, but every
edit was answered 503 "no active session" and parked forever.

The fix registers an un-started ChatSession in the REPL path. Un-started is
the load-bearing half: the REPL owns agent turns, so the session must be a
holder making the agent reachable from the Flask thread, not a second
processing loop.
"""

from __future__ import annotations

import signal
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_root_agent():
    """A root agent the way main() receives it from build_agent_from_config."""
    agent = MagicMock()
    agent.process.return_value = "Done."
    agent.agent_has_first_initiative = False
    agent.context = MagicMock()
    agent.context.return_value = []
    agent.context.append = MagicMock()
    return agent


@pytest.fixture(autouse=True)
def restore_sigchld_handler():
    """Restore the SIGCHLD handler after each test (see test_pipeline.py)."""
    original_sigchld = signal.getsignal(signal.SIGCHLD)
    yield
    signal.signal(signal.SIGCHLD, original_sigchld)


class TestReplModeRegistersTheServerSession:
    """`wichy --first` (REPL + background server) must register the session."""

    def _run_repl_mode(self, mock_root_agent: MagicMock):
        """Run main() in REPL mode with the server "started" and the REPL
        neutralised; return the recorded set_server_active_session calls."""
        with (
            patch(
                "wichy.__main__.build_agent_from_config",
                return_value=mock_root_agent,
            ),
            patch("wichy.__main__.start_server_in_background", return_value=7891),
            patch("wichy.__main__.Repl") as mock_repl,
            patch("wichy.__main__.set_server_active_session") as mock_set_session,
            patch("wichy.skills.reloader.SkillReloader"),
            patch.object(sys, "argv", ["wichy", "--first"]),
        ):
            # Repl.run() would block on terminal input; main() must return.
            mock_repl.return_value.run.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                from wichy.__main__ import main

                main()
        return mock_set_session

    def test_the_session_is_registered_when_the_server_starts(self, mock_root_agent):
        spy = self._run_repl_mode(mock_root_agent)
        spy.assert_called_once()

    def test_the_registered_session_wraps_this_agent(self, mock_root_agent):
        spy = self._run_repl_mode(mock_root_agent)
        (session,), _ = spy.call_args
        assert session.root_agent is mock_root_agent

    def test_the_session_is_a_real_chatsession(self, mock_root_agent):
        from wichy.wichy_server import ChatSession

        spy = self._run_repl_mode(mock_root_agent)
        (session,), _ = spy.call_args
        assert isinstance(session, ChatSession)

    def test_the_session_is_not_started(self, mock_root_agent):
        """The REPL owns agent turns; a started session would run a second
        processing loop against the same agent. ChatSession.start() is what
        spawns that loop and records its thread."""
        spy = self._run_repl_mode(mock_root_agent)
        (session,), _ = spy.call_args
        assert session._thread is None

    def test_no_session_without_the_server(self, mock_root_agent):
        """--no-server starts no Flask app, so there is nothing to notify."""
        with (
            patch(
                "wichy.__main__.build_agent_from_config",
                return_value=mock_root_agent,
            ),
            patch("wichy.__main__.Repl") as mock_repl,
            patch("wichy.__main__.set_server_active_session") as mock_set_session,
            patch("wichy.skills.reloader.SkillReloader"),
            patch.object(sys, "argv", ["wichy", "--first", "--no-server"]),
        ):
            mock_repl.return_value.run.side_effect = SystemExit(0)
            with pytest.raises(SystemExit):
                from wichy.__main__ import main

                main()
        mock_set_session.assert_not_called()
