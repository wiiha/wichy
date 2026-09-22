"""Tests for the shared notes state module and the busy indicator.

The busy indicator is driven by generic agent turn observers, so these tests
cover three layers: the state primitive, the observer mechanism on the agent
base class, and the notes feature's subscription to it.

The exception path matters as much as the happy path: a raised turn must still
close the turn, or the notes UI reports "working" forever.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wichy.agent.core import (
    AgentCore,
    clear_turn_observers,
    observe_turns,
    on_turn_ended,
    on_turn_started,
)
from wichy.constants import ROLE_ASSISTANT
from wichy.context.handler import context_from_file
from wichy.llm_backend import Message
from wichy.root_agent.root_agent import RootAgent
from wichy.tools.notes.busy import install_busy_observer, uninstall_busy_observer
from wichy.tools.notes.state import (
    clear_agent_changes,
    clear_doc_version,
    describe_pending,
    drain_agent_changes,
    get_doc_lock,
    get_doc_version,
    is_agent_busy,
    forget_injected,
    note_injected,
    peek_agent_changes,
    queue_agent_change,
    rename_document,
    turn_begun,
    turn_ended,
    was_injected,
    reset_state,
    set_agent_busy,
    set_doc_version,
)


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts from empty state; both the module and the observer
    registry are process-global."""
    import wichy.tools.notes.busy as busy_module

    reset_state()
    clear_turn_observers()
    busy_module._installed = False
    yield
    reset_state()
    clear_turn_observers()
    busy_module._installed = False


# -------------------------------------------------------------------------
# Turn observers -- generic mechanism, no notes involvement
# -------------------------------------------------------------------------


class _StubAgent(AgentCore):
    """Minimal concrete agent used to exercise turn_scope() directly."""

    @property
    def name(self) -> str:
        return "stub"

    def run_turn(self, fn):
        """Run one turn framed by turn_scope()."""
        with self.turn_scope():
            return fn()


class TestTurnObservers:
    def test_start_and_end_fire_in_order(self):
        events = []
        observe_turns(
            on_start=lambda a: events.append("start"),
            on_end=lambda a: events.append("end"),
        )
        _StubAgent().run_turn(lambda: None)
        assert events == ["start", "end"]

    def test_end_fires_when_the_turn_raises(self):
        """The whole point of the finally: an error must still close the turn."""
        events = []
        observe_turns(
            on_start=lambda a: events.append("start"),
            on_end=lambda a: events.append("end"),
        )

        def explode():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            _StubAgent().run_turn(explode)
        assert events == ["start", "end"]

    def test_return_value_passes_through(self):
        observe_turns(on_start=lambda a: None, on_end=lambda a: None)
        assert _StubAgent().run_turn(lambda: "the result") == "the result"

    def test_observer_exception_does_not_break_the_turn(self):
        """A broken observer must never take down an agent turn."""

        def bad_start(_agent):
            raise RuntimeError("observer is broken")

        observe_turns(on_start=bad_start, on_end=lambda a: None)
        # Must not raise.
        assert _StubAgent().run_turn(lambda: "done") == "done"

    def test_a_broken_observer_does_not_stop_later_observers(self):
        def bad_start(_agent):
            raise RuntimeError("first is bad")

        seen = []
        observe_turns(on_start=bad_start, on_end=lambda a: None)
        observe_turns(on_start=lambda a: seen.append("second"), on_end=lambda a: None)
        _StubAgent().run_turn(lambda: None)
        assert seen == ["second"]

    def test_single_sided_registration_leaves_the_other_side_a_noop(self):
        started = []
        on_turn_started(lambda a: started.append("s"))
        _StubAgent().run_turn(lambda: None)
        assert started == ["s"]

    def test_ended_only_registration(self):
        ended = []
        on_turn_ended(lambda a: ended.append("e"))
        _StubAgent().run_turn(lambda: None)
        assert ended == ["e"]

    def test_observer_receives_the_agent_instance(self):
        seen = []
        observe_turns(on_start=seen.append, on_end=lambda a: None)
        agent = _StubAgent()
        agent.run_turn(lambda: None)
        assert seen == [agent]

    def test_clear_removes_observers(self):
        events = []
        observe_turns(
            on_start=lambda a: events.append("start"),
            on_end=lambda a: events.append("end"),
        )
        clear_turn_observers()
        _StubAgent().run_turn(lambda: None)
        assert events == []

    def test_observer_registered_mid_turn_does_not_fire_for_that_turn(self):
        """A turn sees the observer set that existed when it began."""
        events = []
        agent = _StubAgent()

        def register_late():
            observe_turns(
                on_start=lambda a: events.append("late-start"),
                on_end=lambda a: events.append("late-end"),
            )

        agent.run_turn(register_late)
        assert events == []


# -------------------------------------------------------------------------
# Busy indicator -- direct
# -------------------------------------------------------------------------


class TestAgentBusyPrimitive:
    def test_starts_clear(self):
        assert is_agent_busy() is False

    def test_set_then_clear(self):
        set_agent_busy(True)
        assert is_agent_busy() is True
        set_agent_busy(False)
        assert is_agent_busy() is False

    def test_clear_is_idempotent(self):
        set_agent_busy(False)
        set_agent_busy(False)
        assert is_agent_busy() is False


# -------------------------------------------------------------------------
# Busy indicator -- driven by a real root-agent turn
# -------------------------------------------------------------------------


def _make_response(content="assistant reply", usage=None):
    """Build a fake LLM response with the attributes process() reads."""
    return type(
        "LLMResponse",
        (object,),
        {
            "message": Message(
                role=ROLE_ASSISTANT,
                content=content,
                reasoning=None,
                finish_reason="stop",
            ),
            "usage": usage,
        },
    )()


def _fresh_context(tmp_path: Path):
    """Create a minimal context file and load it."""
    ctx_path = tmp_path / "2026-07-03_12345.json"
    ctx_path.write_text(
        json.dumps({"role": "system", "content": "test context"}) + "\n"
    )
    return context_from_file(str(ctx_path))


def _make_agent(tmp_path: Path) -> RootAgent:
    return RootAgent(
        model_str="test-model",
        tools=[],
        context=_fresh_context(tmp_path),
        print_info_lines=False,
    )


class TestBusyAcrossRealTurn:
    def test_install_is_idempotent_and_leaves_the_flag_clear(self):
        install_busy_observer()
        install_busy_observer()
        assert is_agent_busy() is False

    def test_busy_during_the_llm_call_and_clear_after(self, tmp_path):
        install_busy_observer()
        agent = _make_agent(tmp_path)
        seen = []

        def record_busy(*args, **kwargs):
            seen.append(is_agent_busy())
            return _make_response()

        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch("wichy.root_agent.root_agent.call", side_effect=record_busy),
        ):
            assert is_agent_busy() is False
            agent.process("hello")

        assert seen == [True], "the turn never appeared busy"
        assert is_agent_busy() is False

    def test_busy_still_set_late_in_the_turn(self, tmp_path):
        """Busy must still be true in the turn's tail, not just during the call.

        Per-call scoping would have cleared the flag by the time the assistant
        response is appended. Sampling there distinguishes the two.
        """
        install_busy_observer()
        agent = _make_agent(tmp_path)
        seen = []
        real_append = agent.context.append

        def append_watching(*args, **kwargs):
            seen.append(is_agent_busy())
            return real_append(*args, **kwargs)

        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch("wichy.root_agent.root_agent.call") as mock_call,
            patch.object(agent.context, "append", side_effect=append_watching),
        ):
            mock_call.return_value = _make_response()
            agent.process("hello")

        assert len(seen) == 2, f"expected the user and assistant appends, saw {seen}"
        assert all(seen), "busy cleared before the end of the turn"

    def test_busy_cleared_when_the_turn_raises(self, tmp_path):
        """A raised turn must not strand the indicator on.

        The busy assertion inside the side effect is what gives this test
        teeth: without it the test passes against code that never sets the
        flag, so it would not gate the fix at all.
        """
        install_busy_observer()
        agent = _make_agent(tmp_path)
        seen = []

        def raise_while_busy(*args, **kwargs):
            seen.append(is_agent_busy())
            raise RuntimeError("backend exploded")

        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch(
                "wichy.root_agent.root_agent.call",
                side_effect=raise_while_busy,
            ),
        ):
            assert is_agent_busy() is False
            with pytest.raises(RuntimeError):
                agent.process("hello")

        assert seen == [True], "the turn never appeared busy"
        assert is_agent_busy() is False

    def test_second_turn_also_sets_busy(self, tmp_path):
        """The flag must not be one-shot: turn two must light up too."""
        install_busy_observer()
        agent = _make_agent(tmp_path)
        seen = []

        def record_busy(*args, **kwargs):
            seen.append(is_agent_busy())
            return _make_response()

        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch("wichy.root_agent.root_agent.call", side_effect=record_busy),
        ):
            agent.process("first")
            agent.process("second")

        assert seen == [True, True]


# -------------------------------------------------------------------------
# Document locks
# -------------------------------------------------------------------------


class TestDocLocks:
    def test_same_slug_returns_same_object(self):
        assert get_doc_lock("a") is get_doc_lock("a")

    def test_different_slugs_get_different_locks(self):
        assert get_doc_lock("a") is not get_doc_lock("b")

    def test_lock_is_reentrant(self):
        """RLock: a caller already holding it may nest without deadlock."""
        lock = get_doc_lock("a")
        assert lock.acquire(timeout=1)
        try:
            assert lock.acquire(timeout=1)
            lock.release()
        finally:
            lock.release()

    def test_reset_state_drops_locks(self):
        first = get_doc_lock("a")
        reset_state()
        assert get_doc_lock("a") is not first


# -------------------------------------------------------------------------
# Versions
# -------------------------------------------------------------------------


class TestDocVersions:
    def test_unknown_slug_is_zero(self):
        assert get_doc_version("nope") == 0

    def test_set_then_get(self):
        set_doc_version("a", 7)
        assert get_doc_version("a") == 7

    def test_clear_drops_the_entry(self):
        set_doc_version("a", 7)
        clear_doc_version("a")
        assert get_doc_version("a") == 0

    def test_slugs_are_independent(self):
        set_doc_version("a", 1)
        set_doc_version("b", 2)
        assert get_doc_version("a") == 1
        assert get_doc_version("b") == 2


# -------------------------------------------------------------------------
# Change queue
# -------------------------------------------------------------------------


class TestAgentChanges:
    def test_queue_keeps_different_blocks(self):
        """Ops for DIFFERENT blocks are independent edits, not repeats."""
        queue_agent_change("a", {"op": "update", "block_id": "blk-1"})
        queue_agent_change("a", {"op": "update", "block_id": "blk-2"})
        assert drain_agent_changes("a") == [
            {"op": "update", "block_id": "blk-1"},
            {"op": "update", "block_id": "blk-2"},
        ]

    def test_drain_clears(self):
        queue_agent_change("a", {"op": "update"})
        drain_agent_changes("a")
        assert drain_agent_changes("a") == []

    def test_drain_is_slug_scoped(self):
        queue_agent_change("a", {"op": "update", "id": "a1"})
        queue_agent_change("b", {"op": "update", "id": "b1"})
        assert drain_agent_changes("a") == [{"op": "update", "id": "a1"}]
        assert drain_agent_changes("b") == [{"op": "update", "id": "b1"}]

    def test_peek_does_not_clear(self):
        queue_agent_change("a", {"op": "update"})
        assert peek_agent_changes("a") == [{"op": "update"}]
        assert peek_agent_changes("a") == [{"op": "update"}]

    def test_peek_returns_a_copy(self):
        """Mutating the result must not reach into the queue."""
        queue_agent_change("a", {"op": "update"})
        peek_agent_changes("a").append({"op": "bogus"})
        assert peek_agent_changes("a") == [{"op": "update"}]

    def test_drain_of_unknown_slug_is_empty(self):
        assert drain_agent_changes("nope") == []

    def test_clear_discards(self):
        queue_agent_change("a", {"op": "update"})
        clear_agent_changes("a")
        assert drain_agent_changes("a") == []


# -------------------------------------------------------------------------
# Rename
# -------------------------------------------------------------------------


class TestRename:
    def test_rename_moves_queued_ops(self):
        """A rename must not drop queued work the browser has not seen."""
        queue_agent_change("old", {"op": "update", "id": "blk-1"})
        rename_document("old", "new", version=3)
        assert drain_agent_changes("old") == []
        assert drain_agent_changes("new") == [{"op": "update", "id": "blk-1"}]

    def test_rename_into_existing_queue_appends(self):
        queue_agent_change("new", {"op": "update", "id": "first"})
        queue_agent_change("old", {"op": "update", "id": "second"})
        rename_document("old", "new", version=1)
        assert [op["id"] for op in drain_agent_changes("new")] == ["first", "second"]

    def test_rename_with_nothing_queued_is_a_noop(self):
        rename_document("old", "new", version=1)
        assert drain_agent_changes("new") == []

    def test_rename_carries_the_version(self):
        set_doc_version("old", 5)
        rename_document("old", "new", version=5)
        assert get_doc_version("new") == 5

    def test_rename_forgets_the_old_version(self):
        """A stale cache under the old slug could satisfy a stale-write check."""
        set_doc_version("old", 5)
        rename_document("old", "new", version=6)
        assert get_doc_version("old") == 0

    def test_rename_reuses_one_lock_object(self):
        """A writer still on the old slug must serialise with one on the new.

        They are writing the same file, so handing out a fresh lock for the new
        slug would let both run their read-check-write at once.
        """
        original = get_doc_lock("old")
        rename_document("old", "new", version=1)
        assert get_doc_lock("new") is original

    def test_rename_creates_a_lock_when_none_existed(self):
        rename_document("old", "new", version=1)
        assert get_doc_lock("new") is not None

    def test_rename_carries_the_injection_mark(self):
        """Otherwise the new name's first real notification is suppressed.

        The mark moves WITH the document: a change under the new name is a change
        to the same document, so a version at or below the last injected one is
        still a duplicate.
        """
        rename_document("old", "new", version=1)
        note_injected("old", 7)
        rename_document("old", "new", version=7)
        assert was_injected("new", 7) is True
        assert was_injected("old", 7) is False


# -------------------------------------------------------------------------
# Injection bookkeeping
# -------------------------------------------------------------------------


class TestInjectionBookkeeping:
    """The record is per slug and per version, and it never goes backwards."""

    def test_a_first_version_is_not_yet_injected(self):
        assert was_injected("a", 1) is False

    def test_a_recorded_version_reads_as_injected(self):
        note_injected("a", 1)
        assert was_injected("a", 1) is True

    def test_a_higher_version_is_not_suppressed(self):
        note_injected("a", 1)
        assert was_injected("a", 2) is False

    def test_a_lower_version_is_suppressed(self):
        note_injected("a", 5)
        assert was_injected("a", 4) is True

    def test_recording_twice_reports_the_second_as_a_repeat(self):
        assert note_injected("a", 3) is True
        assert note_injected("a", 3) is False

    def test_recording_a_lower_version_does_not_lower_the_mark(self):
        note_injected("a", 5)
        assert note_injected("a", 2) is False
        assert was_injected("a", 5) is True

    def test_slugs_are_independent(self):
        note_injected("a", 5)
        assert was_injected("b", 1) is False

    def test_forget_clears_the_mark(self):
        """A reused slug is a different document, and must not look like a repeat."""
        note_injected("a", 5)
        forget_injected("a")
        assert was_injected("a", 1) is False

    def test_forgetting_an_unknown_slug_is_harmless(self):
        forget_injected("never-seen")
        assert was_injected("never-seen", 1) is False

    def test_reset_clears_the_bookkeeping(self):
        note_injected("a", 5)
        reset_state()
        assert was_injected("a", 1) is False


# -------------------------------------------------------------------------
# Pending snapshot -- one consistent read for the poll endpoint
# -------------------------------------------------------------------------


class TestDescribePending:
    def test_returns_changes_version_and_busy(self):
        queue_agent_change("a", {"op": "update", "id": "blk-1"})
        set_doc_version("a", 4)
        set_agent_busy(True)
        assert describe_pending("a") == {
            "changes": [{"op": "update", "id": "blk-1"}],
            "version": 4,
            "agent_busy": True,
        }

    def test_it_peeks_rather_than_draining(self):
        """The ack removes an operation, not the poll.

        A drain would make the poll response its own acknowledgement, so a lost
        response would lose the operation and the browser would never learn a
        version changed.
        """
        queue_agent_change("a", {"op": "update"})
        assert describe_pending("a")["changes"] == [{"op": "update"}]
        # Still there on the next poll.
        assert describe_pending("a")["changes"] == [{"op": "update"}]

    def test_unknown_slug_is_empty_and_idle(self):
        assert describe_pending("nope") == {
            "changes": [],
            "version": 0,
            "agent_busy": False,
        }

    def test_busy_is_read_from_the_live_event(self):
        set_agent_busy(True)
        assert describe_pending("a")["agent_busy"] is True
        set_agent_busy(False)
        assert describe_pending("a")["agent_busy"] is False

    def test_slugs_do_not_leak(self):
        queue_agent_change("a", {"op": "update", "id": "a1"})
        queue_agent_change("b", {"op": "update", "id": "b1"})
        assert describe_pending("a")["changes"] == [{"op": "update", "id": "a1"}]
        assert describe_pending("b")["changes"] == [{"op": "update", "id": "b1"}]


# -------------------------------------------------------------------------
# Installation -- register() must actually wire the observer
# -------------------------------------------------------------------------


class TestRegistrationInstallsTheObserver:
    """Guards the wiring, not just the mechanism.

    Every other test in this file calls install_busy_observer() itself, so they
    would all pass even if `register()` never called it and the indicator were
    dead in the real app. This test goes through the real registration path.
    """

    def test_registering_the_notes_blueprint_installs_the_observer(self):
        from flask import Flask

        from wichy.tools.notes import register as register_notes

        assert is_agent_busy() is False

        app = Flask(__name__)
        register_notes(app)

        # Sampled from an observer registered AFTER the busy one, so the busy
        # observer's start callback has already run when we look.
        seen = []
        observe_turns(
            on_start=lambda a: seen.append(is_agent_busy()),
            on_end=lambda a: seen.append(is_agent_busy()),
        )
        _StubAgent().run_turn(lambda: None)

        # Busy was set at turn start, and the busy observer's own end callback
        # had already run by the time ours did.
        assert seen == [True, False]
        assert is_agent_busy() is False


# ---------------------------------------------------------------------------
# Stage 9: the busy indicator as a count of turns in flight
# ---------------------------------------------------------------------------


class TestBusyReflectsNestedTurns:
    """Busy must stay on while ANY turn is running.

    The flag was one process-wide Event set at turn start. Turns nest -- a
    sub-agent runs inside the outer turn -- so whichever finished first cleared
    it while the other was still working, and the UI went idle mid-turn.
    """

    def test_a_nested_turn_keeps_the_indicator_on(self):
        turn_begun()
        turn_begun()
        assert is_agent_busy() is True
        # The inner turn ends. The outer one is still running.
        turn_ended()
        assert is_agent_busy() is True, "busy cleared while the outer turn ran"
        turn_ended()
        assert is_agent_busy() is False

    def test_three_deep_nesting(self):
        for _ in range(3):
            turn_begun()
        turn_ended()
        turn_ended()
        assert is_agent_busy() is True
        turn_ended()
        assert is_agent_busy() is False

    def test_an_unmatched_end_does_not_drive_the_count_negative(self):
        """A missed start must not make several later turns report idle.

        A bare decrement would go to -1, and the next two turns would climb only
        to 0 and 1 -- so the second of them would already read idle.
        """
        turn_ended()
        turn_ended()
        turn_begun()
        assert is_agent_busy() is True, "the count was left negative"
        turn_ended()
        assert is_agent_busy() is False

    def test_reset_clears_the_count(self):
        turn_begun()
        turn_begun()
        reset_state()
        assert is_agent_busy() is False
        # And a single later turn reads correctly, rather than taking two.
        turn_begun()
        assert is_agent_busy() is True
        turn_ended()
        assert is_agent_busy() is False

    def test_nested_turns_are_counted_through_the_real_observers(self, tmp_path):
        """Driven through actual agent turns, not the primitives.

        A sub-agent's turn runs INSIDE the outer turn, which is what makes the
        count necessary. Both scopes are entered here and the inner one exits
        first, exactly as nesting does.
        """
        clear_turn_observers()
        install_busy_observer()
        agent = _StubAgent()
        sampled = []

        def nested():
            sampled.append(("outer", is_agent_busy()))

            def inner():
                sampled.append(("inner", is_agent_busy()))

            agent.run_turn(inner)
            # The INNER turn has ended. The outer one is still running.
            sampled.append(("outer-after-inner", is_agent_busy()))

        assert is_agent_busy() is False
        agent.run_turn(nested)
        assert is_agent_busy() is False

        assert sampled == [
            ("outer", True),
            ("inner", True),
            ("outer-after-inner", True),
        ], sampled


class TestAStartObserverCannotStickBusy:
    """A BaseException from a start callback must still close the turn.

    The start notification sat OUTSIDE the try, so an exception escaping the
    callback loop skipped the end notification: the count leaked and the
    indicator stayed on for the life of the process.
    """

    def test_a_raising_start_observer_leaves_busy_consistent(self):
        clear_turn_observers()
        install_busy_observer()

        def explode(_agent):
            raise KeyboardInterrupt("interrupted at turn start")

        # Registered AFTER the busy observer, so the busy callback has already
        # run when this one blows up.
        on_turn_started(explode)

        with pytest.raises(KeyboardInterrupt):
            _StubAgent().run_turn(lambda: None)

        assert is_agent_busy() is False, "busy stuck on after a start failure"

    def test_a_normal_observer_exception_is_still_swallowed(self):
        """Only BaseException propagates; an Exception must not break the turn."""
        clear_turn_observers()

        def complain(_agent):
            raise RuntimeError("observer is broken")

        on_turn_started(complain)
        # No raise: the turn runs normally.
        _StubAgent().run_turn(lambda: None)


class TestTheObserverCanBeReinstalled:
    """clear_turn_observers() must not disarm the installer permanently.

    `_installed` was never reset, so after any test or re-setup that cleared the
    observers, install_busy_observer() returned early and the indicator never
    worked again in that process.
    """

    def test_uninstall_then_install_rearms_the_indicator(self, tmp_path):
        clear_turn_observers()
        uninstall_busy_observer()
        install_busy_observer()

        agent = _make_agent(tmp_path)
        seen = []

        def record_busy(*args, **kwargs):
            seen.append(is_agent_busy())
            return _make_response()

        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch("wichy.root_agent.root_agent.call", side_effect=record_busy),
        ):
            agent.process("hello")

        assert seen == [True], "the indicator was not re-armed"
        assert is_agent_busy() is False

    def test_a_plain_clear_disarms_and_uninstall_recovers(self, tmp_path):
        """`clear_turn_observers()` alone leaves this module believing it is
        still subscribed, and a later `install` then no-ops.

        That is the documented contract of the pair, and it is why the recovery
        path exists: `uninstall_busy_observer()` clears AND resets the flag, so
        the next install really registers. Asserted through a real turn, because
        the failure is an ABSENT observer -- nothing observable from the
        primitives alone.
        """
        seen = []

        def record_busy(*args, **kwargs):
            seen.append(is_agent_busy())
            return _make_response()

        agent = _make_agent(tmp_path)

        # install -> clear -> install: the module still thinks it is installed,
        # so the second install registers nothing and the turn is invisible.
        install_busy_observer()
        clear_turn_observers()
        install_busy_observer()
        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch("wichy.root_agent.root_agent.call", side_effect=record_busy),
        ):
            agent.process("hello")
        assert seen == [False], "a plain clear unexpectedly re-armed the observer"

        # The recovery path: uninstall resets the flag, so install re-registers.
        uninstall_busy_observer()
        install_busy_observer()
        seen.clear()
        with (
            patch("wichy.root_agent.root_agent.console.log"),
            patch("wichy.root_agent.root_agent.user_console.print"),
            patch("wichy.root_agent.root_agent.call", side_effect=record_busy),
        ):
            agent.process("hello")
        assert seen == [True], "uninstall then install did not re-arm the observer"
        assert is_agent_busy() is False


class TestTaskAgentTurnsFireObservers:
    """A sub-agent turn is an agent turn.

    `turn_scope` was only on RootAgent.process, so a whole class of turns -- the
    delegated ones the user is waiting on -- was invisible to every observer.
    """

    def test_task_agent_run_is_wrapped_in_a_turn_scope(self):
        """Asserted on the source: constructing a real TaskAgent needs a live
        backend, and the wiring is the thing that was missing."""
        import inspect

        from wichy.tools.task import base as task_base

        source = inspect.getsource(task_base.TaskAgent.run)
        assert "self.turn_scope()" in source
        # Scoped to the processing call, not merely present in the function.
        assert source.index("self.turn_scope()") < source.index("self._process()")

    def test_the_scope_is_used_as_a_context_manager_not_dropped(self):
        """A bare call would build the generator and never enter it."""
        import inspect

        from wichy.tools.task import base as task_base

        source = inspect.getsource(task_base.TaskAgent.run)
        assert "with self.turn_scope():" in source
