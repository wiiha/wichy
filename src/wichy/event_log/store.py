"""Event store with queue + single writer thread."""

from __future__ import annotations

import atexit
import json
import queue
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from wichy.config import settings
from wichy.console import user_console

from .paths import agent_events_path, session_events_path
from .schema import EventRecord, now_iso

DEFAULT_MAX_COUNT = 50_000
DEFAULT_MAX_SIZE_MB = 10
DEFAULT_RETENTION_DAYS = 7
DEFAULT_QUEUE_SIZE = 10_000


@dataclass(frozen=True)
class _FlushSentinel:
    """Sentinel used to request a synchronous flush."""

    event: threading.Event


class EventStore:
    """Append-only JSONL event store with a single writer thread."""

    def __init__(
        self,
        path: Path,
        session_id: str,
        *,
        max_count: int | None = None,
        max_size_mb: int | None = None,
        retention_days: int | None = None,
        queue_size: int | None = None,
    ):
        self._path = path
        self._session_id = session_id
        self._max_count = max_count or int(
            getattr(settings, "events_max_count", DEFAULT_MAX_COUNT)
        )
        self._max_size_mb = max_size_mb or int(
            getattr(settings, "events_max_size_mb", DEFAULT_MAX_SIZE_MB)
        )
        self._retention_days = retention_days or int(
            getattr(settings, "events_retention_days", DEFAULT_RETENTION_DAYS)
        )
        self._queue: queue.Queue[EventRecord | _FlushSentinel | None] = queue.Queue(
            maxsize=queue_size
            or int(getattr(settings, "events_queue_size", DEFAULT_QUEUE_SIZE))
        )

        self._next_id = self._compute_next_id()
        self._state_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

        # Ensure directory exists
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._start_thread()

        # Cleanup old backups once at startup.
        self._cleanup_old_backups()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def path(self) -> Path:
        return self._path

    def _compute_next_id(self) -> int:
        """Scan the existing log file to find the next monotonic id."""
        if not self._path.exists():
            return 1
        max_id = 0
        try:
            for raw in self._path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                    max_id = max(max_id, entry.get("id", 0))
                except json.JSONDecodeError:
                    continue
        except OSError as e:
            user_console.print(f"[red]Error reading event log {self._path}:[/red] {e}")
        return max_id + 1

    def _start_thread(self) -> None:
        with self._state_lock:
            if self._started:
                return
            self._thread = threading.Thread(
                target=self._writer_loop,
                name=f"event-log-writer-{self._path.name}",
                daemon=True,
            )
            self._started = True
            self._thread.start()

    def emit(self, event_type: str, payload: dict[str, Any]) -> int:
        """Enqueue an event for durable storage."""
        with self._id_lock:
            event_id = self._next_id
            self._next_id += 1

        record = EventRecord(
            id=event_id,
            timestamp=now_iso(),
            session_id=self._session_id,
            event_type=event_type,
            payload=payload,
        )

        # If writer thread is dead, fall back to synchronous append.
        if not self._thread or not self._thread.is_alive():
            self._direct_append(record)
            return event_id

        try:
            self._queue.put_nowait(record)
        except queue.Full:
            user_console.print(
                f"[yellow]Event log queue full; dropping event {event_type}[/yellow]"
            )
            # Last resort: try synchronous append so the event is not lost.
            self._direct_append(record)

        return event_id

    def _writer_loop(self) -> None:
        """Single consumer thread that drains the queue and appends to disk."""
        pending: list[EventRecord] = []
        while not self._shutdown_event.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if pending:
                    self._write_lines(pending)
                    pending = []
                continue

            if item is None:
                break
            if isinstance(item, _FlushSentinel):
                if pending:
                    self._write_lines(pending)
                    pending = []
                item.event.set()
                continue

            pending.append(item)
            if len(pending) >= 10:
                self._write_lines(pending)
                pending = []

        # Drain remaining items before exit.
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None or isinstance(item, _FlushSentinel):
                if isinstance(item, _FlushSentinel):
                    if pending:
                        self._write_lines(pending)
                        pending = []
                    item.event.set()
                continue
            pending.append(item)

        if pending:
            self._write_lines(pending)

    def _write_lines(self, records: list[EventRecord]) -> None:
        """Atomically append a batch of records and rotate if needed."""
        if not records:
            return

        lines = [record.to_json() + "\n" for record in records]
        with self._file_lock:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.writelines(lines)
                    f.flush()
            except Exception as e:
                user_console.print(
                    f"[red]Error writing event log {self._path}:[/red] {e}"
                )
                return

        self._maybe_rotate()

    def _direct_append(self, record: EventRecord) -> None:
        """Synchronous fallback append when the writer thread is unavailable."""
        with self._file_lock:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(record.to_json() + "\n")
                    f.flush()
            except Exception as e:
                user_console.print(
                    f"[red]Error writing event log {self._path}:[/red] {e}"
                )

    def _maybe_rotate(self) -> None:
        """Rotate the log if count or size thresholds are exceeded."""
        try:
            count = 0
            size = 0
            if self._path.exists():
                text = self._path.read_text(encoding="utf-8")
                size = len(text.encode("utf-8"))
                count = sum(1 for _ in text.splitlines() if _.strip())

            size_limit = self._max_size_mb * 1024 * 1024
            if count >= self._max_count or (size_limit > 0 and size >= size_limit):
                self._rotate()
        except Exception as e:
            user_console.print(f"[red]Error checking event log rotation:[/red] {e}")

    def _rotate(self) -> Path | None:
        """Move the active log to a timestamped backup and reset id counter."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self._path.stem}_{timestamp}{self._path.suffix}"
        backup_path = self._path.with_name(backup_name)
        with self._file_lock:
            if not self._path.exists():
                return None
            try:
                shutil.move(str(self._path), str(backup_path))
            except Exception as e:
                user_console.print(
                    f"[red]Error rotating event log {self._path}:[/red] {e}"
                )
                return None
        with self._id_lock:
            self._next_id = 1
        self._cleanup_old_backups()
        return backup_path

    def _cleanup_old_backups(self) -> None:
        """Delete rotated backups older than the retention period."""
        if not self._path.parent.exists():
            return
        cutoff = datetime.now() - timedelta(days=self._retention_days)
        for candidate in self._path.parent.glob(f"{self._path.stem}_*"):
            if not candidate.is_file():
                continue
            try:
                mtime = datetime.fromtimestamp(candidate.stat().st_mtime)
                if mtime < cutoff:
                    candidate.unlink()
            except Exception as e:
                user_console.print(
                    f"[red]Error cleaning up old event log {candidate}:[/red] {e}"
                )

    def flush(self, timeout: float = 5.0) -> bool:
        """Flush queued events to disk and wait for acknowledgment."""
        if not self._thread or not self._thread.is_alive():
            return True
        sentinel = _FlushSentinel(threading.Event())
        try:
            self._queue.put_nowait(sentinel)
        except queue.Full:
            return False
        return sentinel.event.wait(timeout=timeout)

    def close(self, timeout: float = 5.0) -> None:
        """Shut down the writer thread and flush remaining events."""
        self._shutdown_event.set()
        if self._thread and self._thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                user_console.print(
                    f"[yellow]Event log writer thread did not stop in {timeout}s[/yellow]"
                )
        with self._state_lock:
            self._started = False
            self._thread = None

    def __del__(self) -> None:
        """Attempt clean shutdown if the store is garbage collected."""
        try:
            self.close(timeout=1.0)
        except Exception:
            pass


_root_store: EventStore | None = None
_agent_stores: dict[str, EventStore] = {}
_lock = threading.Lock()


def get_event_store(session_id: str) -> EventStore:
    """Return the root event store for a session, creating it if needed."""
    global _root_store
    with _lock:
        if _root_store is None or _root_store.session_id != session_id:
            if _root_store is not None:
                _root_store.close(timeout=1.0)
            _root_store = EventStore(session_events_path(session_id), session_id)
        return _root_store


def get_agent_event_store(session_id: str, agent_id: str) -> EventStore:
    """Return the event store for a task agent, creating it if needed."""
    global _agent_stores
    with _lock:
        key = f"{session_id}/{agent_id}"
        if key not in _agent_stores:
            _agent_stores[key] = EventStore(
                agent_events_path(session_id, agent_id), session_id
            )
        return _agent_stores[key]


def close_all(timeout: float = 5.0) -> None:
    """Close all open event stores."""
    global _root_store, _agent_stores
    with _lock:
        if _root_store is not None:
            _root_store.close(timeout=timeout)
            _root_store = None
        for store in list(_agent_stores.values()):
            store.close(timeout=timeout)
        _agent_stores.clear()


atexit.register(close_all, timeout=2.0)
