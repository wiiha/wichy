"""In-memory server state shared by the notes API and the block tools.

This module owns the process-wide coordination primitives for block documents:

- ``agent_changes`` -- slug -> queued agent operations awaiting browser pickup
- ``doc_versions`` -- slug -> current version, for fast stale-write checks
- ``doc_locks`` -- slug -> per-document lock
- ``agent_busy`` -- whether an agent turn is in flight

Nothing here is persisted. After a restart the browser re-fetches on its next
poll and reconciles from disk, so losing this state is safe.

The three containers share one module-level re-entrant lock. That lock protects
only the containers themselves -- cheap, in-memory operations -- so it is never
held across file I/O or network calls. The per-document lock is the one that
spans a read-check-write.

Do not import anything from ``wichy.tools`` or ``wichy.root_agent`` here. The
agent base class and the notes tools both import this module, so keeping it
stdlib-only is what makes that safe.
"""

from __future__ import annotations

import threading

# -------------------------------------------------------------------------
# Containers
# -------------------------------------------------------------------------

# slug -> list of agent ops awaiting browser pickup
agent_changes: dict[str, list[dict]] = {}

# slug -> current version, for fast stale-write checks
doc_versions: dict[str, int] = {}

# slug -> per-document lock. Held across resolve -> read -> compare -> write.
#
# Entries are never removed, deliberately. Dropping a lock while another thread
# holds it would hand a *different* lock object to the next caller and silently
# break mutual exclusion -- the exact lost update these exist to prevent. The
# set is therefore bounded by the number of documents ever touched in this
# process, which is user data and small. A restart clears it.
doc_locks: dict[str, threading.RLock] = {}

# True from the moment a user message is accepted until the response is ready.
# The Event carries its own synchronization, so it is not guarded by _state_lock.
agent_busy = threading.Event()

# Guards the three containers above. Re-entrant so a caller already inside may
# call another accessor. Never hold this across I/O.
_state_lock = threading.RLock()


# -------------------------------------------------------------------------
# Document locks
# -------------------------------------------------------------------------


def get_doc_lock(slug: str) -> threading.RLock:
    """Return the per-document lock for *slug*, creating it on first use.

    The lock is re-entrant, so a caller that already holds it may nest. Callers
    must hold the lock across the whole read-check-write sequence; taking it
    around the write alone does not prevent a lost update.

    Args:
        slug: The document slug.

    Returns:
        The re-entrant lock for that slug. It is never swapped out or deleted,
        so holding a reference across the critical section is safe.
    """
    with _state_lock:
        lock = doc_locks.get(slug)
        if lock is None:
            lock = threading.RLock()
            doc_locks[slug] = lock
        return lock


# -------------------------------------------------------------------------
# Agent changes queue
# -------------------------------------------------------------------------


def queue_agent_change(slug: str, op: dict) -> None:
    """Append one agent operation to *slug*'s pending-change list.

    Args:
        slug: The document slug.
        op: The operation dict. Stored as-is; the caller owns its shape.
    """
    with _state_lock:
        agent_changes.setdefault(slug, []).append(op)


def drain_agent_changes(slug: str) -> list[dict]:
    """Return and clear *slug*'s pending agent operations.

    The drain is atomic with respect to other callers, so two concurrent polls
    cannot both receive the same operation.

    Args:
        slug: The document slug.

    Returns:
        The operations that were queued, in insertion order.
    """
    with _state_lock:
        return agent_changes.pop(slug, [])


def peek_agent_changes(slug: str) -> list[dict]:
    """Return a copy of *slug*'s pending operations without clearing them.

    Args:
        slug: The document slug.

    Returns:
        A shallow copy of the queued operation dicts.
    """
    with _state_lock:
        return list(agent_changes.get(slug, []))


def clear_agent_changes(slug: str) -> None:
    """Discard *slug*'s pending operations without returning them.

    Args:
        slug: The document slug.
    """
    with _state_lock:
        agent_changes.pop(slug, None)


def discard_stale_changes(slug: str, current_version: int) -> list[dict]:
    """Drop queued operations older than *current_version*, atomically.

    An operation produced at or before *current_version* has already been
    applied, so keeping it would have the browser reapply an old state over a
    newer one. This is the ONLY expiry for a queued operation -- nothing times
    out on a clock, because a slow browser is not a reason to lose work.

    Args:
        slug: The document slug.
        current_version: The version the document is now at.

    Returns:
        The operations still queued, in insertion order.
    """

    def recorded_version(op: dict) -> int | None:
        """An op's version, or None when it does not carry a usable one."""
        value = op.get("version")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    with _state_lock:
        kept = []
        for op in agent_changes.get(slug, []):
            version = recorded_version(op)
            if version is None:
                # No usable version: keep it. "I do not know when this was made"
                # is not "the browser has already applied it", and dropping it
                # would discard an op that was never delivered.
                kept.append(op)
            elif version > current_version:
                # Strictly newer: acking up to V means the browser has applied
                # everything produced at or before V, so an op at V is already on
                # screen and reapplying it would revert the document.
                kept.append(op)
        if kept:
            agent_changes[slug] = kept
        else:
            agent_changes.pop(slug, None)
        return list(kept)


def collapse_by_block(ops: list[dict]) -> list[dict]:
    """Reduce queued operations to the latest one per block.

    Several operations for one block are not a queue of independent edits: only
    the last state of that block is meaningful, because the browser applies the
    final content, not a replay. Collapsing here is what makes the conflict cap
    a count of DISTINCT BLOCKS rather than of operations.

    Args:
        ops: The queued operations, oldest first.

    Returns:
        One operation per block: the last one for it, in the order the blocks
        were first touched.
    """
    latest: dict[str, dict] = {}
    order: list[str] = []
    for op in ops:
        key = str(op.get("block_id") or "")
        if key not in latest:
            order.append(key)
        latest[key] = op
    return [latest[key] for key in order]


def count_distinct_blocks(ops: list[dict]) -> int:
    """How many distinct blocks a set of operations touches."""
    return len({str(op.get("block_id") or "") for op in ops})


# -------------------------------------------------------------------------
# Versions
# -------------------------------------------------------------------------


def get_doc_version(slug: str) -> int:
    """Return the cached version for *slug*, or 0 when nothing is cached.

    This is a fast-path check only. The authoritative version lives in the
    document file, so a missing entry means "unknown", not "version zero".

    Args:
        slug: The document slug.

    Returns:
        The cached version, or 0 if the slug has no cached version.
    """
    with _state_lock:
        return doc_versions.get(slug, 0)


def set_doc_version(slug: str, version: int) -> None:
    """Cache *version* for *slug*.

    Args:
        slug: The document slug.
        version: The version just written to disk.
    """
    with _state_lock:
        doc_versions[slug] = version


def clear_doc_version(slug: str) -> None:
    """Forget any cached version for *slug*.

    Used when the slug stops existing, so a stale cache cannot later satisfy a
    stale-write check.

    Args:
        slug: The document slug.
    """
    with _state_lock:
        doc_versions.pop(slug, None)


# -------------------------------------------------------------------------
# Change snapshot -- what the browser polls for
# -------------------------------------------------------------------------


def describe_pending(slug: str) -> dict:
    """Return everything the browser needs about *slug* in one consistent read.

    Read as a unit on purpose. A caller that reads the queue and the version
    separately can see a version newer than the operations it was handed, and
    would then discard operations it had not applied yet.

    The queue is PEEKED, not drained. A drain makes the response its own
    acknowledgement: if it is lost -- tab closed, reload, a dropped connection --
    the operations are gone with it, and the browser never learns a version
    changed so it never re-fetches them either. The ack is what removes them, so
    an operation survives until the browser says it has applied it.

    Args:
        slug: The document slug.

    Returns:
        A dict with ``changes`` (the pending operations), ``version`` (the
        cached version), and ``agent_busy``.
    """
    with _state_lock:
        changes = list(agent_changes.get(slug, []))
        version = doc_versions.get(slug, 0)
        return {
            "changes": changes,
            "version": version,
            "agent_busy": agent_busy.is_set(),
        }


# -------------------------------------------------------------------------
# Rename
# -------------------------------------------------------------------------


def rename_document(old_slug: str, new_slug: str, version: int) -> None:
    """Move every piece of per-slug state from *old_slug* to *new_slug*.

    A rename must not drop queued work or orphan the lock. The browser is
    polling under the old slug and would never see operations left behind
    there, while the new slug would appear to have none.

    The same lock object is deliberately reused rather than a fresh one being
    created: a writer still holding the old slug's lock must serialise against
    a writer that has already moved to the new slug, as they are writing the
    same file.

    Args:
        old_slug: The slug the document is moving away from.
        new_slug: The slug the document is moving to.
        version: The version to cache for the new slug.
    """
    with _state_lock:
        pending = agent_changes.pop(old_slug, [])
        if pending:
            agent_changes.setdefault(new_slug, []).extend(pending)

        lock = doc_locks.pop(old_slug, None)
        if lock is not None:
            doc_locks[new_slug] = lock

        doc_versions.pop(old_slug, None)
        doc_versions[new_slug] = version


# -------------------------------------------------------------------------
# Busy indicator
# -------------------------------------------------------------------------


def set_agent_busy(busy: bool) -> None:
    """Set or clear the busy indicator.

    Args:
        busy: True while an agent turn is in flight, False once the response
            is ready.
    """
    if busy:
        agent_busy.set()
    else:
        agent_busy.clear()


def is_agent_busy() -> bool:
    """Return whether an agent turn is currently in flight.

    Returns:
        True if a turn is in flight.
    """
    return agent_busy.is_set()


# -------------------------------------------------------------------------
# Reset -- for tests only
# -------------------------------------------------------------------------


def reset_state() -> None:
    """Clear every container and the busy flag.

    Exists for test isolation. Nothing in the application calls it: live state
    is meant to survive for the process lifetime, and a restart recovers from
    disk anyway.
    """
    with _state_lock:
        agent_changes.clear()
        doc_versions.clear()
        doc_locks.clear()
    agent_busy.clear()
