"""
Test cases for the file_safety locking primitives.

Covers canonical key collapsing (relative/absolute, '..', symlinks), per-path
serialisation, independence across paths, re-entrancy, and registry cleanup.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from wichy.tools.file_safety import (
    _reset_for_tests,
    canonical_key,
    file_lock,
    is_locked,
)


@pytest.fixture(autouse=True)
def clean_locks():
    """Isolate the process-global lock registry per test."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_canonical_key_forms_collapse(tmp_path, monkeypatch):
    """Relative/absolute/'..'/symlink forms of one file share a single key."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "d").mkdir()
    p = tmp_path / "d" / "f.txt"
    p.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(p)

    assert canonical_key("d/f.txt") == canonical_key(str(p))
    assert canonical_key("d/../d/f.txt") == canonical_key(str(p))
    assert canonical_key(str(link)) == canonical_key(str(p))


def test_canonical_key_does_not_require_existence(tmp_path):
    """write_file creates new files, so a missing path must still key."""
    missing = str(tmp_path / "does" / "not" / "exist.txt")
    assert canonical_key(missing) == canonical_key(missing)


def test_same_key_serialises(tmp_path):
    """Two holders of one path must not overlap."""
    target = str(tmp_path / "f.txt")
    overlap = []
    inside = threading.Barrier(2)

    def worker():
        with file_lock(target):
            # Bounded wait: if the lock works the second holder never arrives.
            try:
                inside.wait(timeout=1.0)
                overlap.append(True)
            except threading.BrokenBarrierError:
                pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker) for _ in range(2)]
        for f in futs:
            f.result(timeout=30)

    assert overlap == []


def test_different_keys_do_not_block(tmp_path):
    """Distinct paths must not serialise."""
    both_inside = threading.Barrier(2)

    def worker(name):
        with file_lock(str(tmp_path / name)):
            both_inside.wait(timeout=5)  # both hold => not serialised

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker, n) for n in ("a.txt", "b.txt")]
        for f in futs:
            f.result(timeout=30)


def test_lock_is_reentrant(tmp_path):
    """A nested acquire on one thread must not self-deadlock."""
    target = str(tmp_path / "f.txt")
    with file_lock(target):
        with file_lock(target):
            assert is_locked(target)


def test_lock_released_after_exception(tmp_path):
    """The lock must not leak when the guarded body raises."""
    target = str(tmp_path / "f.txt")
    with pytest.raises(ValueError):
        with file_lock(target):
            raise ValueError("boom")

    assert not is_locked(target)
    # And it is immediately reacquirable.
    with file_lock(target):
        assert is_locked(target)


def test_is_locked_false_when_free(tmp_path):
    """Guard the test-only observability helper itself."""
    target = str(tmp_path / "f.txt")
    assert not is_locked(target)
    with file_lock(target):
        assert is_locked(target)
    assert not is_locked(target)


def test_canonicalisation_equivalence_at_tool_level(tmp_path, monkeypatch):
    """Two different spellings of one path must block each other.

    Proves the real invariant (serialisation by canonical path) rather than
    just the helper's string output.
    """
    monkeypatch.chdir(tmp_path)
    real = tmp_path / "f.txt"
    real.write_text("x")
    overlap = []

    def holds(lock_path):
        with file_lock(lock_path):
            time.sleep(0.2)

    def second():
        # Wait for the first holder to be inside, then try the alias spelling.
        while not is_locked(str(real)):
            time.sleep(0.01)
        with file_lock("f.txt"):
            overlap.append(True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(holds, str(real))
        f2 = pool.submit(second)
        for f in (f1, f2):
            f.result(timeout=30)

    assert overlap == [True]


def test_registry_does_not_grow_without_bound(tmp_path):
    """Harness checklist: no unbounded map growth.

    A long-lived server edits many distinct paths; the registry must not keep
    one lock per path forever.
    """
    from wichy.tools import file_safety

    for i in range(200):
        with file_lock(str(tmp_path / f"f{i}.txt")):
            pass

    assert len(file_safety._LOCKS) == 0, "lock registry leaked entries"
    assert len(file_safety._USERS) == 0
    assert len(file_safety._HELD) == 0


def test_registry_entry_survives_nested_use(tmp_path):
    """Eviction must count nesting, not just the outermost exit."""
    from wichy.tools import file_safety

    target = str(tmp_path / "f.txt")
    with file_lock(target):
        with file_lock(target):
            assert len(file_safety._LOCKS) == 1
            assert file_safety._USERS[file_safety.canonical_key(target)] == 2
        # Inner exit must not evict while the outer holder is still in.
        assert len(file_safety._LOCKS) == 1
    assert len(file_safety._LOCKS) == 0


def test_waiter_prevents_eviction_and_keeps_mutual_exclusion(tmp_path):
    """The race the refcount exists to prevent.

    A waiting thread is counted before it acquires. If the lock could be
    evicted while a waiter still references it, a new caller would build a
    SECOND lock for the same path and mutual exclusion would silently break.
    """
    from wichy.tools import file_safety

    target = str(tmp_path / "f.txt")
    key = canonical_key(target)
    inside = threading.Barrier(2)
    overlap = []

    def worker(wait_first):
        if wait_first:
            time.sleep(0.05)
        with file_lock(target):
            try:
                inside.wait(timeout=4.0)
                overlap.append(True)
            except threading.BrokenBarrierError:
                pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker, False), pool.submit(worker, True)]
        for f in futs:
            f.result(timeout=30)

    assert overlap == [], "two holders of one path overlapped"
    assert key not in file_safety._LOCKS
    assert key not in file_safety._USERS
