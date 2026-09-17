"""Process-global registry of in-flight tool calls, with kill support.

This module is the single source of truth for "which tool calls are
currently executing" across the whole wichy process. It exists so that
the user can force-stop ("kill") a running tool call without stopping
the agent or the wichy instance itself -- e.g. a glob search over the
whole filesystem or a long-running bash command.

Design overview:

- ``register(tool_call_id, ...)`` is called by
  ``BaseTool.validate_and_execute`` before ``execute()`` starts and the
  entry is removed when the call finishes, via ``finish_call``.
- ``request_kill(tool_call_id, reason)`` is called from another thread
  (Flask request thread, REPL main thread via SIGINT, or the cascade
  kill). It marks the record first, then delivers the kill by the best
  available mechanism:
  - subprocess tools (bash) register a ``Popen`` handle: the whole
    process group receives SIGKILL, so children like ``find`` do not
    survive; the blocked ``communicate()`` in the tool thread then
    returns promptly.
  - pure-Python tools get ``ToolKilledError`` raised into their thread
    via ``PyThreadState_SetAsyncExc``, re-delivered by a small monitor
    daemon thread every 250ms for up to ~10s (15s for ``task`` tool
    entries, where the backstop is last-resort only).
- The executing thread never kills itself: it observes the
  ``kill_requested`` mark after ``execute()`` returns and swaps the
  result for the kill string built by
  ``wichy.tools.errors.format_tool_killed``.
- Killing an already-finished call is a race-tolerant no-op (the API
  layer answers 200 with ``killed: false``): records are unregistered
  when the call completes, and the last 50 killed ids are kept in a
  bounded history for auditing.

Kill delivery correctness relies on the MARK, not on where the
async-raised exception actually pops: the exception may land in an
inner tool call's exception handler when calls are nested on one
thread (task agents), where the ``was_killed`` check converts it. The
backstop is armed only cross-thread and only for calls without a
process handle.

All registry state is guarded by a single lock. The lock is ONLY held
for dict/list operations, never during I/O, process kills, or
async-raise deliveries.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict, List, Optional

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Cap for the recently-killed history.
KILLED_HISTORY_MAX = 50

#: Cap for the recently-finished (NOT killed) call history. The kill
#: API uses it to answer a race-tolerant 200 (``killed: false``)
#: instead of 404 when a call finished normally between the client's
#: listing and its kill request -- a kill click on a stale UI card is
#: normal operation, not an unknown id.
FINISHED_HISTORY_MAX = 200

#: Interval between async-raise re-deliveries in the monitor loop.
_ASYNC_RAISE_INTERVAL_S = 0.25

#: How long the monitor loop keeps re-delivering before giving up
#: (the exception then stays armed until the next bytecode boundary).
_ASYNC_RAISE_GRACE_S = 10.0

#: Longer grace for ``task`` tool entries: the async-raise backstop is
#: last-resort only there (the cascade kill relies on request_stop +
#: inner-call kills + the agent's fast-exit first, because the raise
#: may otherwise be swallowed by an inner tool's exception handler and
#: the agent loop would continue).
_TASK_ASYNC_RAISE_GRACE_S = 15.0


class ToolKilledError(Exception):
    """Raised into a tool's executing thread when it is force-killed.

    This error is only ever delivered cross-thread via
    ``PyThreadState_SetAsyncExc``. It must never escape
    ``BaseTool.validate_and_execute`` unconverted; the ``was_killed``
    check there swaps it into the kill result string.
    """


# ---------------------------------------------------------------------------
# Kill record
# ---------------------------------------------------------------------------


class KillRecord:
    """One registered in-flight tool call.

    All attributes are written by the executing thread at register time
    (or by the kill machinery under the registry lock); reads are cheap
    and lock-free on purpose -- the agent-side finalize happens on the
    SAME thread that ran ``execute()``.
    """

    def __init__(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        agent_id: str,
        thread: threading.Thread,
        in_task_agent_frame: bool = False,
    ) -> None:
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.agent_id = agent_id
        #: The executing thread object (liveness checks for the kill
        #: backstop; idents get recycled, objects do not).
        self.thread = thread
        self.thread_ident = thread.ident or 0
        #: True while the executing thread is inside a task agent frame
        #: (i.e. this call is the ``task`` tool call itself). Used to
        #: arm the longer backstop grace period.
        self.in_task_agent_frame = in_task_agent_frame
        #: For ``task`` tool calls: the agent_id of the spawned task
        #: agent, attached by the task tool after creating it, so a
        #: kill of this call cascades (request_stop + inner kills).
        self.task_agent_id: Optional[str] = None
        self.start_time = time.time()
        self.finish_time: Optional[float] = None
        #: Set once a kill has been requested. The executing thread
        #: polls this after ``execute()`` returns.
        self.kill_requested = threading.Event()
        self.reason: Optional[str] = None
        #: Subprocess handle for process-group kills (bash tool).
        self._process: Optional[subprocess.Popen[Any]] = None
        #: True once a process-group kill has been attempted.
        self.process_killed = False

    # -- process plumbing (used by subprocess tools like bash) --------------

    def set_process(self, process: subprocess.Popen[Any]) -> None:
        """Register the Popen handle so kills can target the process group.

        Called by the tool right after spawning the subprocess. If a
        kill was already requested, the process group is killed
        immediately (closing the race between spawn and kill, which
        would otherwise orphan the freshly spawned subprocess).
        """
        with _LOCK:
            self._process = process
            already_requested = self.kill_requested.is_set()
        if already_requested:
            self.kill_process_group()

    def attach_task_agent(self, agent_id: str) -> None:
        """Attach the agent_id of the task agent spawned by this call.

        Called by the task tool right after creating the TaskAgent, so
        a later kill of this ``task`` call can cascade into that agent
        (request_stop + kill of its in-flight calls). If a kill was
        already requested, the cascade fires immediately (closing the
        race between spawn and kill).
        """
        with _LOCK:
            self.task_agent_id = agent_id
            already_requested = self.kill_requested.is_set()
            reason = self.reason
        if already_requested:
            # Late-attach recovery: fire the full cascade, not just the
            # stopper. No inner call can exist yet (the agent has not
            # started), but kill_calls_for_agent is cheap and idempotent
            # and makes this path future-proof against reordering.
            _stop_task_agent(agent_id, reason)
            kill_calls_for_agent(agent_id, reason)

    def kill_process_group(self) -> bool:
        """SIGKILL the whole process group of the stored Popen handle.

        Returns True if a kill was attempted, False if there is no
        process handle or the child was already reaped (its pid may
        have been recycled -- killing by pid then risks hitting an
        unrelated process group, so we refuse).
        ``ProcessLookupError`` is swallowed: the race between process
        death and killpg is normal operation.
        """
        with _LOCK:
            process = self._process
        if process is None or process.returncode is not None:
            # No handle, or the tool already waited/reaped the child:
            # nothing to kill, and a reaped pid must never be killed.
            return False
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Already dead / not ours -- nothing more to do.
            pass
        with _LOCK:
            self.process_killed = True
        return True

    # -- introspection ---------------------------------------------------------

    def duration_s(self) -> float:
        """Seconds this call has been running so far."""
        end = self.finish_time if self.finish_time is not None else time.time()
        return end - self.start_time

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-safe dict for API listings."""
        return {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": _safe_args(self.arguments),
            "agent_id": self.agent_id,
            "status": "killed" if self.kill_requested.is_set() else "running",
            "started_at": self.start_time,
            "duration_s": round(self.duration_s(), 2),
        }


# ---------------------------------------------------------------------------
# Registry state
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_REGISTRY: Dict[str, KillRecord] = {}
_KILLED_HISTORY: Deque[Dict[str, Any]] = deque(maxlen=KILLED_HISTORY_MAX)
#: Bounded ring of recently finished (NOT killed) call ids, so the kill
#: API can answer race-tolerant 200s instead of 404 for calls the
#: registry saw finish normally (stale UI kill clicks).
_FINISHED_HISTORY: Deque[str] = deque(maxlen=FINISHED_HISTORY_MAX)
#: Ambient agent-id stack: set by validate_and_execute so nested
#: registrations (task agents running tools on the same thread) inherit
#: the enclosing task agent's agent_id. Only manipulated by the
#: executing thread; push/pop MUST be paired in try/finally, since a
#: leaked entry would mis-attribute every later call on that thread.
_AGENT_ID_STACK = threading.local()

#: Current in-flight record for THIS thread's innermost validate_and_execute.
#: Lets a tool's execute() attach metadata to its own call record (the
#: task tool attaches its spawned task agent; the bash tool attaches its
#: Popen handle) without the hidden-kwargs plumbing reaching pydantic.
_CURRENT_RECORD = threading.local()


def set_current_record(record: Optional[KillRecord]) -> None:
    """Set the current thread's innermost in-flight call record.

    Called by validate_and_execute around execute(). Only the executing
    thread may call this; the previous value is saved/restored by the
    caller so nesting works (a task call contains inner tool calls).
    """
    _CURRENT_RECORD.record = record


def current_record() -> Optional[KillRecord]:
    """Return the current thread's innermost in-flight record, if any."""
    return getattr(_CURRENT_RECORD, "record", None)


def push_agent_id(agent_id: str) -> None:
    """Push the ambient agent_id used as fallback for nested calls."""
    stack = getattr(_AGENT_ID_STACK, "stack", None)
    if stack is None:
        stack = []
        _AGENT_ID_STACK.stack = stack
    stack.append(agent_id)


def pop_agent_id() -> None:
    """Pop the ambient agent_id stack (paired with push_agent_id)."""
    stack = getattr(_AGENT_ID_STACK, "stack", None)
    if stack:
        stack.pop()


def current_agent_id() -> Optional[str]:
    """Return the current ambient agent_id, if any."""
    stack: Optional[List[str]] = getattr(_AGENT_ID_STACK, "stack", None)
    if stack:
        return str(stack[-1])
    return None


def generate_tool_call_id() -> str:
    """Generate a synthetic id for calls that have no LLM-assigned id."""
    return f"call_{uuid.uuid4().hex[:24]}"


# ---------------------------------------------------------------------------
# Register / unregister (executing thread only)
# ---------------------------------------------------------------------------


def register(
    tool_call_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    agent_id: str = "root",
    in_task_agent_frame: bool = False,
) -> KillRecord:
    """Register a call as in-flight (last-wins on tool_call_id).

    Called by ``validate_and_execute`` at the top, on the thread that
    will run ``execute()``. A second register with the same id replaces
    the live record (orphaning the old one's kill mark); ids are
    LLM-assigned or uuid-synthetic, so collisions only occur in tests.
    """
    record = KillRecord(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
        agent_id=agent_id,
        thread=threading.current_thread(),
        in_task_agent_frame=in_task_agent_frame,
    )
    with _LOCK:
        _REGISTRY[record.tool_call_id] = record
    return record


def unregister(tool_call_id: str) -> None:
    """Remove a finished call from the registry (idempotent)."""
    with _LOCK:
        _REGISTRY.pop(tool_call_id, None)


def get_record(tool_call_id: str) -> Optional[KillRecord]:
    """Return the live record for a call id, or None if finished."""
    with _LOCK:
        return _REGISTRY.get(tool_call_id)


# ---------------------------------------------------------------------------
# Cascade hook (task agents)
# ---------------------------------------------------------------------------

#: Optional callback invoked by request_kill when the killed record is a
#: ``task`` tool call. Signature: (agent_id_of_task_agent, reason) -> None.
#: The task module installs it at import time so the registry (stdlib-only)
#: never imports wichy.tools.task (which would create a cycle).
_TASK_AGENT_STOPPER_LOCK = threading.Lock()
_task_agent_stopper: Optional[Any] = None


def set_task_agent_stopper(callback: Optional[Any]) -> None:
    """Install (or clear) the cascade-stop callback for task agents.

    The callback receives the agent_id of the task agent whose enclosing
    ``task`` tool call is being killed. It must stop the agent
    (request_stop + killed fast-exit flag) without raising; exceptions
    are swallowed by the caller because a kill must never fail the
    request path.
    """
    global _task_agent_stopper
    with _TASK_AGENT_STOPPER_LOCK:
        _task_agent_stopper = callback


def _stop_task_agent(agent_id: str, reason: Optional[str]) -> None:
    """Invoke the installed task-agent stopper, swallowing all errors.

    BaseException, not just Exception: request_kill promises to NEVER
    raise, and it runs on threads where a KeyboardInterrupt (REPL
    Ctrl+C path) or SystemExit must not abort the kill delivery.
    """
    with _TASK_AGENT_STOPPER_LOCK:
        callback = _task_agent_stopper
    if callback is None:
        return
    try:
        callback(agent_id, reason)
    except BaseException:
        # The kill mark is already set; a failing cascade callback must
        # never fail the request_kill path.
        pass


# ---------------------------------------------------------------------------
# Kill request (cross-thread)
# ---------------------------------------------------------------------------


def request_kill(tool_call_id: str, reason: Optional[str] = None) -> bool:
    """Request a force-kill of a tool call. Best-effort, non-blocking.

    Returns True if the call was in-flight and got marked (kill armed),
    False if the id was unknown. NEVER raises.

    Delivery is idempotent: a repeat request on an already-marked call
    only updates the reason and never re-arms or re-kills. Delivery
    order for a first request:
    1. Mark the record (so the executing thread's was_killed check
       wins even if every delivery mechanism fails).
    2. If a Popen handle exists: kill the process group (bash path);
       the blocked communicate() returns promptly. Calls WITH a
       process handle never arm the async-raise backstop -- the
       process death (or natural completion) is what unblocks them.
    3. Otherwise (pure-Python): arm the async-raise monitor loop in a
       small daemon thread -- but only when requested from another
       thread; a self-kill is honored via the mark alone.
    """
    with _LOCK:
        record = _REGISTRY.get(tool_call_id)
        if record is None:
            return False
        already_marked = record.kill_requested.is_set()
        record.kill_requested.set()
        if reason is not None and record.reason is None:
            record.reason = reason
        has_process = record._process is not None
        in_task_frame = record.in_task_agent_frame
        task_agent_id = record.task_agent_id
        self_kill = record.thread_ident == threading.current_thread().ident
    # Outside the lock: side effects. Repeat requests do nothing here.
    if already_marked:
        return True
    # Cascade first: killing a `task` call must stop its agent and every
    # in-flight inner call BEFORE the async-raise backstop is armed --
    # the thread unwinds through the inner handlers first, and the
    # backstop (longer grace for task entries) is last-resort only.
    if task_agent_id is not None:
        _stop_task_agent(task_agent_id, reason)
        kill_calls_for_agent(task_agent_id, reason)
    if has_process:
        record.kill_process_group()
    elif not self_kill:
        _start_async_raise_monitor(
            record,
            _TASK_ASYNC_RAISE_GRACE_S if in_task_frame else _ASYNC_RAISE_GRACE_S,
        )
    return True


def kill_all_in_flight(reason: Optional[str] = None) -> List[Dict[str, Any]]:
    """Kill every in-flight call (REPL Ctrl+C path). Returns summaries."""
    with _LOCK:
        ids = list(_REGISTRY.keys())
    killed: List[Dict[str, Any]] = []
    for call_id in ids:
        if request_kill(call_id, reason):
            with _LOCK:
                record = _REGISTRY.get(call_id)
                if record is not None:
                    killed.append(
                        {
                            "tool_call_id": call_id,
                            "tool_name": record.tool_name,
                        }
                    )
    return killed


def kill_calls_for_agent(agent_id: str, reason: Optional[str] = None) -> List[str]:
    """Kill every in-flight call registered under agent_id (cascade).

    Used by the cascade kill of a ``task`` tool call: all inner calls
    under that task agent get killed with the same reason. Returns the
    killed ids.
    """
    with _LOCK:
        ids = [
            record.tool_call_id
            for record in _REGISTRY.values()
            if record.agent_id == agent_id
        ]
    for call_id in ids:
        request_kill(call_id, reason)
    return ids


# ---------------------------------------------------------------------------
# Kill confirmation (executing thread)
# ---------------------------------------------------------------------------


def was_killed(tool_call_id: str) -> bool:
    """True if a kill was requested for this call id.

    ID-BASED query: consults the live record first, then the killed
    history once the record is popped. Used after a call is gone
    (race-tolerant API responses, tests). It must NOT decide a call's
    RESULT string or kill event: ids are LLM-assigned and can recur, so
    the history fallback would misfire on a fresh call whose provider
    re-emitted a previously killed tool_call_id. Result-string and
    kill-event decisions read the record OBJECT instead (see
    ``record_is_killed`` and ``mark_last_call_killed``).
    """
    with _LOCK:
        record = _REGISTRY.get(tool_call_id)
        if record is not None:
            return record.kill_requested.is_set()
        for entry in _KILLED_HISTORY:
            if entry["tool_call_id"] == tool_call_id:
                return True
    return False


def killed_reason(tool_call_id: str) -> Optional[str]:
    """Return the user-given kill reason for a call id, if any."""
    with _LOCK:
        record = _REGISTRY.get(tool_call_id)
        if record is not None:
            return record.reason
        for entry in _KILLED_HISTORY:
            if entry["tool_call_id"] == tool_call_id:
                return entry.get("reason")
    return None


def record_is_killed(record: Optional[KillRecord]) -> bool:
    """Identity-based kill check for a record captured at register time.

    Answers ONLY for the exact record object: it never sees a previous
    call that shared the id, and never flips a fresh successful call
    into a kill because a provider re-emitted a killed tool_call_id.
    This is the API for deciding a call's RESULT string.
    """
    return record is not None and record.kill_requested.is_set()


def record_kill_reason(record: Optional[KillRecord]) -> Optional[str]:
    """User-given kill reason from a record captured at register time."""
    return record.reason if record is not None else None


# Same-thread finalize handoff: finish_call pops the record before
# AgentCore._tool_call inspects the outcome, so an id-based lookup there
# would fall through to the killed history and misfire on re-emitted
# tool_call_ids. validate_and_execute records the outcome on the
# executing thread; _tool_call reads it back right after the call.
_LAST_FINALIZE = threading.local()


def mark_last_call_killed(killed: bool, reason: Optional[str] = None) -> None:
    """Record (executing thread) whether the just-finished call was killed."""
    _LAST_FINALIZE.last = (bool(killed), reason)


def last_call_was_killed() -> bool:
    """True if the calling thread's most recent call was kill-marked."""
    last = getattr(_LAST_FINALIZE, "last", None)
    return bool(last and last[0])


def last_call_kill_reason() -> Optional[str]:
    """Reason recorded by the calling thread's most recent call, if killed."""
    last = getattr(_LAST_FINALIZE, "last", None)
    return last[1] if last else None


# ---------------------------------------------------------------------------
# Unregister-with-history (called by validate_and_execute finally)
# ---------------------------------------------------------------------------


def finish_call(tool_call_id: str) -> None:
    """Unregister a call; if it was killed, append to bounded history.

    MUST be called from the executing thread in a finally block, so a
    record never leaks past its call. Keep the surrounding finally
    block tiny: an async-raise straggler landing inside it would
    escape both validate_and_execute's except handlers.
    """
    with _LOCK:
        record = _REGISTRY.pop(tool_call_id, None)
        if record is not None:
            record.finish_time = time.time()
            if record.kill_requested.is_set():
                _KILLED_HISTORY.append(
                    {
                        "tool_call_id": record.tool_call_id,
                        "tool_name": record.tool_name,
                        "killed_at": record.finish_time,
                        "reason": record.reason,
                    }
                )
            else:
                # Finished normally: remember the id (bounded) so a
                # kill request arriving after completion answers a
                # race-tolerant 200 instead of 404.
                _FINISHED_HISTORY.append(record.tool_call_id)


# ---------------------------------------------------------------------------
# Listing / introspection (API layer)
# ---------------------------------------------------------------------------


def list_in_flight() -> List[Dict[str, Any]]:
    """Snapshot of all in-flight calls (registry-backed API listing).

    Argument stringification happens OUTSIDE the registry lock: a
    misbehaving __str__ must never stall kill requests.
    """
    with _LOCK:
        records = list(_REGISTRY.values())
    return [record.snapshot() for record in records]


def list_killed_ids() -> List[str]:
    """The last (up to 50) killed call ids, oldest first."""
    with _LOCK:
        return [entry["tool_call_id"] for entry in _KILLED_HISTORY]


def call_exists(tool_call_id: str) -> bool:
    """True if the id is in-flight, killed (history), or recently
    finished normally (bounded ring).

    Used by the API to distinguish "unknown id" (404) from
    "already finished" (200, killed: false).
    """
    with _LOCK:
        if tool_call_id in _REGISTRY:
            return True
        for entry in _KILLED_HISTORY:
            if entry["tool_call_id"] == tool_call_id:
                return True
        return tool_call_id in _FINISHED_HISTORY


def is_in_flight(tool_call_id: str) -> bool:
    """True if the id is currently in-flight."""
    with _LOCK:
        return tool_call_id in _REGISTRY


# ---------------------------------------------------------------------------
# Async thread interruption (the backstop for pure-Python tools)
# ---------------------------------------------------------------------------


def _async_raise(thread_ident: int, exc: type[BaseException]) -> bool:
    """Raise ``exc`` inside the thread identified by ``thread_ident``.

    Uses ``PyThreadState_SetAsyncExc``: the exception is delivered at
    the next bytecode boundary of the target thread. Returns True if
    the call succeeded.

    IMPORTANT: this is the nuclear option. The delivered exception may
    land anywhere in the target thread's Python frames -- including an
    inner tool call's exception handler (task-agent nesting on one
    thread), where it gets converted to a kill-string result and the
    agent loop would continue. Kill correctness therefore relies on
    the kill_requested MARK first and the was_killed swap in
    validate_and_execute, not on where exactly this exception pops.

    Never call with the CURRENT thread's ident: a self-interrupt would
    pop inside the caller's own frame (e.g. the kill machinery)
    instead of inside execute(). The executing thread observes the
    kill_requested mark instead.
    """
    if not thread_ident:
        return False
    if thread_ident == threading.get_ident():
        return False
    try:
        result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_ident), ctypes.py_object(exc)
        )
        return bool(result == 1)
    except (SystemError, RuntimeError, TypeError, ValueError):
        # Thread may have exited between our check and the call.
        return False


def _start_async_raise_monitor(record: KillRecord, grace_s: float) -> None:
    """Spawn the monitor daemon that re-delivers async-raise until the
    call finishes or the grace period expires.

    The first delivery is delayed by one interval: the request_kill
    caller may share the thread with the record (self-kill case) and
    must be out of the request_kill frame before the exception lands,
    otherwise it pops inside our own kill machinery instead of inside
    execute().

    The monitor is bound to the record OBJECT, not the call id: ids
    get re-used (synthetic ids, test resets), and a stale monitor must
    never fire into a fresh record's thread. A stale monitor finds its
    record gone from the registry and exits without raising. The
    target thread's liveness is also checked per delivery: idents are
    recycled, thread objects are not.
    """

    def _monitor() -> None:
        deadline = time.monotonic() + grace_s
        time.sleep(_ASYNC_RAISE_INTERVAL_S)
        while time.monotonic() < deadline:
            with _LOCK:
                live = _REGISTRY.get(record.tool_call_id)
                if live is not record:
                    # Call finished (or id re-registered): done.
                    return
            if not record.thread.is_alive():
                return
            if record.thread_ident == threading.get_ident():
                # Executing thread requested its own kill: it observes
                # the kill_requested mark itself; never raise into it.
                return
            if not _async_raise(record.thread_ident, ToolKilledError):
                return
            time.sleep(_ASYNC_RAISE_INTERVAL_S)

    try:
        thread = threading.Thread(
            target=_monitor, name=f"kill-monitor-{record.tool_call_id}", daemon=True
        )
        thread.start()
    except RuntimeError:
        # Thread exhaustion: correctness does not depend on delivery
        # (the kill mark is already set); never raise out of here.
        pass


# ---------------------------------------------------------------------------
# REPL interrupt guard (Ctrl+C while the agent processes input)
# ---------------------------------------------------------------------------


def repl_interrupt_guard(
    on_kill: Optional[Any] = None,
) -> Any:
    """Context manager: SIGINT while tools run kills them instead of
    interrupting the whole turn.

    Installed around ``root_agent.process()`` in the REPL: Ctrl+C is the
    single user's only way to say "stop what you're doing" while the
    agent runs a long tool call (the motivating case: a whole-filesystem
    ``find``). The handler:

    1. If any tool call is in flight: kill ALL of them and swallow the
       signal (the killed calls return crafted kill strings and the
       agent continues its turn). Never raises.
    2. If none are in flight: re-raise KeyboardInterrupt inside the
       handler, preserving the pre-existing REPL behavior (abandon the
       turn; the existing ``except KeyboardInterrupt`` catches it).

    The previous handler is always restored on exit (prompt_toolkit and
    other libraries also install SIGINT handlers; the guard is
    per-process()-call and must not leak). Signal handlers can only be
    installed from the main thread; using this guard off the main thread
    degrades to a no-op that still runs the body.

    NOTE: this only works where the terminal generates SIGINT at all
    (ordinary terminals, ``stty isig``). IDE debug terminals commonly
    run raw mode (``-isig``), where Ctrl+C becomes a stdin byte instead
    of a signal and no handler can see it -- the UI Kill buttons
    (chat / context editor, via the registry-backed API) are the
    fallback there.
    """

    import contextlib
    import signal

    @contextlib.contextmanager
    def _guard():
        try:
            previous = signal.getsignal(signal.SIGINT)
        except ValueError:
            # Not on the main thread: nothing to install.
            yield
            return

        def _handle_sigint(signum, frame):  # noqa: ANN001
            try:
                killed = kill_all_in_flight(reason="REPL interrupt")
            except BaseException:
                killed = []
            if killed:
                if on_kill is not None:
                    try:
                        on_kill(killed)
                    except BaseException:
                        pass
                # Swallow: the agent keeps running with kill results.
                return
            # No tools running: old behavior -- interrupt the turn.
            raise KeyboardInterrupt

        try:
            signal.signal(signal.SIGINT, _handle_sigint)
        except ValueError:
            # Race: left the main thread between getsignal and signal().
            yield
            return
        try:
            yield
        finally:
            try:
                signal.signal(signal.SIGINT, previous)
            except ValueError:
                pass

    return _guard()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_args(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow copy of args safe for JSON serialization + size caps."""
    safe: Dict[str, Any] = {}
    for key, value in arguments.items():
        # Internal kwargs (leading underscore) are plumbing, not args.
        if str(key).startswith("_"):
            continue
        try:
            text = str(value)
        except Exception:
            text = "<unrepresentable>"
        safe[str(key)] = text[:200]
    return safe


# ---------------------------------------------------------------------------
# Test support
# ---------------------------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear all registry state. Test-only.

    Also clears the ambient agent-id stack and current-record slot of
    the CALLING thread (thread-locals of other threads cannot be
    reached from here; tests must not leak pushes on foreign threads).
    The installed task-agent stopper is intentionally left in place:
    it is module behavior, not per-test state.
    """
    with _LOCK:
        _REGISTRY.clear()
        _KILLED_HISTORY.clear()
        _FINISHED_HISTORY.clear()
    if getattr(_AGENT_ID_STACK, "stack", None):
        _AGENT_ID_STACK.stack = []
    if getattr(_CURRENT_RECORD, "record", None) is not None:
        _CURRENT_RECORD.record = None
