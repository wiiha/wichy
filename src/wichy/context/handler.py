"""
ContextHandler - manages a single conversation's context as a JSONL file.

Provides file change watching for live sync between REPL and web editor.
Writes are atomic (temp + rename) to avoid corruption.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from wichy.config import settings
from wichy.console import user_console
from wichy.constants import CONTEXT_FILE_EXT, LOG_TYPE, MESSAGE_TYPE
from wichy.helpers.string import truncate_to_len


class ContextHandler:
    """
    Manages a single conversation's context, persisting it as a JSONL file.

    The JSONL file may contain two kinds of entries:

    - ``type="message"``: LLM conversation turns stored in ``self.context``.
      Returned by ``__call__`` and counted by ``__len__``.
    - ``type="log"``: Arbitrary session metadata stored in ``self.logs``.
      Persisted to the same file but never included in the LLM context and
      invisible to ``self.context`` / ``__len__``.
    """

    def __init__(self, custom_suffix="", sub_dir=""):
        """
        Initialize a new ContextHandler instance.

        Args:
            custom_suffix (str): Custom suffix appended to the context file name.
            sub_dir (str): Subdirectory under contexts_dir to store the file in.

        Attributes:
            context (list): Message-type entries; sent to the LLM.
            logs (list): Log-type entries; persisted but never sent to the LLM.
            id (str): Time-based unique identifier for this context.
            start_date (str): Creation date in ``YYYY-MM-DD`` format.
        """
        self.context = []
        self.logs = []
        self.id = str(time.time()).split(".")[0]
        self.start_date = datetime.now().strftime("%Y-%m-%d")
        self.custom_suffix = custom_suffix
        self.sub_dir = sub_dir
        self._ensure_context_dir()

        # File path and change tracking
        self._path = self._gen_save_path()
        self._file_mtime = None

        # Thread safety
        self._lock = threading.RLock()

        # Background watching
        self._watch_thread = None
        self._watch_active = False
        self._watch_interval = 2.0

    @property
    def path(self) -> Path:
        """Return the path to the context file."""
        result: Path = self._path
        return result

    def _ensure_context_dir(self):
        """Create the context storage directory (and any sub_dir) if missing."""
        self.context_dir = settings.contexts_dir
        if self.sub_dir:
            self.context_dir = self.context_dir / self.sub_dir
        os.makedirs(self.context_dir, exist_ok=True)

    def __len__(self):
        """Return the number of message entries (log entries excluded)."""
        with self._lock:
            return len(self.context)

    def __call__(self, tick: bool = False):
        """
        Return the message context as a list (log entries excluded).

        Strips internal metadata fields (like `_truncated_from`) that should
        not be sent to the LLM.

        Args:
            tick (bool): If True, increment _tick on all entries (default False).
        """
        if tick:
            self.tick()
        with self._lock:
            result = []
            for msg in self.context:
                # Create a copy without internal metadata fields or reasoning
                clean_msg = {
                    k: v
                    for k, v in msg.items()
                    if not k.startswith("_") and k != "reasoning"
                }
                result.append(clean_msg)
            return result

    def tick(self):
        """Increment _tick on every entry by 1. Persists to disk."""
        with self._lock:
            # Skip file update if file doesn't exist yet
            if not self._path.exists():
                # Still update in-memory even if no file
                for msg in self.context:
                    msg["_tick"] = msg.get("_tick", 0) + 1
                for log in self.logs:
                    log["_tick"] = log.get("_tick", 0) + 1
                return

            # Read file, update _tick, rebuild in-memory lists, write back
            lines = self._path.read_text(encoding="utf-8").splitlines()
            entries = []
            for line in lines:
                raw = line.strip()
                if not raw:
                    continue
                entry = json.loads(raw)
                entry["_tick"] = entry.get("_tick", 0) + 1
                entries.append(entry)

            # Rebuild in-memory lists from updated entries
            self.context = []
            self.logs = []
            for entry in entries:
                entry_type = entry.get("type", MESSAGE_TYPE)
                if entry_type == MESSAGE_TYPE:
                    # Strip type and timestamp for in-memory (they're persisted on disk)
                    msg = {
                        k: v for k, v in entry.items() if k not in ("type", "timestamp")
                    }
                    self.context.append(msg)
                else:
                    self.logs.append(entry)

            # Write back to file
            temp_path = self._path.with_suffix(".tmp")
            temp_path.write_text(
                "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
            )
            temp_path.replace(self._path)
            self._file_mtime = self._path.stat().st_mtime

        self._file_mtime = self._path.stat().st_mtime

    def append(self, new_object):
        """
        Append a message dict to the in-memory context and persist it.

        A ``type`` and ``timestamp`` field are injected into the persisted copy
        if not already present; the original dict is not mutated.

        Args:
            new_object (dict): Must contain at least ``role`` and ``content`` keys.
        """
        with self._lock:
            new_object.setdefault("_tick", 0)
            self.context.append(new_object)
        self._write_line(new_object, entry_type=MESSAGE_TYPE)

    def add(self, role, content):
        """
        Convenience wrapper: create a message dict and call :meth:`append`.

        Args:
            role (str): The message role (e.g. ``"user"``, ``"assistant"``).
            content (str): The message content.
        """
        self.append({"role": role, "content": content})

    def steer(self, role: str, content: str) -> None:
        """
        Inject a mid-flight message into the conversation context.

        This is the same as :meth:`add` semantically but communicates intent:
        the message is injected externally while the agent is still running,
        and will be picked up on the *next* LLM call boundary.

        Thread-safe (uses the same :class:`threading.RLock` as all other
        mutation methods).

        Args:
            role (str): The message role (e.g. ``"user"``, ``"system"``).
            content (str): The message content.
        """
        if content is None or content.strip() == "":
            return

        from wichy.console import user_console

        user_console.print(
            f"[italic]steer injected ({role}): {truncate_to_len(text=content, new_len=80, suffix='...')}[/italic]"
        )
        self.add(role=role, content=content)

    def add_log(self, data: dict):
        """
        Persist a log entry without adding it to the LLM context.

        The entry is saved to the same JSONL file with ``type="log"`` and kept
        in ``self.logs`` for in-process inspection, but is never included in
        ``self.context`` and therefore never sent to the LLM.

        Args:
            data (dict): Arbitrary data to log. ``"type"`` is always forced to
                ``LOG_TYPE``; ``"timestamp"`` is always set to the current ISO
                time.  Both keys overwrite any values already present in *data*.
        """
        log_object = {**data, "type": LOG_TYPE, "timestamp": datetime.now().isoformat()}
        log_object.setdefault("_tick", 0)
        with self._lock:
            self.logs.append(log_object)
        self._write_line(log_object, entry_type=None)  # type already set in dict

    def drop(self, n: int = 1):
        """
        Drop the last *n* message entries from memory and from the JSONL file.

        Log entries interspersed in the file are left untouched.

        Args:
            n (int): Number of message entries to drop. Values below 1 are a no-op.
        """
        if n < 1:
            return

        with self._lock:
            if self._path.exists():
                try:
                    _drop_last_n_message_lines(filename=self._path, n=n)
                    self._reload_from_disk()
                except Exception as e:
                    user_console.print(f"[red]Error dropping lines:[/red] {e}")

    def delete(self):
        """
        Delete the JSONL context file from disk.

        Raises:
            FileNotFoundError: If the file does not exist.
            OSError: If the file cannot be removed for other reasons.
        """
        with self._lock:
            if not self._path.exists():
                raise FileNotFoundError(f"Context file does not exist: {self._path}")
            try:
                os.remove(self._path)
            except Exception as e:
                user_console.print(f"[red]Error deleting context file:[/red] {e}")
                raise

    # ------------------------------------------------------------------
    # File change watching
    # ------------------------------------------------------------------

    def start_watching(self, interval: float = 2.0):
        """
        Start a background thread that polls for file changes.

        The thread checks the file's modification time every *interval* seconds.
        If a change is detected, the context is reloaded from disk.

        Args:
            interval (float): Polling interval in seconds (default: 2.0)
        """
        if self._watch_thread and self._watch_thread.is_alive():
            return
        self._watch_interval = interval
        self._watch_active = True
        self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._watch_thread.start()

    def stop_watching(self):
        """Stop the background file watching thread."""
        self._watch_active = False
        if self._watch_thread:
            self._watch_thread.join(timeout=1.0)
            self._watch_thread = None

    def check_and_reload_if_changed(self) -> bool:
        """
        Check if the context file's modification time has changed.
        If so, reload the context from disk.

        Returns:
            bool: True if the context was reloaded, False otherwise.
        """
        if not self._path or not self._path.exists():
            return False
        try:
            current_mtime = self._path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return False

        if current_mtime != self._file_mtime:
            with self._lock:
                # Double-check inside lock
                if current_mtime != self._file_mtime:
                    self._reload_from_disk()
                    return True
        return False

    def _watch_loop(self):
        """Background thread loop: periodically check for file changes."""
        while self._watch_active:
            time.sleep(self._watch_interval)
            try:
                self.check_and_reload_if_changed()
            except Exception:
                # Silently ignore errors in watcher; we don't want the thread to die
                pass

    def _reload_from_disk(self):
        """Reload context and logs from the file. Must be called with lock held."""
        if not self._path or not self._path.exists():
            return

        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return

        messages, logs = [], []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue  # skip bad lines

            if entry.get("type", MESSAGE_TYPE) == LOG_TYPE:
                logs.append(entry)
            else:
                # Strip metadata fields that are added on write
                messages.append(
                    {k: v for k, v in entry.items() if k not in ("type", "timestamp")}
                )

        self.context = messages
        self.logs = logs
        try:
            self._file_mtime = self._path.stat().st_mtime
        except (OSError, FileNotFoundError):
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _gen_save_path(self) -> Path:
        """Return the Path for this context's JSONL file."""
        parts = [self.start_date, self.id]
        if self.custom_suffix:
            parts.append(self.custom_suffix)
        filename = "_".join(parts) + CONTEXT_FILE_EXT
        result: Path = self.context_dir / filename
        return result

    def _write_line(self, obj: dict, entry_type: str | None):
        """
        Serialize *obj* as a JSON line and append it to the JSONL file.

        Args:
            obj (dict): The object to serialize. Not muted.
            entry_type (str | None): If provided, injected as ``"type"`` only
                when the key is absent. Pass ``None`` when the type is already
                set on *obj* (e.g. log entries built by :meth:`add_log`).
        """
        record = dict(obj)
        record.setdefault("_tick", 0)
        if entry_type is not None:
            record.setdefault("type", entry_type)
        record.setdefault("timestamp", datetime.now().isoformat())

        try:
            # Append is atomic on POSIX when using "a" mode; sufficient for
            # single-user local operation.
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            self._file_mtime = self._path.stat().st_mtime
        except Exception as e:
            user_console.print(f"[red]Error writing to context file:[/red] {e}")

    def replace_all(self, messages: list):
        """
        Atomically replace all messages in the context file.

        This method preserves log entries while replacing message entries.

        Args:
            messages: List of message dicts (with 'role' and 'content').
        """
        with self._lock:
            # Read existing logs
            logs = []
            if self._path.exists():
                try:
                    lines = self._path.read_text(encoding="utf-8").splitlines()
                    for raw in lines:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            entry = json.loads(raw)
                            if entry.get("type", MESSAGE_TYPE) == LOG_TYPE:
                                logs.append(entry)
                        except json.JSONDecodeError:
                            continue
                except Exception:
                    pass

            # Write new messages + preserved logs atomically
            temp_path = self._path.with_suffix(".tmp")
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    for msg in messages:
                        entry = dict(msg)
                        entry.setdefault("type", MESSAGE_TYPE)
                        entry.setdefault("timestamp", datetime.now().isoformat())
                        f.write(json.dumps(entry) + "\n")
                    # Append preserved logs
                    for log in logs:
                        f.write(json.dumps(log) + "\n")
                temp_path.replace(self._path)
                self._reload_from_disk()
            except Exception as e:
                user_console.print(f"[red]Error replacing context: {e}[/red]")
                raise

    def update_message(self, index: int, new_msg: dict):
        """
        Update a specific message by index atomically.

        Args:
            index: Zero-based index of message to update
            new_msg: New message dict (role, content)
        """
        if index < 0:
            raise IndexError("Message index cannot be negative")

        with self._lock:
            if not self._path.exists():
                raise FileNotFoundError("Context file does not exist")

            lines = self._path.read_text(encoding="utf-8").splitlines()

            # Find all message line indices
            message_indices = [
                i
                for i, line in enumerate(lines)
                if line.strip()
                and json.loads(line).get("type", MESSAGE_TYPE) == MESSAGE_TYPE
            ]

            if index >= len(message_indices):
                raise IndexError(
                    f"Message index {index} out of range (0-{len(message_indices) - 1})"
                )

            # Build updated line
            updated_entry = dict(new_msg)
            updated_entry.setdefault("type", MESSAGE_TYPE)
            updated_entry.setdefault("timestamp", datetime.now().isoformat())
            new_line = json.dumps(updated_entry)

            # Replace the line
            lines[message_indices[index]] = new_line

            # Write atomically
            temp_path = self._path.with_suffix(".tmp")
            temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temp_path.replace(self._path)

            # Reload
            self._reload_from_disk()

    def delete_message(self, index: int):
        """
        Delete a specific message by index.

        Args:
            index: Zero-based index of message to delete
        """
        if index < 0:
            raise IndexError("Message index cannot be negative")

        with self._lock:
            if not self._path.exists():
                raise FileNotFoundError("Context file does not exist")

            lines = self._path.read_text(encoding="utf-8").splitlines()

            # Find message line indices
            message_indices = [
                i
                for i, line in enumerate(lines)
                if line.strip()
                and json.loads(line).get("type", MESSAGE_TYPE) == MESSAGE_TYPE
            ]

            if index >= len(message_indices):
                raise IndexError(
                    f"Message index {index} out of range (0-{len(message_indices) - 1})"
                )

            cut_index = message_indices[index]
            lines.pop(cut_index)

            # Write back atomically
            temp_path = self._path.with_suffix(".tmp")
            temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temp_path.replace(self._path)

            self._reload_from_disk()

    def truncate_message(self, index: int, max_chars: int = 200):
        """
        Truncate a message's content, storing the original in _truncated_from.

        Args:
            index: Zero-based index of message to truncate
            max_chars: Maximum characters to keep (default 200)

        Raises:
            IndexError: If index is out of range
            ValueError: If message is too short to truncate
        """
        if index < 0:
            raise IndexError("Message index cannot be negative")

        with self._lock:
            if not self._path.exists():
                raise FileNotFoundError("Context file does not exist")

            lines = self._path.read_text(encoding="utf-8").splitlines()

            # Find message line indices
            message_indices = [
                i
                for i, line in enumerate(lines)
                if line.strip()
                and json.loads(line).get("type", MESSAGE_TYPE) == MESSAGE_TYPE
            ]

            if index >= len(message_indices):
                raise IndexError(
                    f"Message index {index} out of range (0-{len(message_indices) - 1})"
                )

            line_idx = message_indices[index]
            entry = json.loads(lines[line_idx])

            # Only truncate if content is longer than max_chars
            content = entry.get("content", "")
            if len(content) <= max_chars:
                raise ValueError(
                    f"Message content ({len(content)} chars) is already under {max_chars} chars"
                )

            # Store original in _truncated_from if not already truncated
            if "_truncated_from" in entry:
                # Already truncated, update from the stored original
                original = entry["_truncated_from"]
            else:
                original = content

            # Create truncated version
            original_size = len(original)
            truncated_content = (
                content[:max_chars]
                + f"... [truncated, original: {original_size} chars]"
            )

            # Update entry
            entry["content"] = truncated_content
            entry["_truncated_from"] = original

            # Write back
            lines[line_idx] = json.dumps(entry)

            temp_path = self._path.with_suffix(".tmp")
            temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temp_path.replace(self._path)

            self._reload_from_disk()

    def expand_message(self, index: int):
        """
        Restore a truncated message's original content from _truncated_from.

        Args:
            index: Zero-based index of message to expand

        Raises:
            IndexError: If index is out of range
            ValueError: If message is not truncated (no _truncated_from field)
        """
        if index < 0:
            raise IndexError("Message index cannot be negative")

        with self._lock:
            if not self._path.exists():
                raise FileNotFoundError("Context file does not exist")

            lines = self._path.read_text(encoding="utf-8").splitlines()

            # Find message line indices
            message_indices = [
                i
                for i, line in enumerate(lines)
                if line.strip()
                and json.loads(line).get("type", MESSAGE_TYPE) == MESSAGE_TYPE
            ]

            if index >= len(message_indices):
                raise IndexError(
                    f"Message index {index} out of range (0-{len(message_indices) - 1})"
                )

            line_idx = message_indices[index]
            entry = json.loads(lines[line_idx])

            if "_truncated_from" not in entry:
                raise ValueError("Message is not truncated (no _truncated_from field)")

            # Restore original content
            entry["content"] = entry.pop("_truncated_from")

            # Write back
            lines[line_idx] = json.dumps(entry)

            temp_path = self._path.with_suffix(".tmp")
            temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temp_path.replace(self._path)

            self._reload_from_disk()


# ----------------------------------------------------------------------
# Module-level helpers (preserve original API)
# ----------------------------------------------------------------------


def new_context(custom_suffix: str = "", sub_dir: str = ""):
    """
    Create and return a :class:`ContextHandler` instance.

    Args:
        custom_suffix: Appended to the context filename.
        sub_dir: Subdirectory under ``contexts_dir`` to store the file in.

    Returns:
        ContextHandler: A freshly initialized context.
    """
    return ContextHandler(custom_suffix=custom_suffix, sub_dir=sub_dir)


def context_from_file(path):
    """
    Load a :class:`ContextHandler` from an existing JSONL file.

    Message entries (``type="message"`` or missing ``type``) populate
    ``ContextHandler.context``; log entries (``type="log"``) populate
    ``ContextHandler.logs``.

    *path* may be an absolute path, a relative path, or a bare filename that
    will be resolved against ``settings.contexts_dir``.

    Args:
        path (str | Path): Path to the JSONL context file.

    Returns:
        ContextHandler: A context handler pre-loaded with the file's contents.

    Raises:
        ValueError: If the file cannot be found, is empty, or contains no
            message entries.
    """
    path = Path(path)

    # Resolve bare filenames against the contexts directory.
    if not path.is_file():
        candidate = settings.contexts_dir / path.name
        if candidate.is_file():
            path = candidate
        else:
            raise ValueError(f"Context file not found: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"Context file is empty: {path}")

    messages, logs = [], []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        entry = json.loads(raw)
        if entry.get("type", MESSAGE_TYPE) == LOG_TYPE:
            logs.append(entry)
        else:
            messages.append(
                {k: v for k, v in entry.items() if k not in ("type", "timestamp")}
            )

    if not messages:
        raise ValueError(f"No message entries found in context file: {path}")

    # Parse id, date, and optional suffix from the filename.
    stem_parts = [p for p in path.stem.split("_") if p]
    ctx_date = stem_parts[0]
    ctx_id = stem_parts[1]
    ctx_suffix = "_".join(stem_parts[2:]) if len(stem_parts) > 2 else ""

    # Detect whether the file lives in a named subdirectory of contexts_dir.
    try:
        rel = path.parent.relative_to(settings.contexts_dir)
        ctx_sub_dir = str(rel) if rel != Path(".") else ""
    except ValueError:
        ctx_sub_dir = ""

    ch = ContextHandler(custom_suffix=ctx_suffix, sub_dir=ctx_sub_dir)
    ch._path = path
    ch._file_mtime = path.stat().st_mtime
    ch.context = messages
    ch.logs = logs
    # Override start_date to reflect the date from the filename
    ch.start_date = ctx_date
    ch.id = ctx_id

    return ch


def previous_conversations():
    """
    Return the file names of all saved conversation files in ``contexts_dir``.

    Returns:
        list[str]: File names (not full paths) of the
            top-level contexts directory.
    """
    contexts_dir = settings.contexts_dir
    return [f.name for f in contexts_dir.iterdir() if f.is_file()]


def latest_context_file():
    """
    Return the path to the most recently modified context file.

    Returns:
        Path: Absolute path to the latest context file.

    Raises:
        FileNotFoundError: If no context files exist.
    """
    contexts_dir = settings.contexts_dir
    files = [
        f
        for f in contexts_dir.iterdir()
        if f.is_file() and f.suffix == CONTEXT_FILE_EXT
    ]
    if not files:
        raise FileNotFoundError(f"No context files found in {contexts_dir}")
    return max(files, key=lambda f: f.stat().st_mtime)


def _drop_last_n_message_lines(filename: Path, n: int):
    """
    Remove the last *n* message entries from *filename*, plus any log lines
    that are interleaved within that tail range.

    Concretely: find the file index of the *n*-th-from-last message line and
    truncate everything from that index onwards, regardless of entry type.
    Log lines that appear before the cut-off point are preserved.

    NOTE: This function expects that the caller already holds an exclusive lock
    on the file. It does NOT acquire its own lock.

    Args:
        filename (Path): Path to the JSONL file to modify.
        n (int): Number of message lines to remove.

    Raises:
        ValueError: If fewer than *n* message lines exist in the file.
    """
    lines = Path(filename).read_text(encoding="utf-8").splitlines(keepends=True)

    message_indices = [
        i
        for i, line in enumerate(lines)
        if line.strip() and json.loads(line).get("type", MESSAGE_TYPE) == MESSAGE_TYPE
    ]

    if len(message_indices) < n:
        raise ValueError(
            f"Cannot drop {n} message lines; only {len(message_indices)} exist."
        )

    cut_from = message_indices[-n]
    Path(filename).write_text("".join(lines[:cut_from]), encoding="utf-8")  # type: ignore


if __name__ == "__main__":
    for c in previous_conversations():
        print(c)
