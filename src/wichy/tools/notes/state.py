"""In-memory server state shared by the notes API and the block tools.

This module owns the process-wide coordination primitives for block documents:

- ``agent_changes`` -- slug -> queued agent operations awaiting browser pickup
- ``doc_versions`` -- slug -> current version, for fast stale-write checks
- ``doc_locks`` -- slug -> per-document lock
- ``agent_busy`` -- whether an agent turn is in flight
- ``last_injected`` -- slug -> highest version already sent to the agent's context

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
from typing import Callable, Iterable

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

# slug -> highest document version already injected into the agent's context.
#
# The change-notification route is otherwise not idempotent: the browser parks
# ops on a 503 and retries, and a retry that arrives after a lost response would
# append a second, identical summary of the same change to the agent's context.
# Bounded by the number of documents ever touched in this process, like the rest
# of this module. A restart clears it, which only means one retry may duplicate.
last_injected: dict[str, int] = {}

# Guards the three containers above. Re-entrant so a caller already inside may
# call another accessor. Never hold this across I/O.
_state_lock = threading.RLock()
# -------------------------------------------------------------------------
# Deferred change notification
# -------------------------------------------------------------------------

#: slug -> user ops held for the next notification.
#:
#: A user types in bursts: one sentence produces several editor change events,
#: and every pause longer than the browser's debounce posted its own batch. Each
#: batch was injected on arrival, so the agent received three or four messages
#: describing the SAME block, none of them saying what the text had become. The
#: buffer holds the burst instead, and one notification is delivered once the
#: edits stop.
#:
#: Ops are merged per block on arrival (see ``buffer_notification``), so a
#: sentence's worth of keystrokes costs one line per block rather than one line
#: per keystroke batch.
_pending_ops: dict[str, list[dict]] = {}

#: slug -> the version the buffered ops were computed against.
#:
#: Kept so the flushed notification carries the version of the write it
#: describes, which is what the injection idempotency check runs on.
_pending_version: dict[str, int] = {}

#: slug -> the timer counting down the quiet period for that slug.
_notify_timers: dict[str, threading.Timer] = {}

#: Delivers a slug's buffered notification once its quiet period elapses.
#:
#: Registered by the API layer, because delivering means reaching the agent and
#: this module must stay stdlib-only (the agent base class imports it). Until a
#: callback is registered nothing is delivered, which is why registration
#: happens at app setup rather than lazily.
#:
#: Returns True when the notification was delivered, False to keep the ops and
#: retry -- the browser has already been told they were accepted, so a failure
#: here must not drop them.
_notify_flush: "Callable[[str], bool] | None" = None

#: Quiet period, in seconds, before a buffered notification is delivered.
_notify_settle_seconds: float = 2.5

#: How many times one buffered notification is re-armed after a failed delivery.
#:
#: Delivery can fail (no session, a context error). Giving up on the first
#: failure would drop a notification the browser has already been told was
#: accepted, and the browser deletes its queue on that answer -- so the edit
#: would be lost with nothing reporting it. Retrying keeps the ops until they are
#: either delivered or the budget runs out, and running out is logged.
_MAX_NOTIFY_ATTEMPTS = 4


def set_notify_flush(callback: "Callable[[str], bool] | None") -> None:
    """Register the callback that delivers one slug's notification.

    Args:
        callback: Called with a slug when its quiet period elapses. It returns
            True once the notification has been delivered, or False to keep the
            ops buffered and retry. None to unregister, which is what tests use
            to keep a stray timer from reaching a later test's state.
    """
    global _notify_flush
    with _state_lock:
        _notify_flush = callback


def set_notify_settle_seconds(seconds: float) -> None:
    """Set the quiet period applied to notifications buffered from now on.

    Args:
        seconds: Time to wait for further edits before delivering. Zero delivers
            on the next timer tick rather than synchronously -- the flush still
            runs off the request thread.
    """
    global _notify_settle_seconds
    with _state_lock:
        _notify_settle_seconds = max(0.0, float(seconds))


# -------------------------------------------------------------------------
# Document locks
# -------------------------------------------------------------------------


def forget_doc_lock(slug: str) -> None:
    """Drop *slug*'s per-document lock entry, if no one else holds it.

    For a caller that created the entry but did not commit the work it was for --
    a rename that failed part-way. The entry is what ``has_state`` reads, so
    leaving it behind would claim the slug forever.

    Refuses to remove a lock another thread is inside: dropping it while held
    would hand the next caller a DIFFERENT lock object for the same file, which
    is the lost update these exist to prevent. A lock that is genuinely in use is
    therefore kept, and the caller's slug stays claimed -- which is correct,
    because the work is still happening.

    Args:
        slug: The document slug.
    """
    with _state_lock:
        lock = doc_locks.get(slug)
        if lock is None:
            return
        # acquire(blocking=False) is the only way to ask "is anyone inside this
        # lock" without waiting. A re-entrant lock is owned by the current thread
        # after its own acquire, so the release below balances it exactly.
        if not lock.acquire(blocking=False):
            return
        try:
            doc_locks.pop(slug, None)
        finally:
            lock.release()


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
    """Queue one agent operation for *slug*, collapsed per block in storage.

    Only the final state of a block is ever delivered -- the browser applies the
    last content, not a replay -- so an older operation for the same block is
    REPLACED rather than accumulated. Collapsing at write time keeps storage
    bounded by the document's block count even when the browser never
    acknowledges (a closed tab), instead of growing without limit. First-touch
    order is preserved, so the browser still sees blocks in a stable order.

    Args:
        slug: The document slug.
        op: The operation dict. Stored as-is; the caller owns its shape.
    """
    with _state_lock:
        queue = agent_changes.setdefault(slug, [])
        block_id = str(op.get("block_id") or "")
        for index, existing in enumerate(queue):
            if str(existing.get("block_id") or "") == block_id:
                queue[index] = op
                return
        queue.append(op)


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
# Injection bookkeeping
# -------------------------------------------------------------------------


def was_injected(slug: str, version: int) -> bool:
    """Whether ops at *version* have already reached the agent's context.

    The read-only half of the idempotency pair, kept separate from the recording
    half so the record is only written once the injection has actually SUCCEEDED.
    Recording first would let a failed injection suppress its own retry: the
    client would be told the notification was a duplicate of one the agent never
    received.

    Args:
        slug: The document slug.
        version: The document version the ops were computed against.

    Returns:
        True when a notification at or above this version was already injected.
    """
    with _state_lock:
        previous = last_injected.get(slug)
        return previous is not None and version <= previous


def note_injected(slug: str, version: int) -> bool:
    """Record that *version*'s notification reached the agent's context.

    A client that parks its ops on a transient failure and retries sends the same
    version again; the agent's context must not receive the same summary twice, so
    the highest injected version is remembered per slug and anything at or below
    it is treated as a duplicate by ``was_injected``. A genuinely newer version
    still injects.

    Must be called only AFTER the injection succeeded.

    Args:
        slug: The document slug.
        version: The document version the injected ops were computed against.

    Returns:
        True when this version had not been recorded before.
    """
    with _state_lock:
        previous = last_injected.get(slug)
        if previous is not None and version <= previous:
            return False
        last_injected[slug] = version
        return True


def forget_injected(slug: str) -> None:
    """Forget the last injected version for *slug*.

    Called when the slug stops naming the same document (delete, rename), so a
    document created later under a reused name cannot inherit the dead one's
    bookkeeping and have its first notification suppressed.

    Args:
        slug: The document slug.
    """
    with _state_lock:
        last_injected.pop(slug, None)


def merge_pending_ops(existing: Iterable[dict], incoming: Iterable[dict]) -> list[dict]:
    """Fold a new batch of user ops into a buffered batch, one entry per block.

    Only a block's FINAL state is worth reporting: the agent is told what the
    text became, not every intermediate keystroke. So a later op for a block
    replaces the earlier one, keeping its position, exactly as the agent-op queue
    collapses (see ``queue_agent_changes``). First-touch order is preserved so the
    message lists blocks in the order the user reached them.

    The merged entry keeps the EARLIEST ``before``. A later batch's ``before`` is
    the text an earlier batch of the same burst already produced, so keeping it
    would show a diff of the last keystroke alone ("Hello" -> "Hello world")
    rather than the whole edit ("Hel" -> "Hello world") -- which is the flood
    again, one level down.

    A block added during the burst reads as ``add`` however many updates followed,
    because the block did not exist beforehand and a diff against nothing would
    be noise. A block added and then deleted reads as ``remove``: it is gone.

    Args:
        existing: Ops already buffered, oldest first.
        incoming: The new batch to fold in.

    Returns:
        The merged ops, one per block, in first-touch order.
    """
    order: list[str] = []
    merged: dict[str, dict] = {}
    for op in list(existing) + list(incoming):
        key = str(op.get("block_id") or "")
        first = merged.get(key)
        if first is None:
            order.append(key)
            merged[key] = dict(op)
            continue
        combined = dict(op)
        # A later op may carry none of the descriptive fields a move lacks, so
        # they are carried forward rather than lost.
        for field in ("data", "block_type", "index"):
            if field not in combined and field in first:
                combined[field] = first[field]
        if str(first.get("op")) == "add" and str(op.get("op")) != "remove":
            # Still an addition: the block did not exist before the burst, so a
            # diff against a previous state would be against nothing.
            combined["op"] = "add"
            combined.pop("before", None)
        elif "before" in first:
            # Keep the EARLIEST before, including on a removal: what was deleted
            # is the text the burst started from, not the intermediate state.
            combined["before"] = first["before"]
        merged[key] = combined
    return [merged[key] for key in order]


def buffer_notification(slug: str, ops: Iterable[dict], version: int) -> None:
    """Hold *slug*'s ops and (re)start its quiet period.

    Called from the request thread. Nothing is delivered here: the ops wait until
    the user stops editing. Each call restarts the timer, so a burst of batches
    becomes one delivery.

    Args:
        slug: The document slug.
        ops: The user's ops from this request.
        version: The document version the ops were computed against. The LAST
            batch's version wins, because the ops buffered so far describe the
            document as it now stands.
    """
    with _state_lock:
        merged = merge_pending_ops(_pending_ops.get(slug, []), ops)
        _pending_ops[slug] = merged
        _pending_version[slug] = version
        # A fresh edit gets a fresh delivery budget: the counter exists to stop an
        # undeliverable notification retrying forever, not to punish a later edit
        # for an earlier failure.
        _notify_attempts.pop(slug, None)
        _arm_notify_timer_locked(slug)


#: slug -> how many delivery attempts its current buffer has had.
#:
#: Delivery can fail (no session yet, a context error). Each failure re-arms the
#: timer rather than dropping the ops, because the browser has already been told
#: they were accepted and has deleted its copy -- losing them here loses the edit.
#: The counter is what stops that retrying forever, and is reset whenever new ops
#: arrive, since a fresh edit deserves a fresh budget.
_notify_attempts: dict[str, int] = {}


def _arm_notify_timer_locked(slug: str) -> None:
    """Restart *slug*'s quiet-period timer. Caller holds ``_state_lock``."""
    existing = _notify_timers.pop(slug, None)
    if existing is not None:
        existing.cancel()
    delay = _notify_settle_seconds
    timer = threading.Timer(delay, _fire_notification, args=(slug,))
    # A timer thread must never hold the process open: it is background work
    # whose remaining lifetime is a debounce interval, not a task to wait on.
    timer.daemon = True
    _notify_timers[slug] = timer
    timer.start()


def _fire_notification(slug: str) -> None:
    """Deliver *slug*'s buffered notification, or re-arm to try again.

    Runs on the timer's own thread, never the request thread, so a slow agent
    cannot delay the HTTP response.

    A successful delivery clears the buffer. A failed one re-arms the timer, up
    to a bounded number of attempts, because the browser has already been told the
    ops were accepted and has discarded its copy -- dropping them here is how an
    edit disappears with nothing reporting it.
    """
    with _state_lock:
        delivered_batch = _pending_ops.get(slug)
    delivered = deliver_pending_notification(slug)
    with _state_lock:
        _notify_timers.pop(slug, None)
        if delivered:
            # Clear only what was delivered. A batch that arrived while this one
            # was in flight is newer and must survive to its own notification, or
            # the newest edits are the ones that go missing.
            if (
                delivered_batch is not None
                and _pending_ops.get(slug) is delivered_batch
            ):
                _pending_ops.pop(slug, None)
                _pending_version.pop(slug, None)
                _notify_attempts.pop(slug, None)
            else:
                # Newer ops are waiting; report those too, after another pause.
                _arm_notify_timer_locked(slug)
            return
        if slug not in _pending_ops:
            return
        attempts = _notify_attempts.get(slug, 0) + 1
        _notify_attempts[slug] = attempts
        if attempts >= _MAX_NOTIFY_ATTEMPTS:
            print(
                f"[wichy] gave up delivering a note change notification for "
                f"'{slug}' after {attempts} attempts; the edits stay queued."
            )
            return
        _arm_notify_timer_locked(slug)


def deliver_pending_notification(slug: str) -> bool:
    """Inject *slug*'s buffered notification into the agent's context.

    Does NOT touch the buffer: the caller decides whether to clear it, because a
    delivery that happens while new ops are arriving must not discard them. See
    ``_fire_notification`` and ``flush_pending_notification``.

    Args:
        slug: The document slug.

    Returns:
        True when the buffered ops were delivered or there were none to deliver,
        False when delivery failed and should be retried.
    """
    ops = peek_pending_notification(slug)
    if not ops:
        return True
    flush = _notify_flush
    if flush is None:
        return False
    try:
        return bool(flush(slug))
    except Exception as e:  # pragma: no cover - depends on agent internals
        print(f"[wichy] could not deliver a note change notification: {e}")
        return False


def flush_pending_notification(slug: str) -> bool:
    """Deliver *slug*'s buffered notification immediately, cancelling its timer.

    The synchronous entry point. The timer uses it, and so do tests and any
    caller that needs the delivery to have happened before returning rather than
    after a quiet period.

    Args:
        slug: The document slug.

    Returns:
        True when the ops were delivered, or there was nothing to deliver.
    """
    with _state_lock:
        timer = _notify_timers.pop(slug, None)
        ops = _pending_ops.get(slug)
    if timer is not None:
        timer.cancel()
    if ops is None:
        return True
    delivered = deliver_pending_notification(slug)
    if delivered:
        with _state_lock:
            # Clear only what was delivered: a batch that arrived during delivery
            # is newer than this one and must survive to its own notification.
            if _pending_ops.get(slug) is ops:
                _pending_ops.pop(slug, None)
                _pending_version.pop(slug, None)
                _notify_attempts.pop(slug, None)
    return delivered


def peek_pending_notification(slug: str) -> list[dict]:
    """Return a copy of *slug*'s buffered ops without clearing them.

    Args:
        slug: The document slug.

    Returns:
        The buffered ops, oldest first, one per block.
    """
    with _state_lock:
        return list(_pending_ops.get(slug, []))


def pending_notification_version(slug: str) -> int:
    """Return the version the buffered notification describes, or 0."""
    with _state_lock:
        return _pending_version.get(slug, 0)


def forget_pending_notification(slug: str) -> None:
    """Drop and cancel *slug*'s buffered notification.

    Called when the slug stops naming the document the ops were made against
    (delete, rename). The browser is polling under the old slug, so a
    notification left behind there would describe a document that is gone, and
    its delivery would be attributed to the wrong name.

    Args:
        slug: The document slug.
    """
    with _state_lock:
        _pending_ops.pop(slug, None)
        _pending_version.pop(slug, None)
        _notify_attempts.pop(slug, None)
        timer = _notify_timers.pop(slug, None)
    if timer is not None:
        timer.cancel()


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


def has_state(slug: str) -> bool:
    """Whether *slug* has any in-memory state of its own.

    A caller about to move a document ONTO this slug needs to know before it
    touches a single file: the refusal has to happen while the operation is still
    a no-op, or a rename that fails part-way leaves the files moved and the state
    behind. Checked under the same lock ``rename_document`` takes.

    Args:
        slug: The document slug.

    Returns:
        True when a lock, queued ops, a buffered notification, a cached version
        or an injection mark exists for the slug.
    """
    with _state_lock:
        return (
            slug in doc_locks
            or slug in agent_changes
            or slug in _pending_ops
            or slug in doc_versions
            or slug in last_injected
        )


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

        # NEVER clobber the target's lock object. Overwriting it would hand a
        # writer that already fetched the old object a DIFFERENT lock from the
        # next caller, which is the lost update the lock exists to prevent. When
        # the target has one it is the caller's own (it holds it, having taken
        # ``document_lock(new_slug)``), so the safe answer is to keep it and drop
        # the old slug's.
        #
        # A target that already belonged to another document is refused BEFORE
        # any of this, by ``has_state`` in the rename path -- where the refusal
        # can still leave the whole operation a no-op. Merging two documents'
        # state is not coherent, so it is never attempted here.
        lock = doc_locks.pop(old_slug, None)
        if lock is not None and new_slug not in doc_locks:
            doc_locks[new_slug] = lock

        doc_versions.pop(old_slug, None)
        doc_versions[new_slug] = version

        # Follows the document, so the next notification under the new name is
        # not suppressed as a repeat of one made under the old name.
        injected = last_injected.pop(old_slug, None)
        if injected is not None:
            last_injected.pop(new_slug, None)
            last_injected[new_slug] = injected

        # A buffered notification follows its document too. Left under the old
        # slug it would be delivered as a change to a document that no longer has
        # that name -- and the agent would be told to edit the wrong one.
        buffered = _pending_ops.pop(old_slug, None)
        buffered_version = _pending_version.pop(old_slug, None)
        timer = _notify_timers.pop(old_slug, None)
        if buffered is not None:
            _pending_ops[new_slug] = merge_pending_ops(
                _pending_ops.get(new_slug, []), buffered
            )
            if buffered_version is not None:
                _pending_version[new_slug] = buffered_version
            _arm_notify_timer_locked(new_slug)
    # Cancelled outside the lock, like ``reset_state``: a timer that already fired
    # is waiting for this lock, so cancelling it under the lock would deadlock.
    if timer is not None:
        timer.cancel()


# -------------------------------------------------------------------------
# Busy indicator
# -------------------------------------------------------------------------


#: How many turns are in flight, and the lock that keeps the count honest.
#:
#: A single Event cannot describe nesting: agent turns nest (a sub-agent runs
#: inside the outer turn), and whichever turn ended first cleared the flag while
#: the other was still running. The count turns "is anything working" into a
#: question with a correct answer for any depth.
_busy_lock = threading.Lock()
_busy_turns = 0


def turn_begun() -> None:
    """Count one agent turn in, and mark the agent busy."""
    global _busy_turns
    with _busy_lock:
        _busy_turns += 1
        agent_busy.set()


def turn_ended() -> None:
    """Count one agent turn out, clearing busy only when none are left.

    Safe to call for a nested turn: the outer turn is still counted, so the
    indicator stays on.
    """
    global _busy_turns
    with _busy_lock:
        # max(0, ...) rather than a bare decrement: an unmatched end (a turn
        # whose start was missed) must not drive the count negative, which would
        # take several later turns to climb back to zero and leave the indicator
        # off while work is happening.
        _busy_turns = max(0, _busy_turns - 1)
        if _busy_turns == 0:
            agent_busy.clear()


def set_agent_busy(busy: bool) -> None:
    """Set or clear the busy indicator unconditionally.

    For tests and for callers that know the whole picture. Turn observers use
    :func:`turn_begun` / :func:`turn_ended` instead, which respect nesting.

    Args:
        busy: True while an agent turn is in flight, False once the response
            is ready.
    """
    global _busy_turns
    with _busy_lock:
        _busy_turns = 1 if busy else 0
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
    global _busy_turns
    with _state_lock:
        agent_changes.clear()
        doc_versions.clear()
        doc_locks.clear()
        last_injected.clear()
        pending_timers = list(_notify_timers.values())
        _pending_ops.clear()
        _pending_version.clear()
        _notify_attempts.clear()
        _notify_timers.clear()
    # Cancelled OUTSIDE the lock: a timer that has already fired is waiting for
    # this lock inside ``_fire_notification``, so cancelling while holding it
    # would deadlock the reset against the very thread it is trying to stop.
    for timer in pending_timers:
        timer.cancel()
    with _busy_lock:
        _busy_turns = 0
    agent_busy.clear()
