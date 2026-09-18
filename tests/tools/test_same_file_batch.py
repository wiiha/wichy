"""Same-file edits in one assistant batch must not clobber each other.

A batch runs on a ThreadPoolExecutor when a message carries more than one tool
call, so two edits of one file interleave their read-modify-write cycles and the
later write is computed from a stale snapshot. Both calls report success and one
edit is silently gone.

A one-shot rendezvous inside the read makes the race deterministic rather than
scheduling-dependent: the proxy blocks after the content is in hand, so both
threads hold the stale snapshot before either writes. Post-fix the first thread
holds the path lock, the rendezvous times out, and the second re-reads.

Note on strength: the same-file race tests here fail pre-fix on every run
(measured 20/20). `test_replace_text_and_write_file_never_splice` is different --
it passes pre-fix too, because a wholesale overwrite loses the race either way,
so it is an outcome check rather than a pre-fix proof.
"""

import builtins
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from wichy.tools.file_safety import _reset_for_tests, file_lock, is_locked
from wichy.tools.insert_lines import InsertLinesTool
from wichy.tools.replace_text import ReplaceTextTool
from wichy.tools.write_file import WriteFileTool

#: Barrier timeout. Keep this SMALL. Pre-fix both parties arrive and it costs
#: nothing, but post-fix the loser always times out, so this value is added to
#: every post-fix run of these tests -- at 2.0s that was 8s for this file
#: alone. Kept comfortably above the rendezvous latency, which is microseconds.
_BARRIER_TIMEOUT_S = 0.5


@pytest.fixture(autouse=True)
def clean_locks():
    """Isolate the process-global lock registry per test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


class _BarrieredRead:
    """File proxy whose read() rendezvous AFTER capturing the content.

    Meeting before the actual read is not enough: the read itself is fast, so
    a thread can finish its whole read-compute-write before the other thread
    reads, and the edits then land anyway. Barriering after the content is in
    hand guarantees both threads hold the stale snapshot before either writes,
    which is what makes the pre-fix failure deterministic.
    """

    def __init__(self, handle, barrier: threading.Barrier, timeout: float):
        self._handle = handle
        self._barrier = barrier
        self._timeout = timeout

    def _sync(self) -> None:
        try:
            self._barrier.wait(timeout=self._timeout)
        except threading.BrokenBarrierError:
            # Must be swallowed. It would otherwise propagate into the tool's
            # `except Exception` and surface as "Failed to read file", failing
            # the post-fix run for the wrong reason.
            pass

    def read(self, *args):
        data = self._handle.read(*args)
        self._sync()
        return data

    def readlines(self, *args):
        data = self._handle.readlines(*args)
        self._sync()
        return data

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def _rendezvous_on_read(monkeypatch, target: str, timeout: float = _BARRIER_TIMEOUT_S):
    """Make the first two read-mode opens of *target* rendezvous post-read.

    Scoped by canonical path and armed one-shot: a process-wide patch would
    also trap this test's own assertion read and hang it.
    """
    key = os.path.realpath(target)
    barrier = threading.Barrier(2)
    real_open = builtins.open
    armed = []

    def rendezvous_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if (
            isinstance(file, (str, os.PathLike))
            and os.path.realpath(str(file)) == key
            and "r" in mode
            and "w" not in mode
            and "a" not in mode
            and len(armed) < 2
        ):
            armed.append(1)
            return _BarrieredRead(handle, barrier, timeout)
        return handle

    monkeypatch.setattr(builtins, "open", rendezvous_open)
    return armed


def test_two_replace_text_same_file_in_one_batch(monkeypatch, tmp_path):
    """Two non-overlapping edits in one batch must both land."""
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")

    _rendezvous_on_read(monkeypatch, str(target))

    tool = ReplaceTextTool()
    edits = [("alpha", "ALPHA"), ("gamma", "GAMMA")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(
                tool.execute,
                file_path=str(target),
                old_content=old,
                new_content=new,
                count=1,
            )
            for old, new in edits
        ]
        results = [f.result(timeout=30) for f in futs]

    for r in results:
        assert "Replaced 1 occurrence(s)" in r, r

    monkeypatch.undo()  # stop intercepting before asserting
    assert target.read_text() == "ALPHA\nbeta\nGAMMA\n"


def test_no_edit_is_skipped_silently(monkeypatch, tmp_path):
    """The silent-loss half of the bug.

    Both calls report success, so the only way to see the loss is the file
    itself. This asserts every edit that claimed success is present.
    """
    target = tmp_path / "f.txt"
    target.write_text("a\nb\nc\nd\n")

    _rendezvous_on_read(monkeypatch, str(target))

    tool = ReplaceTextTool()
    edits = [("a\n", "A\n"), ("c\n", "C\n")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [
            pool.submit(
                tool.execute,
                file_path=str(target),
                old_content=old,
                new_content=new,
                count=1,
            )
            for old, new in edits
        ]
        results = [f.result(timeout=30) for f in futs]

    monkeypatch.undo()

    claimed = [r for r in results if "Replaced 1 occurrence(s)" in r]
    content = target.read_text()
    assert len(claimed) == 2
    assert (
        "A\n" in content and "C\n" in content
    ), f"{len(claimed)} edits claimed success but file is {content!r}"


def test_replace_text_and_insert_lines_same_file(monkeypatch, tmp_path):
    """Cross-tool: both tools must take the SAME lock for the same file."""
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")

    _rendezvous_on_read(monkeypatch, str(target))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_replace = pool.submit(
            ReplaceTextTool().execute,
            file_path=str(target),
            old_content="alpha",
            new_content="ALPHA",
            count=1,
        )
        f_insert = pool.submit(
            InsertLinesTool().execute,
            file_path=str(target),
            offset=0,
            content="HEADER\n",
        )
        results = [f.result(timeout=30) for f in (f_replace, f_insert)]

    monkeypatch.undo()

    assert "Replaced 1 occurrence(s)" in results[0], results[0]
    assert "Inserted content after line 0" in results[1], results[1]

    content = target.read_text()
    assert "HEADER\n" in content, content
    assert "ALPHA" in content, content
    assert "beta" in content and "gamma" in content, content


def test_replace_text_and_write_file_never_splice(monkeypatch, tmp_path):
    """Cross-tool with a non-read-modify-write writer.

    write_file overwrites wholesale, so no ordering can be promised. What must
    hold is that the outcome is one complete version, never a splice of both.
    """
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\ngamma\n")
    written = "REPLACEMENT\n"

    _rendezvous_on_read(monkeypatch, str(target))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_write = pool.submit(
            WriteFileTool().execute, path=str(target), content=written
        )
        f_replace = pool.submit(
            ReplaceTextTool().execute,
            file_path=str(target),
            old_content="alpha",
            new_content="ALPHA",
            count=1,
        )
        results = [f.result(timeout=30) for f in (f_write, f_replace)]

    monkeypatch.undo()

    assert "Successfully wrote" in results[0], results[0]
    content = target.read_text()
    assert content in (written, "ALPHA\nbeta\ngamma\n"), f"spliced output: {content!r}"


def test_lock_is_released_after_a_batch(monkeypatch, tmp_path):
    """A completed batch must not leave the path locked."""
    target = tmp_path / "f.txt"
    target.write_text("alpha\n")

    ReplaceTextTool().execute(
        file_path=str(target), old_content="alpha", new_content="ALPHA", count=1
    )

    assert not is_locked(str(target))


def _locked_by_another_thread(path):
    """Hold the path lock in a background thread until the returned Event is set."""
    release = threading.Event()
    acquired = threading.Event()

    def holder():
        with file_lock(path):
            acquired.set()
            release.wait(timeout=_BARRIER_TIMEOUT_S * 20)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert acquired.wait(timeout=5), "test setup failed to take the lock"
    return release, t


#: Each entry is (tool_factory, kwargs). All three tools edit a caller-supplied path.
_MODIFYING_CALLS = [
    (
        "replace_text",
        lambda: ReplaceTextTool(),
        {"old_content": "alpha", "new_content": "ALPHA", "count": 1},
    ),
    (
        "insert_lines",
        lambda: InsertLinesTool(),
        {"offset": 1, "content": "inserted\n"},
    ),
    ("write_file", lambda: WriteFileTool(), {"content": "REWRITTEN\n"}),
]


@pytest.mark.parametrize("name,factory,kwargs", _MODIFYING_CALLS)
def test_modifying_tool_waits_for_the_path_lock(name, factory, kwargs, tmp_path):
    """A modifying call must not proceed while the path is locked.

    Deterministic without a race: this test holds the lock itself and asserts
    the tool call has not finished. Pre-fix every one of these completes
    immediately, because none of the tools consulted a lock at all.
    """
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\n")

    release, holder = _locked_by_another_thread(str(target))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(
            factory().execute, **{_path_kwarg(name): str(target), **kwargs}
        )
        # Give it long enough that an unlocked implementation would be done.
        with pytest.raises(TimeoutError):
            fut.result(timeout=0.4)
        assert not fut.done(), f"{name} ignored the path lock"
        # Release BEFORE shutting the pool down: shutdown() waits for the
        # blocked call, so releasing afterwards would deadlock until the
        # holder's own timeout.
        release.set()
        fut.result(timeout=30)  # now completes
    finally:
        release.set()
        pool.shutdown(wait=True)
        holder.join(timeout=10)

    assert holder.is_alive() is False


@pytest.mark.parametrize("name,factory,kwargs", _MODIFYING_CALLS)
def test_modifying_tool_completes_once_the_lock_is_released(
    name, factory, kwargs, tmp_path
):
    """Control: the same call succeeds when the lock is free.

    Proves the wait above is the lock and not a bug in the call itself.
    """
    target = tmp_path / "f.txt"
    target.write_text("alpha\nbeta\n")

    result = factory().execute(**{_path_kwarg(name): str(target), **kwargs})

    assert not result.startswith("error:"), result
    assert not is_locked(str(target))


def _path_kwarg(name):
    """replace_text/insert_lines take file_path; write_file takes path."""
    return "path" if name == "write_file" else "file_path"
