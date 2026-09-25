"""Append-only revision log for block documents.

Every mutation writes exactly one revision entry describing what changed.
History is append-only: entries are never modified, and the only thing that
removes a revision log is deleting the note it belongs to. That makes the log an
audit trail rather than a cache: reverting to an earlier state appends a NEW
entry, so a mistake stays visible.

Three properties carry the weight here:

- **Entry ids are monotonic per document, and rotation never resets them.** The
  counter lives in the document's ``meta.next_revision_id``, not in the log, so
  rotating a file cannot restart numbering. Without that, ``since_id`` and
  get-by-id become ambiguous the moment a log rotates, because two different
  entries would share an id.
- **Rotation is a pure storage detail.** Once the live file reaches a size
  threshold it is renamed aside, so appends stay bounded and the live file stays
  small; nothing is dropped. Rotating does not decide how much history survives,
  because all of it does, for the life of the note.
- **Past states are rebuilt by replaying forward, never by undoing an op in
  place.** An entry records the data a block ended up with, not the data it had
  before, so the pre-change content exists only in the earlier entries. Replay
  therefore starts from the beginning of the log and preserves block order,
  because order is part of the state and is not derivable from ids.
- **Revision 1 is a full snapshot of the document as created.** Without it every
  entry would describe a change from an unknown baseline, so replay could never
  rebuild the earliest state and a revert to the beginning would have nothing to
  restore. Creation is one mutating operation, so it records one entry, whose
  ops are an ``add`` for each initial block. That is what makes the rest of the
  log replayable.

This is a synchronous, document-scoped writer. It does not reuse
``event_log/store.py``: that store is session-scoped, asynchronous, batched, and
resets its counter on rotation, so it cannot back per-document revisions.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from wichy.tools.notes.blocks import require_valid_slug, revisions_path
from wichy.tools.notes.models import Author, now_iso

#: The kinds of change an entry can record. ``revert`` is produced only by a
#: revert and is never inferred from the block ops: "these blocks changed" and
#: "this was a deliberate rollback" are different facts, and the UI shows them
#: differently.
RevisionOp = Literal["update", "add", "remove", "move", "revert"]

#: The part of a rotated log's name after ``<slug>.revisions.``: a UTC
#: timestamp, an optional collision counter, then the extension.
#: Accepts both the microsecond stamp written now and the second-precision
#: stamp written by earlier code, so an existing rotated log still reads.
_ROTATED_SUFFIX = re.compile(r"^(\d{8}T\d{6}(?:\d{6})?)(?:-(\d+))?\.jsonl$")


class RevisionNotFoundError(LookupError):
    """No revision with the requested id exists for this document."""


class IncompleteHistoryError(RuntimeError):
    """The log does not reach back far enough to rebuild the requested state.

    Raised instead of guessing. A revert built on a partial history would
    silently resurrect stale block content or drop blocks it never saw, and the
    user would have no way to tell that from a correct revert.
    """


class HistoryAnchorError(RuntimeError):
    """The requested state sits behind a history anchor and was never recorded.

    An anchor is the marker for where the recorded history of an older build
    begins: it restates the state at that point, so everything from it onward
    replays exactly, but the state *before* it was dropped and cannot be
    rebuilt. A revert means "the state before this revision", which is exactly
    that unrecorded state, so it is refused rather than silently producing an
    empty document. Restoring *at* the anchor is supported, because the anchor
    itself is a rebuilt state.
    """


class CorruptRevisionLogError(RuntimeError):
    """The live revision log ends in a torn, half-written entry.

    Raised on READ, not on write. Appends are fsynced, so a torn tail is rare,
    but a crash mid-append or a disk that filled up -- by this build, an older
    one, or anything else editing the file -- leaves the last line incomplete.
    Tolerant parsing would skip it silently: the history would simply appear to
    end earlier, with no sign that an entry went missing. Refused instead, so
    the user is told to inspect the line before trusting the history.
    """


@dataclass
class BlockState:
    """One block as it existed at some point in the log."""

    id: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Plain form, for comparing against a live document's blocks."""
        return {"type": self.type, "data": dict(self.data)}

    def to_editor_block(self) -> dict[str, Any]:
        """Form suitable for rebuilding a document block."""
        return {"id": self.id, "type": self.type, "data": dict(self.data)}


@dataclass
class HistoryState:
    """A reconstructed past state, with an honest account of its completeness."""

    blocks: list[BlockState] = field(default_factory=list)
    #: True when the replay began at revision 1, so every change is accounted
    #: for. False means earlier entries are missing -- rotated away, or the
    #: document predates the log -- and the state may be partial.
    complete: bool = True
    #: Why the state is incomplete, when it is.
    reason: str | None = None
    #: True when the history is missing an entry in the MIDDLE, as opposed to
    #: merely beginning part-way through. A gap means entries exist on both sides
    #: of a lost change, so every state built after it is inexact -- and unlike a
    #: missing start, no anchor can recover it, because the information is gone.
    has_gap: bool = False

    def by_id(self) -> dict[str, BlockState]:
        """State keyed by block id."""
        return {block.id: block for block in self.blocks}


#: Entries in the live log before it is moved aside. Purely an internal
#: file-size detail: it decides when the live file becomes a rotated one, not
#: how much history survives.
ROTATE_AT_ENTRIES = 50


def _rotation_stamp() -> str:
    """UTC timestamp for a rotated log's filename, to the microsecond.

    Colons are omitted so the name is safe on every filesystem. Microseconds
    rather than seconds because two rotations inside one second are common in
    tests and possible under load, and the finer stamp makes the collision path
    very unlikely rather than routine.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def rotated_logs(slug: str) -> list[Path]:
    """Rotated logs for ``slug``, oldest first."""
    require_valid_slug(slug)
    directory = revisions_path(slug).parent
    if not directory.exists():
        return []
    found: list[tuple[tuple[str, int], Path]] = []
    prefix = f"{slug}.revisions."
    for path in directory.glob(f"{slug}.revisions.*.jsonl"):
        match = _ROTATED_SUFFIX.match(path.name[len(prefix) :])
        if match:
            # Order by the PARSED stamp and counter, never by the raw name.
            # Sorting names would be wrong: "-" sorts before ".", so a
            # collision-suffixed name would come out before the unsuffixed one
            # with the same stamp, inverting the order and making retention
            # delete the newest logs.
            counter = int(match.group(2) or 0)
            found.append(((match.group(1), counter), path))
    # Sorted explicitly rather than relying on the glob's iteration order, which
    # the filesystem does not define.
    found.sort(key=lambda pair: pair[0])
    return [path for _, path in found]


def read_log_entries(path: Path, *, live: bool = False) -> list[dict]:
    """Read one JSONL file, skipping blank and malformed lines.

    Deliberately tolerant, matching the context handler's reader: an old
    malformed line in the middle of a file is corruption that predates this
    read, and refusing the rest would turn one bad line into total history
    loss. That middle-line behaviour is unchanged.

    ``live`` marks the file as the document's CURRENT log, the one an append
    writes to now. There a malformed LAST line, or a file that does not end in a
    newline, is a torn write: a crash mid-append or a full disk. The line is
    reported rather than skipped, because skipping it would make history end
    earlier with no sign anything was lost, and the gap check cannot catch it --
    a torn tail leaves no missing id in the middle, only an absent one at the
    end. Rotated logs stay tolerant: rotation renames a COMPLETE file aside, so a
    torn line there is older corruption the reader must still get past, not a
    write that failed under it. See :class:`CorruptRevisionLogError`.

    Args:
        path: The JSONL file to read.
        live: True when ``path`` is the live log rather than a rotated one.

    Returns:
        Parsed entries in file order; empty when the file is absent, blank or
        malformed. A file that exists but cannot be READ reports the failure
        instead of returning empty, because "no history" and "history I could
        not open" call for different responses from the caller.

    Raises:
        OSError: The file exists but could not be read.
        CorruptRevisionLogError: ``live`` is set and the file ends mid-entry.
    """
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    entries: list[dict] = []
    for index, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if live and index == len(lines) - 1:
                raise CorruptRevisionLogError(
                    f"The revision log '{path.name}' ends mid-entry at line "
                    f"{index + 1}: the recorded history ends part way through a "
                    "change, which usually means a crash during a save. That "
                    "line must be inspected before the history can be trusted."
                )
            continue
        # Only objects are entries. A line holding a bare number, string or
        # array is valid JSON but cannot be a revision, and letting it through
        # would make every later .get() raise -- one stray line costing the
        # whole history, which is what the tolerance above exists to prevent.
        if isinstance(parsed, dict):
            entries.append(parsed)

    # A live log is appended to one complete line at a time, so a file that does
    # not end in a newline stopped in the middle of a write. The last line may
    # still parse (only its tail is missing), which is why this is checked
    # separately from the parse failure above.
    if live and text and not text.endswith("\n"):
        raise CorruptRevisionLogError(
            f"The revision log '{path.name}' does not end with a newline: the "
            f"recorded history ends mid-entry at line {len(lines)}, which "
            "usually means a crash during a save. That line must be inspected "
            "before the history can be trusted."
        )
    return entries


def _entry_id(entry: Mapping[str, Any]) -> int:
    """An entry's id as an int, treating a malformed id as 0.

    A non-integer id cannot be ordered against real ones; sorting it first keeps
    it visible rather than dropping an entry the reader cannot parse.
    """
    value = entry.get("id")
    return value if isinstance(value, int) else 0


def all_entries(slug: str) -> list[dict]:
    """Every entry for ``slug``, oldest first, across rotated logs.

    Concatenation order across files is not trusted: ids are unique and
    monotonic, so they are the only ordering that is correct across several
    files.

    The live log is read strictly (``live=True``): a torn final line is
    reported rather than skipped, because everything downstream -- replay,
    revert, the history browser -- would otherwise present a history that just
    ends earlier, with the lost entry invisible. Rotated logs are read
    tolerantly, since a torn line there predates the rotation.
    """
    require_valid_slug(slug)
    entries = read_log_entries(revisions_path(slug), live=True)
    for path in rotated_logs(slug):
        entries.extend(read_log_entries(path))
    entries.sort(key=_entry_id)
    return entries


def read_revisions(
    slug: str,
    *,
    limit: int | None = None,
    since_id: int | None = None,
    author: str | None = None,
) -> list[dict]:
    """Read a document's revisions, newest first.

    Covers rotated logs as well as the live one. Reading only the live log would
    make history appear to vanish the moment it rotated, and would make
    ``since_id`` useless for any id already rotated out.

    Args:
        slug: The document slug.
        limit: Return at most this many entries.
        since_id: Return only entries with a strictly greater id.
        author: Return only entries by this author.

    Returns:
        Entries, newest first.
    """
    entries = all_entries(slug)
    if since_id is not None:
        entries = [e for e in entries if _entry_id(e) > since_id]
    if author is not None:
        entries = [e for e in entries if e.get("author") == author]
    entries.reverse()
    if limit is not None:
        entries = entries[: max(limit, 0)]
    return entries


def count_revisions(slug: str) -> int:
    """Total revisions recorded for ``slug``, across rotation."""
    return len(all_entries(slug))


def get_revision(slug: str, revision_id: int) -> dict:
    """One revision by id, searching rotated logs too.

    An id stays valid after rotation; that is the point of a counter that
    survives it.

    Raises:
        RevisionNotFoundError: No such revision.
    """
    for entry in all_entries(slug):
        if _entry_id(entry) == revision_id:
            return entry
    raise RevisionNotFoundError(
        f"No revision {revision_id} for slug '{slug}'. "
        "Use read_revisions to list the ids that exist."
    )


def make_entry(
    *,
    revision_id: int,
    author: Author,
    version_from: int,
    version_to: int,
    ops: Iterable[Mapping[str, Any]],
    summary: str,
) -> dict:
    """Build one revision entry."""
    return {
        "id": revision_id,
        "timestamp": now_iso(),
        "author": author,
        "version_from": version_from,
        "version_to": version_to,
        "ops": [dict(op) for op in ops],
        "summary": summary,
    }


def append_entry(slug: str, entry: Mapping[str, Any]) -> None:
    """Append one entry to the document's live log.

    History is append-only: the only thing that ever removes a revision is
    deleting the note. Nothing here trims, drops or rewrites an entry -- rotating
    the file aside is a storage detail that keeps the live file small and does
    not discard history.

    Append mode rather than a read-modify-write of the whole file: the log is
    only ever added to, so a single-line append is atomic on a POSIX filesystem
    and does not need the temp-and-rename treatment a document write does.

    The write is flushed and fsynced before returning, so the line is durable on
    disk. A crash after this returns cannot lose an edit the caller has already
    acknowledged.

    Rotation is checked first, so the new entry lands in the fresh log rather
    than immediately overflowing the old one.
    """
    require_valid_slug(slug)
    rotate_if_needed(slug)
    path = revisions_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def rotate_if_needed(slug: str) -> Path | None:
    """Move the live log aside once it holds a rotation's worth of entries.

    Rotation is a storage detail: it keeps the live file small and bounds the
    cost of an append. Nothing is dropped when it happens -- every entry stays
    readable, spread across the live log and the rotated ones.

    Returns:
        The rotated file's path, or None when no rotation happened.
    """
    path = revisions_path(slug)
    if path.exists() and len(read_log_entries(path)) >= ROTATE_AT_ENTRIES:
        return rotate_now(slug)
    return None


def rotate_now(slug: str) -> Path | None:
    """Rotate the live log unconditionally.

    The timestamp has one-second granularity, so two rotations inside one second
    would collide. A counter suffix is added until the name is free, so the
    second rotation cannot destroy the first one's history.
    """
    require_valid_slug(slug)
    path = revisions_path(slug)
    if not path.exists():
        return None

    stamp = _rotation_stamp()
    target = path.parent / f"{slug}.revisions.{stamp}.jsonl"
    counter = 1
    while target.exists():
        target = path.parent / f"{slug}.revisions.{stamp}-{counter}.jsonl"
        counter += 1

    path.rename(target)
    return target


def replay_matches_document(slug: str) -> bool | None:
    """Whether replaying the whole log reproduces the live document.

    A reconstruction is only trustworthy if it agrees with the document the log
    is supposed to describe. It can disagree when the log lost information: a
    block whose ``add`` survives but whose later ``remove`` was dropped is
    resurrected by a replay, so the rebuilt state contains a block the document
    no longer has.

    This is exactly what a log damaged by an older, buggy prune looks like. The
    information is gone from the file, so no anchor can recover it -- but the
    mismatch IS detectable, and a caller must be able to say "this history is
    approximate" rather than presenting a wrong past as fact.

    Args:
        slug: The document slug.

    Returns:
        True when they agree. False when they disagree. None when the comparison
        cannot be made (no document, or an unreadable one) -- which is NOT a
        mismatch, and must not be reported as one.
    """
    from wichy.tools.notes.blocks import (
        DocumentNotFoundError,
        InvalidDocumentError,
        load_document,
    )

    try:
        document = load_document(slug)
    except (DocumentNotFoundError, InvalidDocumentError, OSError):
        return None

    state = replay(slug)
    rebuilt = [
        {"id": block.id, "type": block.type, "data": dict(block.data)}
        for block in state.blocks
    ]
    live = [
        {"id": block.id, "type": block.type, "data": dict(block.data)}
        for block in document.blocks
    ]
    return rebuilt == live


def _describe_ops(ops: Iterable[Mapping[str, Any]]) -> str:
    """Summarise a change for the history list.

    Counts by kind rather than naming every block, so a request touching forty
    blocks still produces a readable one-line summary.
    """
    counts: dict[str, int] = {}
    for op in ops:
        kind = str(op.get("op", "update"))
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return "No changes"

    verbs = {
        "add": ("Added", "block"),
        "update": ("Updated", "block"),
        "remove": ("Deleted", "block"),
        "move": ("Moved", "block"),
        "revert": ("Reverted", "document"),
    }
    parts = []
    for kind in ("add", "update", "remove", "move", "revert"):
        count = counts.get(kind)
        if count:
            verb, noun = verbs[kind]
            plural = "" if count == 1 else "s"
            parts.append(f"{verb} {count} {noun}{plural}")
    return " and ".join(parts)


describe_ops = _describe_ops


def prepare_revision(
    document,
    *,
    author: Author,
    ops: Iterable[Mapping[str, Any]],
    summary: str | None = None,
) -> dict:
    """Build the next revision entry and advance the document's counter.

    Allocation is separated from appending on purpose, so the caller can write
    the document FIRST and append the entry after. The two orders fail
    differently, and only one is safe:

    - Append first, then write the document: a crash in between leaves the
      counter unadvanced while an entry with that id is already in the log, so
      the next change allocates the SAME id again. Duplicate ids make
      ``get_revision`` and ``since_id`` ambiguous and make a replay apply one
      change twice -- which is what a monotonic id exists to prevent.
    - Write the document first, then append: a crash in between leaves the
      counter advanced with no entry, i.e. a GAP. Ids stay unique and
      unambiguous; the log is merely missing a record of one change.

    A gap is recoverable and a duplicate is not, so this order is the one used.

    Does no I/O beyond nothing at all: it touches only the in-memory document.

    Args:
        document: The document being changed; ``meta.next_revision_id`` is
            advanced.
        author: Who made the change.
        ops: One operation per changed block.
        summary: Override the generated summary.

    Returns:
        The entry, not yet written.
    """
    op_list = [dict(op) for op in ops]
    revision_id = int(document.meta.next_revision_id)
    # The version is bumped before this is called, so the document's current
    # version is the one this change produced.
    version_to = int(document.meta.version)
    entry = make_entry(
        revision_id=revision_id,
        author=author,
        version_from=version_to - 1,
        version_to=version_to,
        ops=op_list,
        summary=summary or _describe_ops(op_list),
    )
    document.meta.next_revision_id = revision_id + 1
    return entry


def record_revision(
    slug: str,
    document,
    *,
    author: Author,
    ops: Iterable[Mapping[str, Any]],
    summary: str | None = None,
) -> dict:
    """Allocate the next entry AND append it immediately.

    Convenience for callers with nothing to write afterwards. A caller that is
    also writing the document should instead call :func:`prepare_revision`, write
    the document, then call :func:`append_entry` -- see that function for why the
    order matters.

    Note that this is NOT the hook to pass as ``on_commit`` to
    ``locked_document``: that context manager records the revision itself, so
    passing this as well would produce two entries for one version bump.

    Args:
        slug: The document slug.
        document: The document being changed.
        author: Who made the change.
        ops: One operation per changed block.
        summary: Override the generated summary.

    Returns:
        The entry that was appended.
    """
    entry = prepare_revision(document, author=author, ops=ops, summary=summary)
    append_entry(slug, entry)
    return entry


def baseline_ops(document) -> list[dict[str, Any]]:
    """Ops describing a document's initial content, as a full snapshot.

    Every block is recorded as an ``add`` carrying its position, so replaying
    from an empty state reproduces the document exactly. Used for revision 1.

    Args:
        document: The document as created.

    Returns:
        One ``add`` op per block, in document order.
    """
    return [
        {
            "op": "add",
            "block_id": block.id,
            "block_type": block.type,
            "data": dict(block.data),
            "index": index,
        }
        for index, block in enumerate(document.blocks)
    ]


def baseline_entry(document) -> dict:
    """Build revision 1: the document's content as created, not yet written.

    Not appended here, so the caller can write the document first and append
    after -- see :func:`prepare_revision` for why that order is the safe one.

    Args:
        document: The newly created document.

    Returns:
        The baseline entry, with the document's counter advanced.
    """
    return prepare_revision(
        document,
        author=document.meta.last_author,
        ops=baseline_ops(document),
        summary=f"Created document with {len(document.blocks)} block"
        + ("" if len(document.blocks) == 1 else "s"),
    )


def block_snapshot(document) -> list[dict[str, Any]]:
    """A document's blocks as an ordered list of ``{id, type, data}``.

    Order is kept because it is part of the state: two documents with the same
    blocks in different orders are different documents, and a replay that
    discarded order could not restore either.
    """
    return [
        {"id": block.id, "type": block.type, "data": dict(block.data)}
        for block in document.blocks
    ]


def diff_ops(
    before: Iterable[Mapping[str, Any]], after: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Operations describing the change from ``before`` to ``after``.

    Compares by block id, so a reordering with unchanged content is reported as
    a move rather than a delete plus an add, and an unchanged block produces no
    op at all. That matters because the ops are what the browser uses to show
    which blocks an actor touched, and what a later replay uses to rebuild the
    past.

    Every op carries ``index``, the block's resulting position. Without it a
    replay cannot rebuild order, since ids alone do not encode it.

    Args:
        before: Ordered ``{id, type, data}`` mappings before the change.
        after: The same, after the change.

    Returns:
        Ops: additions, updates, moves, then removals.
    """
    before_list = list(before)
    after_list = list(after)
    before_by_id = {b["id"]: b for b in before_list}
    after_ids = {b["id"] for b in after_list}

    ops: list[dict[str, Any]] = []

    # Removals FIRST, and they carry no index because a removal does not depend
    # on position. Order matters: every other op's index is measured against the
    # final block list, so a replay must have dropped the removed blocks before
    # it starts placing anything. Emitting removals last would make every index
    # past a removed block off by one, and replay would rebuild the wrong order.
    for block in before_list:
        if block["id"] not in after_ids:
            ops.append(
                {
                    "op": "remove",
                    "block_id": block["id"],
                    "block_type": block.get("type"),
                }
            )

    # With removals applied first, the survivors sit in their original relative
    # order. Walking the target list left to right and fixing each position in
    # turn reaches the target in one pass, with every op's index being its final
    # position -- so a replay applies the ops in emission order and never has to
    # renumber.
    #
    # This describes a change correctly but not always minimally: rotating three
    # blocks records two moves where one (moving the first block to the end)
    # would also do. Minimality is not worth the complexity here, because a
    # wrong-but-shorter op list would make a replay rebuild the wrong order, and
    # the ops are what a revert depends on. The common single-block drag, which
    # is what the summary text and the browser highlight are read for, is
    # reported as exactly one move.
    working = [b["id"] for b in before_list if b["id"] in after_ids]
    for index, block in enumerate(after_list):
        block_id = block["id"]
        old = before_by_id.get(block_id)

        if old is None:
            ops.append(
                {
                    "op": "add",
                    "block_id": block_id,
                    "block_type": block.get("type"),
                    "data": dict(block.get("data") or {}),
                    "index": index,
                }
            )
            working.insert(index, block_id)
            continue

        if index < len(working) and working[index] != block_id:
            ops.append({"op": "move", "block_id": block_id, "index": index})
            working.remove(block_id)
            working.insert(index, block_id)

        if old.get("data") != block.get("data") or old.get("type") != block.get("type"):
            ops.append(
                {
                    "op": "update",
                    "block_id": block_id,
                    "block_type": block.get("type"),
                    "data": dict(block.get("data") or {}),
                    "index": index,
                }
            )

    return ops


def _apply_op(state: list[BlockState], op: Mapping[str, Any]) -> None:
    """Apply one recorded op to a replay-in-progress state.

    Every positional op carries an ``index`` measured against the FINAL block
    list, so this applies them in the order ``diff_ops`` emits them and never
    has to renumber anything.
    """
    block_id = op.get("block_id")
    if not block_id:
        return
    kind = op.get("op")
    index = op.get("index")

    def position() -> int:
        """The op's target index, clamped to the list that exists right now.

        Clamping rather than rejecting: a log written by an older build, or one
        whose operations were hand-edited, should still replay into something
        usable instead of raising and losing the rest of the history.
        """
        if isinstance(index, int) and 0 <= index <= len(state):
            return index
        return len(state)

    if kind == "remove":
        state[:] = [b for b in state if b.id != block_id]
    elif kind in ("add", "update"):
        block = BlockState(
            id=str(block_id),
            type=str(op.get("block_type") or "paragraph"),
            data=dict(op.get("data") or {}),
        )
        existing = next((i for i, b in enumerate(state) if b.id == block_id), None)
        if existing is None:
            state.insert(position(), block)
        else:
            # Replaced in place; for an existing block the index only matters
            # when a separate move op carries the repositioning.
            state[existing] = block
    elif kind == "move":
        moved = next((b for b in state if b.id == block_id), None)
        if moved is not None:
            state.remove(moved)
            state.insert(position(), moved)
    elif kind == "revert":
        # A revert entry names the revision it undid; its effect on the blocks is
        # already described by the block ops recorded alongside it.
        return


def replay(slug: str, upto_id: int | None = None) -> HistoryState:
    """Rebuild block state by replaying the log in order.

    Args:
        slug: The document slug.
        upto_id: Replay entries with an id strictly less than this, or every
            entry when None.

    Returns:
        The reconstructed state. ``complete`` is False when the log does not
        start at revision 1, because then the state before the earliest
        surviving entry is unknown rather than empty.
    """
    entries = all_entries(slug)
    if not entries:
        return HistoryState(
            blocks=[],
            complete=False,
            reason="No revision history is recorded for this document.",
        )

    # Entries with an unparseable id sort to 0 and are ignored for the
    # completeness check: they are not evidence of missing history, and letting
    # them decide would report a complete log as incomplete and make revert
    # refuse a perfectly good target.
    real_ids = [_entry_id(e) for e in entries if isinstance(e.get("id"), int)]
    first_id = min(real_ids) if real_ids else None
    first_entry = entries[0] if entries else None

    # Starting at revision 1 (or at an anchor) is not the whole story: the ids
    # from the earliest entry up to the target must also be CONTIGUOUS. A missing
    # id in between means an entry was lost in the MIDDLE -- dropped by an older
    # build or removed by hand -- so every state built after it was rebuilt
    # without that change and is not exact. A first-id check alone cannot see
    # this: ids [1, 2, 4, 5] still "start at 1", which is how middle damage used
    # to be served as fact.
    #
    # A legacy anchor is NOT a gap: it reuses a real id and restates the state
    # there, so ids continuing A, A+1, A+2 ... from an anchor are contiguous. A
    # missing entry AFTER an anchor is still a gap.
    walk_end = (
        upto_id if upto_id is not None else (max(real_ids) + 1 if real_ids else None)
    )
    gaps: list[tuple[int, int]] = []
    if first_id is not None and walk_end is not None and walk_end > first_id:
        previous = first_id
        for present in sorted(rid for rid in real_ids if rid < walk_end):
            if present > previous + 1:
                gaps.append((previous + 1, present - 1))
            previous = present
        # A run of missing ids that reaches the target: no surviving entry marks
        # its end, so the walk itself supplies the upper bound.
        if previous + 1 < walk_end:
            gaps.append((previous + 1, walk_end - 1))
    if gaps:
        start, end = gaps[0]
        if start == end:
            named = f"revision {start} between {start - 1} and {end + 1}"
        else:
            named = f"revisions {start} through {end}"
        if len(gaps) > 1:
            named += f" (and {len(gaps) - 1} more)"
        gap_reason = (
            f"The log is missing {named}, so states after the gap are rebuilt "
            "from an incomplete history."
        )
    else:
        gap_reason = None

    # A log is "complete" when replaying it reproduces the document exactly.
    #
    # Two ways that happens:
    # - it reaches the document's start (revision 1), or
    # - its earliest entry is an anchor: the anchor IS the missing starting
    #   point, restated, so everything from it replays exactly.
    #
    # What is NOT complete is a log that begins part-way through with no anchor:
    # its earliest entry was built on a state the log never records, so the
    # blocks it added cannot be recovered. That is the case that must be refused,
    # and it is distinct from "an anchor is present but older history is gone" --
    # which is a boundary, not a defect.
    at_anchor = bool(first_entry and first_entry.get("baseline"))
    complete = first_id == 1 or at_anchor
    reason = None
    if not complete:
        reason = (
            f"The earliest surviving revision is {first_id}, and there is no "
            "history anchor, so the state it was built from is unknown. Revisions "
            "from the next anchor onward can be rebuilt."
            if first_id is not None
            else "No revision in this log has a usable id."
        )
    # A gap OVERRIDES either way of being complete: a log that starts at 1 but
    # lost an entry in the middle is missing that change, so a state built after
    # it is not exact. The gap is named in plain words so the caller, the
    # browser and the refusal can all say what is missing.
    if gap_reason is not None:
        complete = False
        reason = gap_reason

    state: list[BlockState] = []
    for entry in entries:
        if upto_id is not None and _entry_id(entry) >= upto_id:
            break
        for op in entry.get("ops", []):
            _apply_op(state, op)
    return HistoryState(
        blocks=state,
        complete=complete,
        reason=reason,
        has_gap=gap_reason is not None,
    )


def _entry_is_anchor(entry: Mapping[str, Any]) -> bool:
    """True when an entry is a history anchor rather than a recorded change.

    ``baseline`` marks the entry an older build wrote to restate the state at
    the point its recorded history begins. New logs never gain one, but legacy
    logs still carry them and a reader must recognise them.
    """
    return bool(entry.get("baseline"))


def state_before(slug: str, revision_id: int) -> HistoryState:
    """Rebuild the document's state as it was *before* ``revision_id``.

    Replaying up to but not including the target gives the state the user is
    actually asking for: an undo means "put it back the way it was before that
    change", not "apply that change again".

    Raises:
        RevisionNotFoundError: No such revision.
        HistoryAnchorError: ``revision_id`` names the history anchor. The state
            before an anchor was never recorded, so a replay would break at the
            anchor and report an empty state -- which is not what the user
            asked for. Use ``restore_document(..., at=True)`` instead.
    """
    entries = all_entries(slug)
    target = next((e for e in entries if _entry_id(e) == revision_id), None)
    if target is None:
        raise RevisionNotFoundError(
            f"No revision {revision_id} for slug '{slug}'. "
            "Use read_revisions to list the ids that exist."
        )
    if _entry_is_anchor(target):
        # Refused where both the library caller and the API route pass through,
        # so neither can quietly write an empty document. Restoring AT the
        # anchor is a different request and stays supported.
        raise HistoryAnchorError(
            f"Revision {revision_id} is the history anchor: the marker for "
            "where the recorded history of an older build begins. The state "
            "before it was never recorded, so reverting to it is refused. "
            "Restore at the anchor instead (at=True), which is supported."
        )
    return replay(slug, upto_id=revision_id)


def revert_ops(
    current: Iterable[Mapping[str, Any]], target: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Ops that turn the current blocks into the target blocks.

    Expressed as a diff so a revert is recorded and applied like any other
    change, rather than by a special path.
    """
    return diff_ops(current, target)


def restore_document(
    slug: str,
    revision_id: int,
    expected_version: int,
    *,
    at: bool = False,
    allow_partial: bool = False,
):
    """Revert ``slug`` to the state before ``revision_id``.

    One transaction: the blocks are restored, the version is bumped once, and a
    single ``revert`` revision is appended. Existing history is never rewritten
    or removed.

    Args:
        slug: The document slug.
        revision_id: The revision to undo.
        expected_version: The caller's expected current version.
        allow_partial: Permit a revert built on an incomplete history. Off by
            default: a partial history would silently drop blocks the log never
            saw, which is indistinguishable from a correct revert afterwards.

    Returns:
        ``(document, entry)``.

    Raises:
        InvalidSlugError: The slug is not valid.
        RevisionNotFoundError: No such revision.
        IncompleteHistoryError: The history does not reach back far enough.
        HistoryAnchorError: ``at`` is False and the target names the history
            anchor, whose preceding state was never recorded. Restoring at the
            anchor (``at=True``) is supported and does not raise.
        StaleVersionError: ``expected_version`` does not match.
        MarkdownDocumentError: The document is a markdown note.
    """
    require_valid_slug(slug)
    # `at=False` restores the state BEFORE the revision -- "undo this change",
    # which is what the agent's revert means. `at=True` restores the state AS OF
    # the revision -- "put the document back to how it looked then", which is
    # what a history browser offers. They differ by exactly one revision, so they
    # share this implementation rather than diverging into two.
    if at:
        target_state = replay(slug, upto_id=revision_id + 1)
        if not any(_entry_id(e) == revision_id for e in all_entries(slug)):
            raise RevisionNotFoundError(
                f"No revision {revision_id} for slug '{slug}'. "
                "Use read_revisions to list the ids that exist."
            )
    else:
        target_state = state_before(slug, revision_id)
    if not target_state.complete and not allow_partial:
        if target_state.has_gap:
            # Distinct from "earlier history is unknown": the log HAS entries on
            # both sides of a lost change, so replaying it silently omits that
            # change and the rebuilt state is wrong rather than merely partial.
            # No anchor can recover it -- the information is gone -- so this is
            # refused even though the log reaches revision 1.
            raise IncompleteHistoryError(
                f"{target_state.reason} That revision cannot be rebuilt exactly, "
                "so the history is refused rather than recording a state that "
                "never existed."
            )
        raise IncompleteHistoryError(
            target_state.reason
            or "History is incomplete, so this state cannot be rebuilt exactly."
        )

    from wichy.tools.notes.blocks import locked_document, make_block

    target_blocks = target_state.blocks
    # The entry carries the revert marker AND the block ops the body produces.
    # The marker is what the UI and the agent read ("this was a rollback"); the
    # block ops are what a later replay needs, because a replay that saw only
    # the marker would have no idea what the revert actually changed.
    marker = {"op": "revert", "revision_id": revision_id}

    result: dict[str, Any] = {}

    def capture(document, entry: dict) -> None:
        # Runs inside the lock, before the write. Reading the log back after the
        # lock was released would race a concurrent append, and the entry
        # returned would then be the other writer's rather than this revert's.
        result["document"] = document
        result["entry"] = entry

    with locked_document(
        slug,
        expected_version,
        author="system",
        summary=(
            f"Restored the document to revision {revision_id}"
            if at
            else f"Reverted to the state before revision {revision_id}"
        ),
        extra_ops=[marker],
        on_commit=capture,
    ) as document:
        existing = {block.id: block for block in document.blocks}
        rebuilt = []
        # Built in the TARGET's order, so restored blocks return to the position
        # they held in the state being restored rather than being appended.
        for target in target_blocks:
            block = existing.get(target.id)
            if block is None:
                block = make_block(
                    target.type,
                    target.data,
                    author="system",
                    block_id=target.id,
                    document=document,
                )
            else:
                block.type = target.type
                block.data = dict(target.data)
            rebuilt.append(block)
        document.blocks = rebuilt

    return result["document"], result["entry"]


def revert_document(
    slug: str,
    revision_id: int,
    expected_version: int,
    *,
    allow_partial: bool = False,
):
    """Revert ``slug`` to the state BEFORE ``revision_id``.

    Thin wrapper over :func:`restore_document`, which is where the work lives;
    this keeps the name the agent's revert and the API already call.
    """
    return restore_document(
        slug,
        revision_id,
        expected_version,
        at=False,
        allow_partial=allow_partial,
    )


__all__ = [
    "BlockState",
    "HistoryState",
    "CorruptRevisionLogError",
    "HistoryAnchorError",
    "IncompleteHistoryError",
    "RevisionNotFoundError",
    "all_entries",
    "append_entry",
    "block_snapshot",
    "count_revisions",
    "describe_ops",
    "diff_ops",
    "get_revision",
    "make_entry",
    "read_log_entries",
    "read_revisions",
    "baseline_entry",
    "prepare_revision",
    "record_revision",
    "replay",
    "replay_matches_document",
    "revert_document",
    "restore_document",
    "revert_ops",
    "rotate_if_needed",
    "rotate_now",
    "rotated_logs",
    "state_before",
]
