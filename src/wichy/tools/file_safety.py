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

    mode 0o666 is passed to os.open so the kernel applies the umask.

    O_EXCL rather than tempfile.mkstemp because mkstemp forces mode 0o600, which
    would make a NEW file written by write_file private where the previous
    open(path, "w") honoured the umask. Passing 0o666 to os.open lets the
    kernel apply the umask, exactly like the builtin. Reading the umask to
    correct a mkstemp file instead is not an option: os.umask is process-global
    and setting it in one thread races every other file creation in the batch.
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

    The temp file lives in the SAME directory, so os.replace is atomic (same
    filesystem). Missing parent directories are created first, which write_file
    relied on. encoding=None means the platform default, matching write_file's
    previous open(path, "w").

    Behaviour changes vs open(path, "w"):

    - The inode is replaced (os.replace), so hard links to the old file are
      broken and a symlinked path is replaced by a regular file rather than
      written through.
    - A read-only *file* (mode 0o444) is now overwritten when its directory is
      writable, because os.replace needs only directory permission. Writing to
      a read-only *directory* still fails loudly, as before. This matches the
      other atomic writers in this repo (context/handler.py,
      config/backend_resolver.py).
    - The existing file's permission bits, including a read-only bit, are
      copied onto the new file.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    mode: Optional[int] = None
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        mode = None

    fd, tmp = _create_exclusive(path)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
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
