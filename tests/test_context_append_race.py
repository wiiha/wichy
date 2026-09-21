"""append()/add_log() must hold the lock across their disk write.

`append()` and `add_log()` append to memory, release the lock, then write to
disk. `tick()` runs entirely under the same lock. So during the window between
the in-memory append and the disk write, `tick()` can read a file that lacks the
new entry, rebuild `self.context` / `self.logs` from that snapshot, and wipe the
entry out of memory. The entry is on disk but not in memory, which is exactly
backwards from a lost write and is why a file-only assertion cannot see it.

The rendezvous below parks the appending thread inside `_write_line`, at the
point where it has already updated memory but has not yet written. Pre-fix the
lock is free at that moment, so `tick()` runs and the divergence is real; the
assertion fails. Post-fix the appender still holds the lock, so `tick()` waits
for the write to land and then rebuilds from a file that includes the entry;
the assertion passes.

Note the rendezvous point. `_write_line` opens the target directly, so patching
`builtins.open` catches it -- but `tick()` reads and writes via `Path.read_text`
/ `Path.write_text`, which go through `io.open` and are NOT intercepted. The
proxy therefore traps the appender's write and nothing else, which is what makes
the window deterministic instead of scheduling-dependent.

The seed write matters too: `tick()` only rebuilds memory when the file exists.
With no file it takes an early-return branch that leaves memory untouched, the
divergence never appears, and this test would pass against the broken code.
"""

import builtins
import json
import os
import threading

import pytest

from wichy.context.handler import ContextHandler

#: Barrier timeout. Pre-fix both parties arrive and the rendezvous is cheap.
#: Post-fix the appending thread always times out, so this value is added to
#: every post-fix run -- keep it small.
_TIMEOUT_S = 0.5


class _BarrieredWrite:
    """File proxy whose write() rendezvous before handing off to the file.

    The proxy is entered AFTER the real `open()` has already happened, so the
    rendezvous sits between "the file object exists" and "the bytes are
    written" -- precisely the window the lock is supposed to cover.
    """

    def __init__(self, handle, barrier: threading.Barrier, timeout: float):
        self._handle = handle
        self._barrier = barrier
        self._timeout = timeout

    def _sync(self) -> None:
        _rendezvous(self._barrier, self._timeout)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def write(self, data):
        self._sync()
        return self._handle.write(data)


def _rendezvous(barrier: threading.Barrier, timeout: float = _TIMEOUT_S) -> None:
    """Wait at *barrier*, swallowing a broken-barrier error.

    Must never propagate: the caller catches broad exceptions and would report a
    spurious write failure, failing the post-fix run for the wrong reason.
    """
    try:
        barrier.wait(timeout=timeout)
    except threading.BrokenBarrierError:
        pass


def _arm_write_block(
    monkeypatch,
    target,
    armed: threading.Event,
    barrier: threading.Barrier,
    timeout: float = _TIMEOUT_S,
) -> None:
    """Make the first append-mode open of *target* rendezvous before writing.

    One-shot and scoped by canonical path: a process-wide patch would trap the
    test's own reads. `armed` is set before blocking so the test can order
    `tick()` strictly after the appender has updated memory.
    """
    key = os.path.realpath(str(target))
    real_open = builtins.open
    fired = []

    def blocking_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if (
            isinstance(file, (str, os.PathLike))
            and os.path.realpath(str(file)) == key
            and "a" in mode
            and not fired
        ):
            fired.append(1)
            armed.set()
            return _BarrieredWrite(handle, barrier, timeout)
        return handle

    monkeypatch.setattr(builtins, "open", blocking_open)


@pytest.fixture
def temp_contexts_dir(tmp_path, monkeypatch):
    """Point the handler at a throwaway contexts directory."""
    monkeypatch.setattr("wichy.context.handler.settings.contexts_dir", tmp_path)
    return tmp_path


def _seeded_handler() -> ContextHandler:
    """Return a handler whose context file already exists with one message."""
    handler = ContextHandler()
    handler.append({"role": "system", "content": "seed"})
    return handler


def _disk_entries(handler: ContextHandler) -> list[dict]:
    return [
        json.loads(line)
        for line in handler.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestAppendDoesNotRaceTick:
    def test_appended_message_survives_a_concurrent_tick(
        self, temp_contexts_dir, monkeypatch
    ):
        handler = _seeded_handler()

        armed = threading.Event()
        barrier = threading.Barrier(2)
        _arm_write_block(monkeypatch, handler.path, armed, barrier)
        monkeypatch.setattr("wichy.console.user_console.print", lambda *a, **k: None)

        appender = threading.Thread(
            target=handler.append,
            args=({"role": "user", "content": "inflight"},),
        )
        appender.start()

        assert armed.wait(timeout=5), "appender never reached its disk write"
        # The appender is now holding the in-memory entry but has not written it.
        handler.tick()
        _rendezvous(barrier)
        appender.join(timeout=10)
        assert not appender.is_alive()

        monkeypatch.undo()

        contents = [m.get("content") for m in handler.context]
        assert "inflight" in contents, (
            "message was persisted but vanished from self.context: "
            f"memory={contents}"
        )
        assert "seed" in contents

        # _tick proves tick() actually ran and rebuilt from the file rather than
        # being skipped or neutered. A tick() that quietly declines to run would
        # satisfy the membership assertions above while never exercising the
        # rebuild this test exists to check.
        assert all(m.get("_tick") == 1 for m in handler.context), handler.context

        on_disk = [e.get("content") for e in _disk_entries(handler)]
        assert on_disk.count("inflight") == 1
        assert on_disk.count("seed") == 1
        assert all(e.get("_tick") == 1 for e in _disk_entries(handler))


class TestAddLogDoesNotRaceTick:
    def test_logged_entry_survives_a_concurrent_tick(
        self, temp_contexts_dir, monkeypatch
    ):
        handler = _seeded_handler()

        armed = threading.Event()
        barrier = threading.Barrier(2)
        _arm_write_block(monkeypatch, handler.path, armed, barrier)
        monkeypatch.setattr("wichy.console.user_console.print", lambda *a, **k: None)

        appender = threading.Thread(
            target=handler.add_log,
            args=({"event": "inflight-log"},),
        )
        appender.start()

        assert armed.wait(timeout=5), "logger never reached its disk write"
        handler.tick()
        _rendezvous(barrier)
        appender.join(timeout=10)
        assert not appender.is_alive()

        monkeypatch.undo()

        events = [entry.get("event") for entry in handler.logs]
        assert "inflight-log" in events, (
            "log was persisted but vanished from self.logs: " f"memory={events}"
        )

        on_disk = [e.get("event") for e in _disk_entries(handler)]
        assert on_disk.count("inflight-log") == 1


class TestRendezvousHygiene:
    def test_the_proxy_traps_only_the_appenders_write(
        self, temp_contexts_dir, monkeypatch
    ):
        """A second append after the rendezvous is not trapped.

        Guards the one-shot arming: if the proxy kept trapping, the post-fix
        run would block on every later write and the suite would get slow
        rather than red.
        """
        handler = _seeded_handler()

        armed = threading.Event()
        barrier = threading.Barrier(2)
        _arm_write_block(monkeypatch, handler.path, armed, barrier)
        monkeypatch.setattr("wichy.console.user_console.print", lambda *a, **k: None)

        appender = threading.Thread(
            target=handler.append, args=({"role": "user", "content": "first"},)
        )
        appender.start()
        assert armed.wait(timeout=5)
        handler.tick()
        _rendezvous(barrier)
        appender.join(timeout=10)

        handler.append({"role": "user", "content": "second"})
        monkeypatch.undo()

        contents = [m.get("content") for m in handler.context]
        assert contents == ["seed", "first", "second"]
