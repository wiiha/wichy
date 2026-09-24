"""Append-only revision log for block documents.

Every mutation writes exactly one revision entry describing what changed.
Entries are never modified or deleted; rotation is the only removal. That makes
the log an audit trail rather than a cache: reverting to an earlier state
appends a NEW entry, so a mistake stays visible.

Three properties carry the weight here:

- **Entry ids are monotonic per document, and rotation never resets them.** The
  counter lives in the document's ``meta.next_revision_id``, not in the log, so
  rotating a file cannot restart numbering. Without that, ``since_id`` and
  get-by-id become ambiguous the moment a log rotates, because two different
  entries would share an id.
- **Rotation is count-based.** Retention by age would let a quiet document keep
  history forever while a busy one lost it within days.
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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from wichy.config import settings
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

    def by_id(self) -> dict[str, BlockState]:
        """State keyed by block id."""
        return {block.id: block for block in self.blocks}


#: Entries in the live log before it is moved aside. An internal file-size
#: detail; how MUCH history survives is the limit in ``_history_limit``.
ROTATE_AT_ENTRIES = 50


def _rotation_stamp() -> str:
    """UTC timestamp for a rotated log's filename, to the microsecond.

    Colons are omitted so the name is safe on every filesystem. Microseconds
    rather than seconds because two rotations inside one second are common in
    tests and possible under load, and the finer stamp makes the collision path
    very unlikely rather than routine.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _history_limit() -> int:
    """How many revisions are kept in total, across every log file.

    Counted in STATES, not files: the newest N revisions survive and older ones
    are dropped, oldest first. A single number rather than "entries before
    rotation" plus "rotated files to keep", because the user-visible fact is how
    much history is available and two settings made that a multiplication the
    user had to do themselves.
    """
    return max(int(settings.notes_revisions_max_count), 1)


def _max_entries() -> int:
    """Entries allowed in the live log before it is rotated away.

    Rotation is an internal file-size mechanism, not a history length: it decides
    when the live file is moved aside, and the history LIMIT is what decides how
    much of it survives. Kept well below the limit so a rotation does not throw
    away history the limit still allows -- at the limit, a rotation would discard
    everything the user asked to keep. Capped at the limit for the degenerate
    case where the limit is smaller.
    """
    return min(ROTATE_AT_ENTRIES, _history_limit())


def _retention() -> int:
    """How many rotated logs to keep, derived from the history limit.

    Not a user setting: the limit counts revisions, and this is just enough files
    to hold them. One rotated log is added of slack so a rotation never prunes a
    file the limit still needs.
    """
    return max(_history_limit() // ROTATE_AT_ENTRIES, 0) + 1


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


def read_log_entries(path: Path) -> list[dict]:
    """Read one JSONL file, skipping blank and malformed lines.

    Deliberately tolerant, matching the context handler's reader: a truncated
    final line is what a crash mid-append looks like, and refusing to read the
    rest of the file would turn one bad line into total history loss.

    Args:
        path: The JSONL file to read.

    Returns:
        Parsed entries in file order; empty when the file is absent, blank or
        malformed. A file that exists but cannot be READ reports the failure
        instead of returning empty, because "no history" and "history I could
        not open" call for different responses from the caller.

    Raises:
        OSError: The file exists but could not be read.
    """
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()

    entries: list[dict] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # Only objects are entries. A line holding a bare number, string or
        # array is valid JSON but cannot be a revision, and letting it through
        # would make every later .get() raise -- one stray line costing the
        # whole history, which is what the tolerance above exists to prevent.
        if isinstance(parsed, dict):
            entries.append(parsed)
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
    """
    require_valid_slug(slug)
    entries = read_log_entries(revisions_path(slug))
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

    Append mode rather than a read-modify-write of the whole file: the log is
    only ever added to, so a single-line append is atomic on a POSIX filesystem
    and does not need the temp-and-rename treatment a document write does.

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
    # AFTER the append: pruning first left the window one short, so the entry
    # just written pushed the total past the limit and the file only shrank on
    # the NEXT write.
    prune_rotated(slug)


def rotate_if_needed(slug: str) -> Path | None:
    """Move the live log aside once it holds a rotation's worth of entries, then
    enforce the history limit.

    Rotation is a storage detail: it keeps the live file small and bounds the
    cost of an append. What survives is decided by ``prune_rotated`` against the
    entry limit, which runs on every write once history is at that limit.

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
    prune_rotated(slug)
    return target


def _baseline_entry(slug: str, upto_id: int) -> dict | None:
    """A snapshot entry standing in for history that ends at *upto_id*.

    Replay rebuilds state by applying the log from the beginning, so a log whose
    earliest entries are gone cannot be rebuilt: any block first created in a
    dropped entry is simply absent afterwards, and the reconstruction looks like
    a real (but wrong) document. That is what an unbounded FIFO does to history.

    This writes the state AT *upto_id* as a single entry of ``add`` ops, so the
    surviving entries replay onto a correct starting point. The entry carries
    ``upto_id`` itself, which is free: the file holding that original entry is
    the one being deleted, so the id is not duplicated anywhere.

    Args:
        slug: The document slug.
        upto_id: The last revision id that is about to be dropped.

    Returns:
        The entry, or None when there is nothing to record (an empty state, or a
        log too incomplete to know the state).
    """
    state = replay(slug, upto_id=upto_id + 1)
    if not state.blocks:
        return None
    ops = [
        {
            "op": "add",
            "block_id": block.id,
            "block_type": block.type,
            "data": dict(block.data),
            "index": index,
        }
        for index, block in enumerate(state.blocks)
    ]
    return {
        "id": upto_id,
        "timestamp": now_iso(),
        "author": "system",
        # Explicit, so a reader can tell a snapshot from a change someone made
        # without inferring it from the author.
        "baseline": True,
        "version_from": 0,
        "version_to": 0,
        "ops": ops,
        "summary": (
            f"Baseline: {len(ops)} block{'s' if len(ops) != 1 else ''} as of "
            "revision %d. Earlier history was dropped." % upto_id
        ),
    }


def prune_rotated(slug: str) -> list[Path]:
    """Enforce the history limit, oldest entries first.

    Counted in ENTRIES, across every file, because that is what the limit means
    to the user: how many revisions are available. File rotation is only a
    storage detail, so pruning is driven by the total rather than by how many
    files happen to exist.

    A baseline entry is written for the boundary being dropped, so the surviving
    entries still replay onto a correct state -- without it, dropping the oldest
    entries removes the block creations they contain and any block not mentioned
    again vanishes from the reconstruction.

    Returns:
        The paths deleted.
    """
    require_valid_slug(slug)
    # The limit counts STATES the user can browse. A baseline entry is overhead
    # that makes those states replayable, so it is not counted against the limit
    # -- otherwise a baseline would silently cost the user one revision.
    limit = _history_limit()
    entries = [e for e in all_entries(slug) if e.get("author") != "system"]
    if len(entries) <= limit:
        return []

    # Keep the newest `limit`. Entries have unique monotonic ids, so the cut is
    # by id rather than by position.
    keep_ids = {_entry_id(e) for e in entries[len(entries) - limit :]}
    dropped = [e for e in entries if _entry_id(e) not in keep_ids]
    last_dropped = max(
        (_entry_id(e) for e in dropped if isinstance(e.get("id"), int)), default=None
    )
    kept = [e for e in entries if _entry_id(e) in keep_ids]

    # The baseline stands in for everything before the surviving window. Its id
    # is the last dropped one, which is now unused anywhere, so ids stay unique
    # and monotonic.
    if last_dropped is not None:
        baseline = _baseline_entry(slug, last_dropped)
        if baseline is not None:
            kept.insert(0, baseline)

    # Rewritten into the LIVE log and every rotated file removed: one file is
    # simpler than keeping a rotated set in step with a moving boundary, and the
    # limit bounds it to `limit` lines.
    deleted = rotated_logs(slug)
    for path in deleted:
        try:
            path.unlink()
        except OSError:
            pass

    path = revisions_path(slug)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept),
            encoding="utf-8",
        )
    except OSError:
        # Not worth failing a write over: the document change already succeeded.
        pass
    return deleted


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
    # Complete when the log starts where history begins, OR at a baseline: a
    # baseline IS the missing starting point, restated. Without this, a pruned
    # log replayed perfectly and still reported itself unusable, which would make
    # revert refuse a target it could restore exactly.
    first_entry = entries[0] if entries else None
    complete = first_id == 1 or bool(first_entry and first_entry.get("baseline"))
    reason = None
    if not complete:
        reason = (
            f"History starts at revision {first_id}, so changes before it are "
            "not recorded and the state cannot be rebuilt exactly."
            if first_id is not None
            else "No revision in this log has a usable id."
        )

    state: list[BlockState] = []
    for entry in entries:
        if upto_id is not None and _entry_id(entry) >= upto_id:
            break
        for op in entry.get("ops", []):
            _apply_op(state, op)
    return HistoryState(blocks=state, complete=complete, reason=reason)


def state_before(slug: str, revision_id: int) -> HistoryState:
    """Rebuild the document's state as it was *before* ``revision_id``.

    Replaying up to but not including the target gives the state the user is
    actually asking for: an undo means "put it back the way it was before that
    change", not "apply that change again".

    Raises:
        RevisionNotFoundError: No such revision.
    """
    if not any(_entry_id(e) == revision_id for e in all_entries(slug)):
        raise RevisionNotFoundError(
            f"No revision {revision_id} for slug '{slug}'. "
            "Use read_revisions to list the ids that exist."
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


def revert_document(
    slug: str,
    revision_id: int,
    expected_version: int,
    *,
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
        StaleVersionError: ``expected_version`` does not match.
        MarkdownDocumentError: The document is a markdown note.
    """
    require_valid_slug(slug)
    target_state = state_before(slug, revision_id)
    if not target_state.complete and not allow_partial:
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
        summary=f"Reverted to the state before revision {revision_id}",
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


__all__ = [
    "BlockState",
    "HistoryState",
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
    "prune_rotated",
    "read_log_entries",
    "read_revisions",
    "baseline_entry",
    "prepare_revision",
    "record_revision",
    "replay",
    "revert_document",
    "revert_ops",
    "rotate_if_needed",
    "rotate_now",
    "rotated_logs",
    "state_before",
]
