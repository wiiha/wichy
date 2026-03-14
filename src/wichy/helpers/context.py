import json
import os
import time
from datetime import datetime
from pathlib import Path

from rich import print

from wichy.config import settings

CONTEXT_FILE_EXT = ".json"

# Type constants for JSONL entries
MESSAGE_TYPE = "message"
LOG_TYPE = "log"


class ContextHandler:
    """
    Manages a single conversation's context, persisting it as a JSONL file.

    The JSONL file may contain two kinds of entries:

    - ``type="message"``: LLM conversation turns stored in ``self.context``.
      Returned by ``__call__`` and counted by ``__len__``.
    - ``type="log"``: Arbitrary session metadata stored in ``self.logs``.
      Persisted to the same file but never included in the LLM context and
      invisible to ``__call__`` / ``__len__``.
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
            custom_suffix (str): Suffix used in the file name.
            sub_dir (str): Subdirectory used for the file.
            context_dir (Path): Resolved path to the storage directory.
        """
        self.context = []
        self.logs = []
        # Time-based ID is fine for local, single-user use.
        self.id = str(time.time()).split(".")[0]
        self.start_date = datetime.now().strftime("%Y-%m-%d")
        self.custom_suffix = custom_suffix
        self.sub_dir = sub_dir
        self._ensure_context_dir()

    def _ensure_context_dir(self):
        """Create the context storage directory (and any sub_dir) if missing."""
        self.context_dir = settings.contexts_dir
        if self.sub_dir:
            self.context_dir = self.context_dir / self.sub_dir
        os.makedirs(self.context_dir, exist_ok=True)

    def __len__(self):
        """Return the number of message entries (log entries excluded)."""
        return len(self.context)

    def __call__(self):
        """Return the message context as a list (log entries excluded)."""
        return self.context

    def append(self, new_object):
        """
        Append a message dict to the in-memory context and persist it.

        A ``type`` and ``timestamp`` field are injected into the persisted copy
        if not already present; the original dict is not mutated.

        Args:
            new_object (dict): Must contain at least ``role`` and ``content`` keys.
        """
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

        save_path = self._gen_save_path()
        try:
            _drop_last_n_message_lines(filename=save_path, n=n)
        except Exception as e:
            print(f"[red]Error dropping lines from file:[/red] {e}")
            return

        self.context = self.context[:-n]

    def delete(self):
        """
        Delete the JSONL context file from disk.

        Raises:
            OSError: If the file cannot be removed.
        """
        os.remove(self._gen_save_path())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _gen_save_path(self) -> Path:
        """Return the Path for this context's JSONL file."""
        parts = [self.start_date, self.id]
        if self.custom_suffix:
            parts.append(self.custom_suffix)
        filename = "_".join(parts) + CONTEXT_FILE_EXT
        return self.context_dir / filename

    def _write_line(self, obj: dict, entry_type: str | None):
        """
        Serialize *obj* as a JSON line and append it to the JSONL file.

        Args:
            obj (dict): The object to serialize. Not mutated.
            entry_type (str | None): If provided, injected as ``"type"`` only
                when the key is absent. Pass ``None`` when the type is already
                set on *obj* (e.g. log entries built by :meth:`add_log`).
        """
        record = dict(obj)
        if entry_type is not None:
            record.setdefault("type", entry_type)
        record.setdefault("timestamp", datetime.now().isoformat())

        try:
            with open(self._gen_save_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            print(f"[red]Error writing to context file:[/red] {e}")


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def new_context():
    """
    Create and return a new :class:`ContextHandler` instance.

    Returns:
        ContextHandler: A freshly initialised context.
    """
    return ContextHandler()


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
    ch.start_date = ctx_date
    ch.id = ctx_id
    ch.context = messages
    ch.logs = logs

    return ch


def previous_conversations():
    """
    Return the file names of all saved conversation files in ``contexts_dir``.

    Returns:
        list[str]: File names (not full paths) of context files in the
            top-level contexts directory.
    """
    contexts_dir = settings.contexts_dir
    return [f.name for f in contexts_dir.iterdir() if f.is_file()]


def _drop_last_n_message_lines(filename: Path, n: int):
    """
    Remove the last *n* message entries from *filename*, plus any log lines
    that are interleaved within that tail range.

    Concretely: find the file index of the *n*-th-from-last message line and
    truncate everything from that index onwards, regardless of entry type.
    Log lines that appear before the cut-off point are preserved.

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

    # Cut from the first of the n targeted message lines onwards.
    cut_from = message_indices[-n]
    Path(filename).write_text("".join(lines[:cut_from]), encoding="utf-8")


if __name__ == "__main__":
    for c in previous_conversations():
        print(c)
