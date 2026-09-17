"""Tests for the tool kill registry (src/wichy/tools/kill_registry.py).

Covers:
- register / unregister / finish_call lifecycle
- request_kill marking + was_killed / killed_reason
- killed history cap + race tolerance (finish then kill)
- list_in_flight / call_exists / list_killed_ids
- kill_calls_for_agent (cascade filter)
- kill_all_in_flight
- async-raise delivery into a busy pure-Python thread
- process-group kill of a registered subprocess
- format_tool_killed string
"""

import subprocess
import threading
import time

import pytest

import wichy.tools.kill_registry as kill_registry
from wichy.tools.errors import format_tool_killed
from wichy.tools.kill_registry import (
    KILLED_HISTORY_MAX,
    ToolKilledError,
    call_exists,
    current_agent_id,
    finish_call,
    generate_tool_call_id,
    is_in_flight,
    kill_all_in_flight,
    kill_calls_for_agent,
    killed_reason,
    list_in_flight,
    list_killed_ids,
    pop_agent_id,
    push_agent_id,
    register,
    request_kill,
    unregister,
    was_killed,
    _reset_for_tests,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate the process-global registry for each test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


class TestRegisterLifecycle:
    """register / unregister / finish_call basics."""

    def test_register_creates_in_flight_entry(self):
        record = register("call-1", "bash", {"command": "ls"}, agent_id="root")
        assert is_in_flight("call-1")
        assert record.tool_name == "bash"
        assert record.agent_id == "root"
        assert record.thread_ident == threading.current_thread().ident
        assert not record.kill_requested.is_set()

    def test_unregister_removes_entry(self):
        register("call-1", "glob", {"pattern": "*.py"})
        unregister("call-1")
        assert not is_in_flight("call-1")
        assert call_exists("call-1") is False

    def test_unregister_unknown_id_is_noop(self):
        unregister("never-existed")  # must not raise

    def test_finish_call_without_kill_leaves_no_history(self):
        register("call-1", "glob", {"pattern": "*.py"})
        finish_call("call-1")
        assert not is_in_flight("call-1")
        assert list_killed_ids() == []

    def test_finish_call_after_kill_appends_history(self):
        register("call-1", "glob", {"pattern": "*.py"})
        request_kill("call-1", "too slow")
        finish_call("call-1")
        assert list_killed_ids() == ["call-1"]
        assert call_exists("call-1") is True

    def test_generate_tool_call_id_shape(self):
        call_id = generate_tool_call_id()
        assert call_id.startswith("call_")
        assert len(call_id) > len("call_")


class TestRequestKill:
    """request_kill marking + confirmation lookups."""

    def test_request_kill_marks_record(self):
        register("call-1", "bash", {"command": "sleep 60"})
        assert request_kill("call-1", "too slow") is True
        assert was_killed("call-1") is True
        assert killed_reason("call-1") == "too slow"

    def test_request_kill_unknown_id_returns_false(self):
        assert request_kill("nope") is False
        assert was_killed("nope") is False

    def test_request_kill_without_reason(self):
        register("call-1", "glob", {"pattern": "/**"})
        request_kill("call-1")
        assert killed_reason("call-1") is None

    def test_was_killed_false_for_live_unmarked_call(self):
        register("call-1", "glob", {"pattern": "*.py"})
        assert was_killed("call-1") is False

    def test_kill_reason_survives_history(self):
        register("call-1", "bash", {"command": "find /"})
        request_kill("call-1", "too broad")
        finish_call("call-1")
        assert killed_reason("call-1") == "too broad"

    def test_finish_call_sets_finish_time(self):
        record = register("call-1", "bash", {"command": "ls"})
        assert record.finish_time is None
        finish_call("call-1")
        assert record.finish_time is not None


class TestRaceTolerance:
    """Kill arriving after the call finished (race tolerance)."""

    def test_call_exists_after_history_entry(self):
        register("call-1", "glob", {})
        request_kill("call-1")
        finish_call("call-1")
        # finished + killed -> still "known" for race-tolerant kills
        assert call_exists("call-1") is True
        assert request_kill("call-1", "late") is False  # not in-flight

    def test_history_is_capped_at_50(self):
        for i in range(KILLED_HISTORY_MAX + 10):
            register(f"call-{i}", "glob", {})
            request_kill(f"call-{i}")
            finish_call(f"call-{i}")
        ids = list_killed_ids()
        assert len(ids) == KILLED_HISTORY_MAX
        assert f"call-{KILLED_HISTORY_MAX + 10 - 1}" in ids
        assert "call-0" not in ids

    def test_was_killed_via_history(self):
        register("call-1", "glob", {})
        request_kill("call-1")
        finish_call("call-1")
        # was_killed still true for an id that finished after the kill
        assert was_killed("call-1") is True


class TestListing:
    """list_in_flight snapshots for the API layer."""

    def test_list_in_flight_shape(self):
        register("call-1", "bash", {"command": "sleep 10", "_hidden": "x"})
        entries = list_in_flight()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["tool_call_id"] == "call-1"
        assert entry["tool_name"] == "bash"
        assert entry["agent_id"] == "root"
        assert entry["status"] == "running"
        assert entry["duration_s"] >= 0.0
        # underscore-prefixed kwargs are plumbing, not args
        assert "_hidden" not in entry["arguments"]
        assert entry["arguments"]["command"] == "sleep 10"

    def test_list_in_flight_after_kill_shows_status(self):
        register("call-1", "bash", {})
        request_kill("call-1")
        assert list_in_flight()[0]["status"] == "killed"

    def test_list_in_flight_empty_when_clean(self):
        assert list_in_flight() == []


class TestCascadeHelpers:
    """kill_calls_for_agent + kill_all_in_flight."""

    def test_kill_calls_for_agent_filters_by_agent_id(self):
        register("root-1", "bash", {}, agent_id="root")
        register("task-1", "glob", {}, agent_id="agent-abc")
        register("task-2", "find", {}, agent_id="agent-abc")
        killed = kill_calls_for_agent("agent-abc", "cascading kill")
        assert sorted(killed) == ["task-1", "task-2"]
        assert killed_reason("task-1") == "cascading kill"
        assert was_killed("task-2") is True
        assert was_killed("root-1") is False

    def test_kill_all_in_flight_kills_everything(self):
        register("call-1", "bash", {}, agent_id="root")
        register("call-2", "glob", {}, agent_id="agent-abc")
        killed = kill_all_in_flight("ctrl+c")
        assert len(killed) == 2
        names = {k["tool_name"] for k in killed}
        assert names == {"bash", "glob"}
        assert was_killed("call-1") and was_killed("call-2")

    def test_kill_all_in_flight_empty_registry(self):
        assert kill_all_in_flight() == []


class TestAgentIdStack:
    """Ambient agent_id stack used by nested task-agent registrations."""

    def test_push_pop_roundtrip(self):
        assert current_agent_id() is None
        push_agent_id("agent-abc")
        assert current_agent_id() == "agent-abc"
        push_agent_id("agent-inner")
        assert current_agent_id() == "agent-inner"
        pop_agent_id()
        assert current_agent_id() == "agent-abc"
        pop_agent_id()
        assert current_agent_id() is None

    def test_pop_on_empty_stack_is_noop(self):
        pop_agent_id()  # must not raise


class TestAsyncRaiseDelivery:
    """ToolKilledError delivered into a busy pure-Python thread."""

    def test_async_raise_interrupts_busy_loop(self):
        killed_event = threading.Event()

        def busy_loop():
            # Pure-Python busy loop: async-raise lands at a bytecode
            # boundary within ~250ms ticks. Register from the WORKER
            # thread: in production, validate_and_execute registers on
            # the executing thread.
            register("call-1", "glob", {}, agent_id="root")
            try:
                while True:
                    pass
            except ToolKilledError:
                # Delivery proven by reaching this handler; do not
                # re-raise (would leak an unhandled thread exception).
                killed_event.set()

        worker = threading.Thread(target=busy_loop, daemon=True)
        worker.start()
        # wait until the worker registered itself
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if is_in_flight("call-1"):
                break
            time.sleep(0.01)
        assert is_in_flight("call-1")
        # kill arrives from ANOTHER thread (production: Flask/REPL)
        assert request_kill("call-1", "stop spinning") is True
        assert killed_event.wait(timeout=5), "async-raise never landed"
        worker.join(timeout=5)
        finish_call("call-1")

    def test_async_raise_thread_already_exited(self):
        # A short-lived thread that registers itself, then exits.
        registered = threading.Event()

        def short_lived():
            register("call-2", "glob", {}, agent_id="root")
            registered.set()

        short = threading.Thread(target=short_lived, daemon=True)
        short.start()
        registered.wait(timeout=5)
        short.join(timeout=5)
        assert not short.is_alive()
        # The record's thread is dead; the kill must not raise despite
        # targeting a dead ident.
        assert request_kill("call-2") is True
        finish_call("call-2")


class TestProcessGroupKill:
    """Popen handle + process-group kill (bash path)."""

    def test_kill_process_group_kills_subprocess(self):
        record = register("call-1", "bash", {"command": "sleep 60"})
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        record.set_process(proc)
        assert proc.poll() is None
        assert record.kill_process_group() is True
        proc.wait(timeout=5)
        assert proc.poll() is not None  # killed

    def test_kill_process_group_without_process(self):
        record = register("call-1", "glob", {})
        assert record.kill_process_group() is False

    def test_set_process_after_kill_request_kills_immediately(self):
        record = register("call-1", "bash", {"command": "sleep 60"})
        request_kill("call-1", "race window")
        # spawn AFTER the kill arrived (the race the set_process
        # arm-closing is designed for)
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        record.set_process(proc)
        proc.wait(timeout=5)
        assert proc.poll() is not None

    def test_request_kill_with_process_skips_async_monitor(self):
        # A call with a live process handle must not arm the
        # async-raise backstop (bash gets pgid kill, no nuclear option).
        record = register("call-1", "bash", {"command": "sleep 60"})
        proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
        record.set_process(proc)
        assert request_kill("call-1") is True
        proc.wait(timeout=5)
        assert record.process_killed is True
        finish_call("call-1")


class TestFormatToolKilled:
    """The crafted kill string returned to the LLM."""

    def test_header_and_tool_name(self):
        s = format_tool_killed("bash")
        assert s.startswith("[TOOL_KILLED]")
        assert "Tool: bash" in s

    def test_nudge_content(self):
        s = format_tool_killed("glob")
        assert "force-stopped" in s
        assert "Narrow the scope" in s

    def test_reason_included_when_given(self):
        s = format_tool_killed("bash", "searching the whole filesystem")
        assert "Reason given by user: searching the whole filesystem" in s

    def test_reason_absent_when_not_given(self):
        s = format_tool_killed("bash")
        assert "Reason given by user" not in s

    def test_no_error_prefix(self):
        # A kill is NOT an error string -- it must not start with
        # "error:" (the LLM treats those as failures to retry).
        assert not format_tool_killed("bash").startswith("error:")


class TestSnapshotSafety:
    """Argument snapshots are safe for the API listing."""

    def test_unrepresentable_value_does_not_crash(self):
        register(
            "call-1",
            "weird",
            {"value": type("Unprintable", (), {"__str__": None})()},
        )
        entries = list_in_flight()
        assert entries[0]["arguments"]["value"] == "<unrepresentable>"

    def test_long_values_are_truncated(self):
        register("call-1", "write_file", {"content": "x" * 5000})
        entries = list_in_flight()
        assert len(entries[0]["arguments"]["content"]) == 200


class TestKillDeliveryRegression:
    """Regression tests for the three delivery bugs found during
    development: delayed first shot, self-kill no-monitor, stale
    monitor vs re-registered id."""

    def test_self_kill_never_raises_into_caller(self):
        # A kill requested from the SAME thread as the record must not
        # pop ToolKilledError into the caller: the executing thread
        # observes the kill_requested mark itself.
        register("call-1", "glob", {}, agent_id="root")
        assert request_kill("call-1", "self") is True
        # caller still alive and unharmed; no exception propagated here
        assert was_killed("call-1") is True
        finish_call("call-1")

    def test_self_kill_does_not_arm_monitor(self):
        register("call-1", "glob", {}, agent_id="root")
        request_kill("call-1")
        # give a wrongly-armed monitor time to fire its first shot
        time.sleep(0.6)
        monitor_names = [
            th.name for th in threading.enumerate() if "kill-monitor" in th.name
        ]
        assert monitor_names == [], f"monitors armed on self-kill: {monitor_names}"
        finish_call("call-1")

    def test_stale_monitor_does_not_hit_re_registered_id(self):
        # Thread A registers call-1 and arms a monitor (simulating the
        # external-kill path). Thread A finishes. Then call-1 is
        # re-registered on thread C. The stale monitor from the FIRST
        # record must not fire into C's frame.
        first_loop_done = threading.Event()

        def first_call():
            record = register("call-1", "glob", {}, agent_id="root")
            # Arm the monitor exactly like a cross-thread kill would.
            kill_registry._start_async_raise_monitor(record, 5.0)
            try:
                time.sleep(0.3)  # let the monitor take its delayed first shot
                first_loop_done.set()
            except ToolKilledError:
                # Expected: this thread armed the monitor itself; the
                # first shot lands here after the sleep returns.
                first_loop_done.set()

        t1 = threading.Thread(target=first_call, daemon=True)
        t1.start()
        first_loop_done.wait(timeout=5)
        t1.join(timeout=5)
        # Thread A's record finished; the stale monitor must exit on
        # its next tick via the object-identity check.
        finish_call("call-1")

        # Re-register the same id on a NEW thread that must stay clean.
        clean = threading.Event()

        def second_call():
            register("call-1", "glob", {}, agent_id="root")
            clean.set()
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                # busy-wait: an async-raise would land at a boundary
                if not is_in_flight("call-1"):
                    return
            # survived the full window: no stale exception landed

        t2 = threading.Thread(target=second_call, daemon=True)
        t2.start()
        clean.wait(timeout=5)
        # hold the fresh record in-flight for the stale monitor's ticks
        try:
            t2.join(timeout=2.5)
            assert not t2.is_alive(), "stale monitor raised into fresh thread"
        finally:
            finish_call("call-1")

    def test_monitor_first_shot_is_delayed(self):
        # The first async-raise happens at least one interval AFTER
        # request_kill returns: the caller must be out of the kill
        # machinery first. We assert the target thread is not hit
        # before ~200ms have elapsed.
        hit_time = []
        hit_event = threading.Event()

        def busy_loop():
            register("call-1", "glob", {}, agent_id="root")
            try:
                while True:
                    pass
            except ToolKilledError:
                hit_time.append(time.monotonic())
                hit_event.set()
                # Swallow: delivery timing is already captured above.

        worker = threading.Thread(target=busy_loop, daemon=True)
        worker.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if is_in_flight("call-1"):
                break
            time.sleep(0.01)
        kill_started = time.monotonic()
        request_kill("call-1")
        elapsed_at_return = time.monotonic() - kill_started
        # request_kill must return fast (best-effort, non-blocking):
        # far below the 250ms first-shot delay.
        assert elapsed_at_return < 0.2
        assert hit_event.wait(timeout=5)
        assert (
            hit_time[0] - kill_started >= 0.2
        ), "first shot arrived too early -- delay missing"
        worker.join(timeout=5)
        finish_call("call-1")


class TestReentrantLock:
    """The registry lock is reentrant: the REPL SIGINT handler runs on
    the main thread, which may itself be inside a locked registry op
    when the signal arrives -- a plain Lock would self-deadlock there."""

    def test_kill_all_in_flight_from_inside_locked_section(self):
        register("call-1", "glob", {}, agent_id="root")
        result: dict = {}

        def under_lock():
            with kill_registry._LOCK:
                # Signal handler path: request_kill + kill_all both take
                # _LOCK again on this same thread.
                result["kill"] = kill_registry.kill_all_in_flight("self")

        threading.Thread(target=under_lock, daemon=True).start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "kill" not in result:
            time.sleep(0.01)
        assert "kill" in result, "reentrant lock path deadlocked"
        assert was_killed("call-1")
        finish_call("call-1")

    def test_request_kill_from_inside_locked_section(self):
        register("call-1", "glob", {}, agent_id="root")
        with kill_registry._LOCK:
            assert request_kill("call-1", "self") is True
        finish_call("call-1")
