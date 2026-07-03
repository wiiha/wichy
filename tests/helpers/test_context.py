"""Tests for the context helper module."""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from wichy.context.handler import (
    ContextHandler,
    _drop_last_n_message_lines,
    context_from_file,
    new_context,
    previous_conversations,
)


@pytest.fixture
def temp_contexts_dir():
    """Create a temporary directory for contexts and patch settings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Patch settings.contexts_dir to use our temp directory
        with patch("wichy.context.handler.settings") as mock_settings:
            mock_settings.contexts_dir = tmp_path
            yield tmp_path


class TestContextHandlerBasics:
    """Test basic ContextHandler operations."""

    def test_init_creates_default_values(self, temp_contexts_dir):
        """Test ContextHandler initializes with default values."""
        ctx = ContextHandler()
        assert ctx.context == []
        assert ctx.logs == []
        assert ctx.custom_suffix == ""
        assert ctx.sub_dir == ""
        assert ctx.start_date == datetime.now().strftime("%Y-%m-%d")
        assert ctx.context_dir == temp_contexts_dir

    def test_init_with_custom_params(self, temp_contexts_dir):
        """Test ContextHandler initializes with custom parameters."""
        ctx = ContextHandler(custom_suffix="test", sub_dir="subdir")
        assert ctx.custom_suffix == "test"
        assert ctx.sub_dir == "subdir"
        expected_dir = temp_contexts_dir / "subdir"
        assert ctx.context_dir == expected_dir
        assert expected_dir.exists()

    def test_len_returns_message_count(self, temp_contexts_dir):
        """Test __len__ returns number of message entries."""
        ctx = ContextHandler()
        assert len(ctx) == 0
        ctx.append({"role": "user", "content": "test1"})
        assert len(ctx) == 1
        ctx.append({"role": "assistant", "content": "test2"})
        assert len(ctx) == 2

    def test_call_returns_messages_only(self, temp_contexts_dir):
        """Test __call__ returns only message entries, excluding logs."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        ctx.add_log({"event": "test"})
        ctx.append({"role": "assistant", "content": "msg2"})

        messages = ctx()
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "msg1"}
        assert messages[1] == {"role": "assistant", "content": "msg2"}

    def test_add_convenience_method(self, temp_contexts_dir):
        """Test add() creates proper message dict."""
        ctx = ContextHandler()
        ctx.add("user", "Hello")
        assert len(ctx) == 1
        assert ctx.context[0] == {"role": "user", "content": "Hello", "_tick": 0}

    def test_append_injects_timestamp_and_type(self, temp_contexts_dir):
        """Test append injects timestamp, type, and _tick if not present."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "test"})

        # Check in-memory context (includes _tick now)
        assert ctx.context[0] == {"role": "user", "content": "test", "_tick": 0}

        # Check file content
        save_path = ctx._gen_save_path()
        assert save_path.exists()
        line = save_path.read_text().strip().split("\n")[0]
        data = json.loads(line)
        assert data["type"] == "message"
        assert "timestamp" in data
        assert data["role"] == "user"
        assert data["content"] == "test"
        assert data["_tick"] == 0

    def test_append_does_not_override_existing_type_and_timestamp(
        self, temp_contexts_dir
    ):
        """Test append respects existing type and timestamp."""
        ctx = ContextHandler()
        ctx.append(
            {
                "role": "user",
                "content": "test",
                "type": "custom",
                "timestamp": "2020-01-01T00:00:00",
            }
        )

        save_path = ctx._gen_save_path()
        line = save_path.read_text().strip().split("\n")[0]
        data = json.loads(line)
        assert data["type"] == "custom"  # Not overridden
        assert data["timestamp"] == "2020-01-01T00:00:00"  # Not overridden


class TestContextHandlerLogs:
    """Test log entry handling."""

    def test_add_log_creates_log_entry(self, temp_contexts_dir):
        """Test add_log creates a log entry with proper structure."""
        ctx = ContextHandler()
        ctx.add_log({"event": "test_event", "value": 42})

        assert len(ctx.logs) == 1
        log_entry = ctx.logs[0]
        assert log_entry["type"] == "log"
        assert log_entry["event"] == "test_event"
        assert log_entry["value"] == 42
        assert "timestamp" in log_entry

        # Log should not appear in message count
        assert len(ctx) == 0

        # Log should be in the file
        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["type"] == "log"

    def test_add_log_forces_type_and_timestamp(self, temp_contexts_dir):
        """Test add_log forces type='log' and sets timestamp, even if provided."""
        ctx = ContextHandler()
        ctx.add_log(
            {
                "event": "test",
                "type": "message",  # Should be overridden
                "timestamp": "2020-01-01T00:00:00",  # Should be overridden
            }
        )

        log_entry = ctx.logs[0]
        assert log_entry["type"] == "log"  # Overridden
        assert log_entry["timestamp"] != "2020-01-01T00:00:00"  # New timestamp set

    def test_logs_not_included_in_call(self, temp_contexts_dir):
        """Test logs are excluded from __call__ and __len__."""
        ctx = ContextHandler()
        for i in range(3):
            ctx.append({"role": "user", "content": f"msg{i}"})
        for i in range(5):
            ctx.add_log({"log_num": i})

        assert len(ctx) == 3
        assert len(ctx()) == 3
        assert len(ctx.logs) == 5

    def test_multiple_logs_preserved_in_file(self, temp_contexts_dir):
        """Test multiple log entries are correctly written to file."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        ctx.add_log({"event": "log1"})
        ctx.add_log({"event": "log2"})
        ctx.append({"role": "assistant", "content": "msg2"})

        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        assert len(lines) == 4

        # Check order is preserved
        entries = [json.loads(line) for line in lines]
        assert entries[0]["type"] == "message"
        assert entries[1]["type"] == "log"
        assert entries[2]["type"] == "log"
        assert entries[3]["type"] == "message"


class TestContextHandlerDrop:
    """Test dropping messages from context."""

    def test_drop_single_message(self, temp_contexts_dir):
        """Test dropping one message."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        ctx.append({"role": "assistant", "content": "msg2"})
        ctx.append({"role": "user", "content": "msg3"})

        ctx.drop(1)

        assert len(ctx) == 2
        assert ctx.context[0] == {"role": "user", "content": "msg1", "_tick": 0}
        assert ctx.context[1] == {"role": "assistant", "content": "msg2", "_tick": 0}

        # Check file was updated
        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_drop_multiple_messages(self, temp_contexts_dir):
        """Test dropping multiple messages."""
        ctx = ContextHandler()
        for i in range(5):
            ctx.append({"role": "user", "content": f"msg{i}"})

        ctx.drop(3)

        assert len(ctx) == 2
        assert ctx.context[0]["content"] == "msg0"
        assert ctx.context[1]["content"] == "msg1"

    def test_drop_with_logs_interleaved(self, temp_contexts_dir):
        """Test dropping messages preserves interleaved logs that are before cutoff."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        ctx.add_log({"event": "log1"})
        ctx.append({"role": "assistant", "content": "msg2"})
        ctx.add_log({"event": "log2"})
        ctx.append({"role": "user", "content": "msg3"})
        ctx.add_log({"event": "log3"})
        ctx.append({"role": "assistant", "content": "msg4"})

        # Drop last 2 messages (msg3 and msg4)
        ctx.drop(2)

        assert len(ctx) == 2
        assert ctx.context[0]["content"] == "msg1"
        assert ctx.context[1]["content"] == "msg2"

        # Check that log1 and log2 are preserved, log3 is dropped (after cutoff)
        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]

        log_entries = [e for e in entries if e.get("type") == "log"]
        assert len(log_entries) == 2
        assert log_entries[0]["event"] == "log1"
        assert log_entries[1]["event"] == "log2"

    def test_drop_zero_or_negative_is_noop(self, temp_contexts_dir):
        """Test drop with n < 1 does nothing."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})

        ctx.drop(0)
        assert len(ctx) == 1

        ctx.drop(-1)
        assert len(ctx) == 1

    def test_drop_raises_if_not_enough_messages(self, temp_contexts_dir):
        """Test drop handles error when trying to drop more messages than exist."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})

        # drop() catches the exception and prints error, does not raise
        ctx.drop(2)  # Should not raise

        # Context should remain unchanged
        assert len(ctx) == 1
        assert ctx.context[0]["content"] == "msg1"

    def test_drop_handles_file_error(self, temp_contexts_dir):
        """Test drop catches and logs file errors without crashing."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})

        # Mock open to raise an error during drop's file write
        with patch("pathlib.Path.write_text", side_effect=OSError("Permission denied")):
            ctx.drop(1)  # Should not raise, should print error

        assert len(ctx) == 1  # Context still has the message (rollback didn't happen)


class TestContextHandlerDelete:
    """Test context file deletion."""

    def test_delete_removes_file(self, temp_contexts_dir):
        """Test delete removes the context file."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "test"})

        save_path = ctx._gen_save_path()
        assert save_path.exists()

        ctx.delete()
        assert not save_path.exists()

    def test_delete_raises_if_file_missing(self, temp_contexts_dir):
        """Test delete raises OSError if file doesn't exist."""
        ctx = ContextHandler(custom_suffix="nonexistent")
        # Don't write anything, so file doesn't exist
        save_path = ctx._gen_save_path()
        assert not save_path.exists()

        with pytest.raises(OSError):
            ctx.delete()


class TestContextHandlerTick:
    """Test the tick feature for incrementing _tick on all entries."""

    def test_tick_increments_all_entries(self, temp_contexts_dir):
        """Test tick() increments _tick on all messages and logs."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        ctx.append({"role": "assistant", "content": "msg2"})
        ctx.add_log({"event": "test_event"})

        # After append, _tick is 0. After tick(), it becomes 1.
        ctx.tick()

        # Check in-memory
        assert ctx.context[0]["_tick"] == 1
        assert ctx.context[1]["_tick"] == 1
        assert ctx.logs[0]["_tick"] == 1

        # Check on disk
        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]
        for entry in entries:
            assert entry["_tick"] == 1

    def test_tick_twice(self, temp_contexts_dir):
        """Test tick() called twice increments _tick to 2."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})

        # After append: _tick = 0
        # After first tick: _tick = 1
        ctx.tick()
        # After second tick: _tick = 2
        ctx.tick()

        assert ctx.context[0]["_tick"] == 2

        # Check on disk
        save_path = ctx._gen_save_path()
        line = save_path.read_text().strip()
        entry = json.loads(line)
        assert entry["_tick"] == 2

    def test_tick_migrates_existing_entries(self, temp_contexts_dir):
        """Test tick() adds _tick=1 to entries that don't have it."""
        # Create a context file without _tick
        context_file = temp_contexts_dir / "test_context.json"
        lines = [
            json.dumps({"role": "user", "content": "msg1"}),
            json.dumps({"role": "assistant", "content": "msg2"}),
        ]
        context_file.write_text("\n".join(lines))

        # Manually set the path for this test
        from wichy.context.handler import context_from_file

        ctx = context_from_file(context_file)

        # After loading from file, entries without tick
        # won't have a tick property until tick() is
        # called at least once.
        # After tick(), they become _tick=1.
        ctx.tick()

        # All entries should now have _tick == 1
        assert ctx.context[0]["_tick"] == 1
        assert ctx.context[1]["_tick"] == 1

        # Check on disk
        lines = context_file.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]
        for entry in entries:
            assert entry["_tick"] == 1

    def test_call_with_tick_true(self, temp_contexts_dir):
        """Test calling context(tick=True) increments _tick."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})

        # After append, _tick is 0. Calling with tick=True increments to 1.
        result = ctx(tick=True)

        assert ctx.context[0]["_tick"] == 1
        # Result should not include _tick (it's stripped by __call__)
        assert result[0] == {"role": "user", "content": "msg1"}

    def test_append_sets_tick_to_0(self, temp_contexts_dir):
        """Test append() sets _tick to 0 on new entries."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "test"})

        assert ctx.context[0]["_tick"] == 0

        # Check on disk
        save_path = ctx._gen_save_path()
        line = save_path.read_text().strip()
        entry = json.loads(line)
        assert entry["_tick"] == 0

    def test_tick_preserves_other_fields(self, temp_contexts_dir):
        """Test tick() preserves other fields like _truncated_from."""
        ctx = ContextHandler()
        ctx.append(
            {"role": "user", "content": "test", "_truncated_from": "original content"}
        )

        # After append, _tick is 0. After tick(), it becomes 1.
        ctx.tick()

        # _truncated_from should still be there
        assert "_truncated_from" in ctx.context[0]
        assert ctx.context[0]["_truncated_from"] == "original content"
        # _tick should be added
        assert ctx.context[0]["_tick"] == 1

        # Check on disk
        save_path = ctx._gen_save_path()
        line = save_path.read_text().strip()
        entry = json.loads(line)
        assert entry["_truncated_from"] == "original content"
        assert entry["_tick"] == 1

    def test_tick_with_no_file(self, temp_contexts_dir):
        """Test tick() handles case where no file exists yet."""
        ctx = ContextHandler()
        # Don't write anything, so file doesn't exist

        # tick() should not raise, just skip file update
        ctx.tick()

        # In-memory should be updated (but it's empty anyway)
        assert ctx.context == []
        assert ctx.logs == []

    def test_tick_strips_tick_from_result(self, temp_contexts_dir):
        """Test that _tick is stripped from __call__ result."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "test"})
        ctx.tick()

        result = ctx()

        assert "_tick" not in result[0]
        assert result[0] == {"role": "user", "content": "test"}

    def test_tick_different_ages(self, temp_contexts_dir):
        """Test that objects added at different times have correct _tick values."""
        ctx = ContextHandler()

        # Add first object
        ctx.append({"role": "user", "content": "msg1"})
        assert ctx.context[0]["_tick"] == 0

        # Tick - first object now at _tick=1
        ctx.tick()
        assert ctx.context[0]["_tick"] == 1

        # Add second object - it starts at _tick=0
        ctx.append({"role": "assistant", "content": "msg2"})
        assert ctx.context[0]["_tick"] == 1
        assert ctx.context[1]["_tick"] == 0

        # Tick again - both increment
        ctx.tick()
        assert ctx.context[0]["_tick"] == 2
        assert ctx.context[1]["_tick"] == 1

        # Check on disk
        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]
        assert entries[0]["_tick"] == 2

    def test_tick_preserves_message_log_order(self, temp_contexts_dir):
        """Test that tick() preserves order of interleaved messages and logs."""
        ctx = ContextHandler()

        # Add message, then log, then message, then log - interleaved
        ctx.append({"role": "user", "content": "msg1"})
        ctx.add_log({"event": "log1"})
        ctx.append({"role": "assistant", "content": "msg2"})
        ctx.add_log({"event": "log2"})

        # Verify initial order on disk
        save_path = ctx._gen_save_path()
        lines = save_path.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]
        assert entries[0]["type"] == "message"
        assert entries[0]["content"] == "msg1"
        assert entries[1]["type"] == "log"
        assert entries[1]["event"] == "log1"
        assert entries[2]["type"] == "message"
        assert entries[2]["content"] == "msg2"
        assert entries[3]["type"] == "log"
        assert entries[3]["event"] == "log2"

        # Tick
        ctx.tick()

        # Verify order is preserved after tick
        lines = save_path.read_text().strip().split("\n")
        entries = [json.loads(line) for line in lines]
        assert entries[0]["type"] == "message"
        assert entries[0]["content"] == "msg1"
        assert entries[0]["_tick"] == 1
        assert entries[1]["type"] == "log"
        assert entries[1]["event"] == "log1"
        assert entries[1]["_tick"] == 1
        assert entries[2]["type"] == "message"
        assert entries[2]["content"] == "msg2"
        assert entries[2]["_tick"] == 1
        assert entries[3]["type"] == "log"
        assert entries[3]["event"] == "log2"
        assert entries[3]["_tick"] == 1


class TestContextHandlerFileOperations:
    """Test file path generation and directory creation."""

    def test_gen_save_path_format(self, temp_contexts_dir):
        """Test _gen_save_path creates correct filename."""
        ctx = ContextHandler(custom_suffix="mysuffix")
        path = ctx._gen_save_path()

        # Should be: <date>_<id>_<suffix>.json
        expected_name = f"{ctx.start_date}_{ctx.id}_mysuffix.json"
        assert path.name == expected_name
        assert path.parent == temp_contexts_dir

    def test_gen_save_path_without_suffix(self, temp_contexts_dir):
        """Test _gen_save_path without suffix."""
        ctx = ContextHandler()
        path = ctx._gen_save_path()
        expected_name = f"{ctx.start_date}_{ctx.id}.json"
        assert path.name == expected_name

    def test_ensure_context_dir_creates_subdir(self, temp_contexts_dir):
        """Test _ensure_context_dir creates subdirectory if specified."""
        ctx = ContextHandler(sub_dir="deep/nested")
        expected_dir = temp_contexts_dir / "deep" / "nested"
        assert expected_dir.exists()
        assert ctx.context_dir == expected_dir

    def test_ensure_context_dir_uses_existing(self, temp_contexts_dir):
        """Test _ensure_context_dir uses existing directory."""
        existing_dir = temp_contexts_dir / "existing"
        existing_dir.mkdir()
        ctx = ContextHandler(sub_dir="existing")
        assert ctx.context_dir == existing_dir


class TestNewContext:
    """Test the new_context() factory function."""

    def test_new_context_returns_context_handler(self, temp_contexts_dir):
        """Test new_context returns a ContextHandler instance."""
        ctx = new_context()
        assert isinstance(ctx, ContextHandler)

    def test_new_context_default_params(self, temp_contexts_dir):
        """Test new_context uses default parameters."""
        ctx = new_context()
        assert ctx.custom_suffix == ""
        assert ctx.sub_dir == ""


class TestContextFromFile:
    """Test loading context from file."""

    def write_sample_context(self, path):
        """Helper to write a sample context file."""
        lines = [
            json.dumps(
                {
                    "role": "system",
                    "content": "sys1",
                    "timestamp": "2024-01-01T00:00:00",
                }
            ),
            json.dumps(
                {"event": "log1", "type": "log", "timestamp": "2024-01-01T00:00:01"}
            ),
            json.dumps(
                {"role": "user", "content": "user1", "timestamp": "2024-01-01T00:00:02"}
            ),
            json.dumps(
                {
                    "role": "assistant",
                    "content": "assist1",
                    "timestamp": "2024-01-01T00:00:03",
                }
            ),
            json.dumps(
                {"event": "log2", "type": "log", "timestamp": "2024-01-01T00:00:04"}
            ),
        ]
        path.write_text("\n".join(lines))

    def test_context_from_file_loads_messages(self, temp_contexts_dir):
        """Test context_from_file loads message entries correctly."""
        context_file = temp_contexts_dir / "test_context.json"
        self.write_sample_context(context_file)

        ctx = context_from_file(context_file)

        assert len(ctx.context) == 3
        assert ctx.context[0] == {"role": "system", "content": "sys1"}
        assert ctx.context[1] == {"role": "user", "content": "user1"}
        assert ctx.context[2] == {"role": "assistant", "content": "assist1"}

    def test_context_from_file_loads_logs(self, temp_contexts_dir):
        """Test context_from_file loads log entries correctly."""
        context_file = temp_contexts_dir / "test_context.json"
        self.write_sample_context(context_file)

        ctx = context_from_file(context_file)

        assert len(ctx.logs) == 2
        assert ctx.logs[0]["event"] == "log1"
        assert ctx.logs[0]["type"] == "log"
        assert ctx.logs[1]["event"] == "log2"

    def test_context_from_file_strips_timestamp_and_type_from_messages(
        self, temp_contexts_dir
    ):
        """Test context_from_file removes timestamp and type from message entries."""
        context_file = temp_contexts_dir / "test_context.json"
        self.write_sample_context(context_file)

        ctx = context_from_file(context_file)

        for msg in ctx.context:
            assert "timestamp" not in msg
            assert "type" not in msg

    def test_context_from_file_resolves_bare_filename(self, temp_contexts_dir):
        """Test context_from_file resolves bare filename against contexts_dir."""
        # Use proper filename format: date_id[_suffix].json
        context_file = temp_contexts_dir / "2024-03-14_12345_bare.json"
        self.write_sample_context(context_file)

        # Pass just the filename, not full path
        ctx = context_from_file("2024-03-14_12345_bare.json")
        assert ctx.context[0]["content"] == "sys1"

    def test_context_from_file_raises_if_not_found(self, temp_contexts_dir):
        """Test context_from_file raises ValueError if file not found."""
        with pytest.raises(ValueError, match="Context file not found"):
            context_from_file("nonexistent.json")

    def test_context_from_file_raises_if_empty(self, temp_contexts_dir):
        """Test context_from_file raises ValueError if file is empty."""
        empty_file = temp_contexts_dir / "empty.json"
        empty_file.touch()

        with pytest.raises(ValueError, match="Context file is empty"):
            context_from_file(empty_file)

    def test_context_from_file_raises_if_no_messages(self, temp_contexts_dir):
        """Test context_from_file raises ValueError if file has no message entries."""
        log_only_file = temp_contexts_dir / "logs_only.json"
        lines = [
            json.dumps({"event": "log1", "type": "log"}),
            json.dumps({"event": "log2", "type": "log"}),
        ]
        log_only_file.write_text("\n".join(lines))

        with pytest.raises(ValueError, match="No message entries found"):
            context_from_file(log_only_file)

    def test_context_from_file_parses_filename_metadata(self, temp_contexts_dir):
        """Test context_from_file extracts date, id, and suffix from filename."""
        context_file = temp_contexts_dir / "2024-03-14_12345_custom.json"
        self.write_sample_context(context_file)

        ctx = context_from_file(context_file)
        assert ctx.start_date == "2024-03-14"
        assert ctx.id == "12345"
        assert ctx.custom_suffix == "custom"

    def test_context_from_file_detects_subdir(self, temp_contexts_dir):
        """Test context_from_file detects subdirectory from file path."""
        subdir = temp_contexts_dir / "subdir"
        subdir.mkdir()
        context_file = subdir / "2024-03-14_12345_subtest.json"
        self.write_sample_context(context_file)

        ctx = context_from_file(context_file)
        assert ctx.sub_dir == "subdir"
        assert ctx.context_dir == subdir


class TestPreviousConversations:
    """Test listing previous conversation files."""

    def test_previous_conversations_returns_filenames(self, temp_contexts_dir):
        """Test previous_conversations returns list of all filenames (no filtering)."""
        # Create some files
        (temp_contexts_dir / "ctx1.json").touch()
        (temp_contexts_dir / "ctx2.json").touch()
        (temp_contexts_dir / "notes.txt").touch()

        files = previous_conversations()
        assert set(files) == {"ctx1.json", "ctx2.json", "notes.txt"}

    def test_previous_conversations_excludes_subdirs(self, temp_contexts_dir):
        """Test previous_conversations only returns files, not directories."""
        (temp_contexts_dir / "file.json").touch()
        (temp_contexts_dir / "subdir").mkdir()

        files = previous_conversations()
        assert "file.json" in files
        assert "subdir" not in files

    def test_previous_conversations_empty_dir(self, temp_contexts_dir):
        """Test previous_conversations returns empty list for empty directory."""
        files = previous_conversations()
        assert files == []


class TestDropLastNMessageLines:
    """Test the _drop_last_n_message_lines helper."""

    def write_lines(self, path, entries):
        """Helper to write JSONL entries to file."""
        lines = [json.dumps(e) for e in entries]
        path.write_text("\n".join(lines))

    def test_drop_last_n_message_lines_basic(self, temp_contexts_dir):
        """Test dropping last n message lines."""
        path = temp_contexts_dir / "test.jsonl"
        self.write_lines(
            path,
            [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "msg2"},
                {"role": "user", "content": "msg3"},
            ],
        )

        _drop_last_n_message_lines(path, 1)

        remaining = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(remaining) == 2
        assert remaining[-1]["content"] == "msg2"

    def test_drop_with_logs_interleaved(self, temp_contexts_dir):
        """Test dropping messages also removes later logs."""
        path = temp_contexts_dir / "test.jsonl"
        self.write_lines(
            path,
            [
                {"role": "user", "content": "msg1"},
                {"event": "log1", "type": "log"},
                {"role": "assistant", "content": "msg2"},
                {"event": "log2", "type": "log"},
                {"role": "user", "content": "msg3"},
                {"event": "log3", "type": "log"},
            ],
        )

        # Drop last 2 messages (msg2 and msg3)
        # Cuts from the first of those messages (msg2 at index 2) onward,
        # removing msg2, log2, msg3, log3.
        _drop_last_n_message_lines(path, 2)

        remaining = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(remaining) == 2
        assert remaining[0] == {"role": "user", "content": "msg1"}
        assert remaining[1] == {"event": "log1", "type": "log"}

    def test_drop_raises_if_not_enough_messages(self, temp_contexts_dir):
        """Test _drop_last_n_message_lines raises if not enough messages."""
        path = temp_contexts_dir / "test.jsonl"
        self.write_lines(
            path,
            [
                {"role": "user", "content": "msg1"},
            ],
        )

        with pytest.raises(
            ValueError, match="Cannot drop 2 message lines; only 1 exist"
        ):
            _drop_last_n_message_lines(path, 2)

    def test_drop_handles_missing_type_field(self, temp_contexts_dir):
        """Test dropping works when entries have no type (defaults to message)."""
        path = temp_contexts_dir / "test.jsonl"
        self.write_lines(
            path,
            [
                {"role": "user", "content": "msg1"},  # No type field -> MESSAGE_TYPE
                {"role": "assistant", "content": "msg2"},
            ],
        )

        _drop_last_n_message_lines(path, 1)

        remaining = [json.loads(line) for line in path.read_text().splitlines()]
        assert len(remaining) == 1
        assert remaining[0]["content"] == "msg1"

    def test_drop_preserves_empty_lines(self, temp_contexts_dir):
        """Test that empty lines are handled correctly."""
        path = temp_contexts_dir / "test.jsonl"
        # Include empty lines in file
        path.write_text(
            "\n".join(
                [
                    json.dumps({"role": "user", "content": "msg1"}),
                    "",
                    json.dumps({"role": "assistant", "content": "msg2"}),
                    "   ",  # whitespace-only line
                    json.dumps({"role": "user", "content": "msg3"}),
                ]
            )
        )

        _drop_last_n_message_lines(path, 1)

        remaining = path.read_text().splitlines()
        # Should have 3 non-empty lines (msg1, empty, msg2)
        assert len([line for line in remaining if line.strip()]) == 2
        assert any(line.strip() == "" for line in remaining)  # Empty line preserved


class TestContextHandlerGetEntries:
    """Tests for ContextHandler.get_entries()."""

    def test_get_entries_reads_disk_order_including_logs(self, temp_contexts_dir):
        """Disk entries are returned in file order, including logs."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        ctx.add_log({"event": "log1"})
        ctx.append({"role": "assistant", "content": "msg2"})

        entries = ctx.get_entries()
        assert len(entries) == 3
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "msg1"
        assert entries[0]["type"] == "message"
        assert entries[0]["_tick"] == 0
        assert entries[1]["type"] == "log"
        assert entries[1]["event"] == "log1"
        assert entries[2]["role"] == "assistant"
        assert entries[2]["content"] == "msg2"

    def test_get_entries_reconstructs_metadata_in_memory_fallback(self, temp_contexts_dir):
        """When file is not written, fallback entries have consistent metadata."""
        ctx = ContextHandler()
        # Mock _write_line to no-op so file is never created
        with patch.object(ctx, "_write_line") as mock_write:
            mock_write.return_value = None
            ctx.append({"role": "user", "content": "msg1"})
            ctx.add_log({"event": "log1"})
            ctx.append({"role": "assistant", "content": "msg2", "reasoning": "because"})

        assert not ctx.path.exists()
        entries = ctx.get_entries()
        assert len(entries) == 3
        # Messages come first in fallback
        assert entries[0]["role"] == "user"
        assert entries[0]["type"] == "message"
        assert "timestamp" in entries[0]
        assert entries[0]["_tick"] == 0
        assert entries[1]["role"] == "assistant"
        assert entries[1]["type"] == "message"
        assert entries[1]["reasoning"] == "because"
        # Logs follow
        assert entries[2]["type"] == "log"
        assert entries[2]["event"] == "log1"

    def test_get_entries_skips_malformed_lines(self, temp_contexts_dir):
        """Malformed JSON lines in the file are skipped."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "msg1"})
        # Corrupt the file by appending a malformed line
        with open(ctx.path, "a") as f:
            f.write("this is not json\n")
        ctx.append({"role": "assistant", "content": "msg2"})

        entries = ctx.get_entries()
        assert len(entries) == 2
        assert entries[0]["content"] == "msg1"
        assert entries[1]["content"] == "msg2"

    def test_get_entries_empty_context(self, temp_contexts_dir):
        """Empty context returns empty list."""
        ctx = ContextHandler()
        assert ctx.get_entries() == []

    def test_get_entries_does_not_mutate_self_context(self, temp_contexts_dir):
        """Memory fallback must not inject metadata into self.context."""
        ctx = ContextHandler()
        with patch.object(ctx, "_write_line") as mock_write:
            mock_write.return_value = None
            ctx.append({"role": "user", "content": "msg1"})

        entries = ctx.get_entries()
        assert entries[0]["type"] == "message"
        # Original in-memory message should remain untouched
        assert "type" not in ctx.context[0]
        assert "timestamp" not in ctx.context[0]


class TestContextHandlerReplaceAllTick:
    """Tests for replace_all() _tick consistency."""

    def test_replace_all_writes_tick_zero(self, temp_contexts_dir):
        """replace_all() should write _tick: 0 like _write_line does."""
        ctx = ContextHandler()
        ctx.append({"role": "user", "content": "original"})
        ctx.replace_all([{"role": "user", "content": "replaced"}])

        entries = ctx.get_entries()
        assert len(entries) == 1
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "replaced"
        assert entries[0]["_tick"] == 0
