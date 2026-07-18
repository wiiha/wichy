"""
File-based advisory locking for context files.

Provides cross-platform file locking using lock files and fcntl (Unix) or
msvcrt (Windows). Simple and robust for single-machine coordination between
REPL and web editor.
"""

import os
import time
import io
import fcntl
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator


class FileLockError(Exception):
    """Raised when lock cannot be acquired."""

    pass


class FileLock:
    """
    Advisory file lock using lock files and optional fcntl.

    Creates a sidecar lock file: `<filename>.lock`
    """

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)
        self.lockpath = self.filepath.parent / f".{self.filepath.name}.lock"
        self._lockfile: io.TextIOWrapper | None = None
        self._pid = os.getpid()

    def _acquire_blocking(self, timeout: float = 10.0) -> None:
        """
        Block until the lock is acquired or timeout expires.

        Performs the actual lock-file creation and fcntl locking. Does not
        release on exit — callers are responsible for calling :meth:`release`.

        Args:
            timeout: Maximum seconds to wait for lock (0 = no wait)

        Raises:
            FileLockError: If lock cannot be acquired within timeout
        """
        start_time = time.time()

        while True:
            try:
                # Try to create lock file atomically (O_EXCL ensures no race)
                fd = os.open(self.lockpath, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
                self._lockfile = os.fdopen(fd, "w")
                self._lockfile.write(f"{self._pid}\n")
                self._lockfile.flush()

                # On Unix, also set fcntl lock for extra safety
                try:
                    fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (AttributeError, OSError):
                    # Windows or fcntl not available - lockfile is enough
                    pass

                # Successfully acquired lock
                return

            except FileExistsError:
                # Lock file exists - check if stale
                try:
                    lock_age = time.time() - self.lockpath.stat().st_mtime
                    # Consider lock stale if older than 30 seconds
                    if lock_age > 30:
                        try:
                            os.remove(self.lockpath)
                            continue  # Try again immediately
                        except OSError:
                            pass
                except (OSError, FileNotFoundError):
                    pass

                # Check timeout
                if timeout > 0 and (time.time() - start_time) >= timeout:
                    raise FileLockError(
                        f"Could not acquire lock for {self.filepath} after {timeout}s"
                    )

                # Wait briefly before retrying
                time.sleep(0.1)

    @contextmanager
    def acquire(self, timeout: float = 10.0) -> Iterator[None]:
        """
        Acquire exclusive lock. Blocks until available or timeout expires.

        Args:
            timeout: Maximum seconds to wait for lock (0 = no wait)

        Raises:
            FileLockError: If lock cannot be acquired within timeout
        """
        self._acquire_blocking(timeout)
        try:
            yield
        finally:
            self.release()

    def release(self) -> None:
        """Release the lock if held."""
        if self._lockfile:
            try:
                # Release fcntl lock if we had one
                try:
                    fcntl.flock(self._lockfile, fcntl.LOCK_UN)
                except (AttributeError, OSError):
                    pass
                self._lockfile.close()
            except OSError:
                pass
            self._lockfile = None

        # Remove lock file
        try:
            self.lockpath.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self):
        self._acquire_blocking()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def lock_file(filepath: Path, timeout: float = 10.0):
    """
    Convenience function: acquire lock and return context manager.

    Usage:
        with lock_file(context_path):
            # read/write file safely
    """
    return FileLock(filepath).acquire(timeout=timeout)
