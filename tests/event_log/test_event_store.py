"""Tests for the EventStore class."""

import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from wichy.event_log.store import EventStore


@pytest.fixture
def tmp_log():
    """Yield a temporary path for an event log."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "events.jsonl"


class TestEventStoreBasics:
    def test_emit_assigns_monotonic_ids(self, tmp_log):
        store = EventStore(tmp_log, "session-1")
        id1 = store.emit("a", {"k": "v1"})
        id2 = store.emit("b", {"k": "v2"})
        assert id2 == id1 + 1
        store.close()

    def test_events_are_persisted(self, tmp_log):
        store = EventStore(tmp_log, "session-1")
        store.emit("a", {"k": "v"})
        assert store.flush(timeout=2.0)
        store.close()

        lines = tmp_log.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["id"] == 1
        assert entry["event_type"] == "a"
        assert entry["payload"] == {"k": "v"}
        assert entry["session_id"] == "session-1"

    def test_multiple_events_preserved_in_order(self, tmp_log):
        store = EventStore(tmp_log, "session-1")
        for i in range(5):
            store.emit("evt", {"n": i})
        store.flush(timeout=2.0)
        store.close()

        entries = [
            json.loads(line)
            for line in tmp_log.read_text().strip().split("\n")
        ]
        assert [e["id"] for e in entries] == [1, 2, 3, 4, 5]
        assert [e["payload"]["n"] for e in entries] == [0, 1, 2, 3, 4]


class TestEventStoreRotation:
    def test_rotates_when_count_exceeded(self, tmp_log):
        store = EventStore(tmp_log, "session-1", max_count=3)
        store.emit("a", {})
        store.emit("b", {})
        store.emit("c", {})
        store.flush(timeout=2.0)
        # Fourth event triggers rotation.
        store.emit("d", {})
        store.flush(timeout=2.0)
        store.close()

        # Active log should contain only the post-rotation event.
        lines = [line for line in tmp_log.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == 1

        backups = list(tmp_log.parent.glob("events_*.jsonl"))
        assert len(backups) == 1

    def test_rotates_when_size_exceeded(self, tmp_log):
        # Cap just under two large events so the second write triggers rotation.
        store = EventStore(tmp_log, "session-1", max_size_mb=0.003, max_count=1_000_000)
        large_payload = "x" * 2048
        store.emit("a", {"x": large_payload})
        store.flush(timeout=2.0)
        store.emit("b", {"x": large_payload})
        store.flush(timeout=2.0)
        store.close()

        # After rotation the active file may be empty; all events live in the backup.
        backups = list(tmp_log.parent.glob("events_*.jsonl"))
        assert len(backups) == 1
        backup_entries = [
            json.loads(line)
            for line in backups[0].read_text().strip().splitlines()
        ]
        assert [e["id"] for e in backup_entries] == [1, 2]


class TestEventStoreCleanup:
    def test_deletes_old_backups(self, tmp_log):
        store = EventStore(tmp_log, "session-1", max_count=1, retention_days=-1)
        store.emit("a", {})
        store.flush(timeout=2.0)
        # Second event triggers rotation; backup is now old.
        time.sleep(0.2)
        store.emit("b", {})
        store.flush(timeout=2.0)
        store.close()

        backups = list(tmp_log.parent.glob("events_*.jsonl"))
        assert len(backups) == 0


class TestEventStoreFallback:
    def test_fallback_when_writer_thread_dead(self, tmp_log):
        store = EventStore(tmp_log, "session-1")
        store.close(timeout=1.0)

        # After close, the thread is gone; emit should fall back to direct append.
        store.emit("fallback", {"k": "v"})

        lines = [line for line in tmp_log.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["event_type"] == "fallback"


class TestEventStoreConcurrency:
    def test_concurrent_emits_preserve_ordering_per_thread(self, tmp_log):
        store = EventStore(tmp_log, "session-1")

        def worker(start):
            for i in range(10):
                store.emit("evt", {"thread": start, "i": i})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        store.flush(timeout=2.0)
        store.close()

        entries = [
            json.loads(line)
            for line in tmp_log.read_text().strip().split("\n")
        ]
        # 40 events, ids 1..40.
        assert len(entries) == 40
        assert [e["id"] for e in entries] == list(range(1, 41))
