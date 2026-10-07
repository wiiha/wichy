"""Per-path locking and atomic writes for file-editing tools.

Serialises the read-modify-write cycle of ``replace_text``, ``insert_lines``
and ``write_file``, so two edits of the same file in one assistant batch
cannot clobber each other.

A batch runs on a ThreadPoolExecutor, so without this two calls targeting one
file both read the same snapshot, the second write reverts the first, and both
report success. The lock is per canonical path, so edits of different files
still run in parallel.
"""

from __future__ import annotations

import os
import secrets
import stat
import threading
from contextlib import contextmanager
from typing import Generator, Optional

#: One re-entrant lock per canonical path. Entries are reference-counted by
#: _USERS and dropped when the last holder or waiter leaves, so a long-lived
#: server editing many files does not accumulate one lock per path forever.
#: Both maps are guarded by _REGISTRY_LOCK, held only for map mutation, never
#: during I/O.
_LOCKS: dict[str, threading.RLock] = {}
_USERS: dict[str, int] = {}
_REGISTRY_LOCK = threading.Lock()

#: Paths currently locked by *some* thread, reference-counted so that
#: re-entrant nesting does not clear it early. Test-only observability: it lets
#: a test assert that no edit was silently skipped because a lock was held,
#: which is otherwise invisible. Never consulted by the locking path.
_HELD: dict[str, int] = {}

_TMP_PREFIX = ".wichy-tmp-"


def canonical_key(path: str) -> str:
    """Canonical lock key for a path.

    realpath() collapses relative/absolute forms, ".." segments and symlinks
    into one key. It does not require the path to exist, so it works for
    write_file creating a new file. normcase is deliberately NOT used: it is a
    no-op on POSIX.
    """
    return os.path.realpath(path)


@contextmanager
def file_lock(path: str) -> Generator[None, None, None]:
    """Hold the per-path lock for the duration of the block.

    Re-entrant, so a nested call on the same thread cannot self-deadlock.

    The lock object is registered in _LOCKS only while some thread holds or
    awaits it, and removed once the last one leaves. The count is incremented
    under _REGISTRY_LOCK BEFORE acquiring, so a thread that is about to wait is
    always counted -- otherwise the lock could be evicted while a waiter still
    held a reference to it, and a later caller would create a second lock for
    the same path, breaking mutual exclusion.
    """
    key = canonical_key(path)
    with _REGISTRY_LOCK:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        _USERS[key] = _USERS.get(key, 0) + 1
    try:
        with lock:
            with _REGISTRY_LOCK:
                _HELD[key] = _HELD.get(key, 0) + 1
            try:
                yield
            finally:
                with _REGISTRY_LOCK:
                    remaining = _HELD[key] - 1
                    if remaining:
                        _HELD[key] = remaining
                    else:
                        del _HELD[key]
    finally:
        with _REGISTRY_LOCK:
            remaining = _USERS[key] - 1
            if remaining:
                _USERS[key] = remaining
            else:
                del _USERS[key]
                _LOCKS.pop(key, None)


def _create_exclusive(path: str) -> tuple[int, str]:
    """Create a uniquely named file next to path; return (fd, its name).

    Uses O_EXCL with 0o666 so the kernel applies the process umask, matching the
    builtin open(). tempfile.mkstemp would force mode 0o600.
    """
    while True:
        candidate = os.path.join(
            os.path.dirname(path) or ".", _TMP_PREFIX + secrets.token_hex(8)
        )
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        return fd, candidate


def atomic_write(path: str, content: str, encoding: Optional[str] = None) -> None:
    """Write content atomically, preserving an existing file's mode.

    A symlinked path is written THROUGH to the link's final target, leaving the
    link in place. os.replace renames a directory entry, so replacing a link
    with a temp file would swap the link for a regular file and strand the
    target with stale content. The path is resolved once, up front, and every
    later step uses the resolved target.

    The temp file lives in the SAME directory as that target, so os.replace is
    atomic (same filesystem). Missing parent directories are created first,
    which write_file relied on. encoding=None means the platform default,
    matching write_file's previous open(path, "w").

    Note: replacing the inode breaks hard links to the old file and lets a
    writable directory overwrite a read-only file; mode bits are preserved.
    """
    target = os.path.realpath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)

    mode: Optional[int] = None
    try:
        mode = stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        mode = None

    fd, tmp = _create_exclusive(target)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_locked(path: str) -> bool:
    """Whether the lock for path is currently held by any thread. Test-only."""
    with _REGISTRY_LOCK:
        return canonical_key(path) in _HELD


def _reset_for_tests() -> None:
    """Clear the lock registry. Test-only.

    Only resets the maps: a lock still held by a leaked thread is not
    reclaimable from here, and a leaked spinner keeps running.
    """
    with _REGISTRY_LOCK:
        _LOCKS.clear()
        _USERS.clear()
        _HELD.clear()
