"""Focused tests for the tracked pending-message input queue."""

from __future__ import annotations

import threading
from queue import Empty
from typing import Any

import pytest

from wichy.wichy_server.input_queue import TrackedInputQueue


def _start_gated_consumer(
    queue: TrackedInputQueue,
    gate: threading.Event,
    received: list[str],
    errors: list[BaseException],
    consumed: threading.Event,
) -> threading.Thread:
    def consume() -> None:
        gate.wait(timeout=3.0)
        try:
            received.append(queue.get_nowait())
        except BaseException as exc:
            errors.append(exc)
        finally:
            consumed.set()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    return thread


# -- FIFO and string semantics ----------------------------------------------


def test_get_and_get_nowait_return_original_strings_in_fifo_order():
    queue = TrackedInputQueue()
    queue.put("first")
    queue.put_nowait("  padded text  ")
    queue.put("/help")

    assert queue.get_nowait() == "first"
    assert queue.get() == "  padded text  "
    assert queue.get_nowait() == "/help"


def test_empty_and_timeout_behavior_is_normal():
    queue = TrackedInputQueue()

    with pytest.raises(Empty):
        queue.get_nowait()
    with pytest.raises(Empty):
        queue.get(timeout=0.05)


# -- IDs ---------------------------------------------------------------------


def test_put_returns_unique_non_empty_ids_across_instances():
    first_queue = TrackedInputQueue()
    second_queue = TrackedInputQueue()
    ids = [first_queue.put(f"a{i}") for i in range(10)]
    ids += [second_queue.put_nowait(f"b{i}") for i in range(10)]

    assert all(isinstance(message_id, str) and message_id for message_id in ids)
    assert len(set(ids)) == len(ids)


# -- Snapshots ---------------------------------------------------------------


def test_snapshot_is_ordered_repeatable_and_non_destructive():
    queue = TrackedInputQueue()
    first_id = queue.put("a")
    second_id = queue.put_nowait("b")
    third_id = queue.put("c")

    first = queue.snapshot()
    second = queue.snapshot()

    assert [message.id for message in first] == [first_id, second_id, third_id]
    assert [message.line for message in first] == ["a", "b", "c"]
    assert first == second
    assert queue.qsize() == 3

    assert queue.get_nowait() == "a"
    assert [message.line for message in queue.snapshot()] == ["b", "c"]


def test_snapshot_of_empty_queue_is_empty():
    assert TrackedInputQueue().snapshot() == []


# -- Edit --------------------------------------------------------------------


def test_edit_first_entry_returns_position_zero_and_keeps_id():
    queue = TrackedInputQueue()
    first_id = queue.put("original")
    second_id = queue.put("neighbor")

    assert queue.edit(first_id, "replacement") == 0

    messages = queue.snapshot()
    assert messages[0].id == first_id
    assert messages[0].line == "replacement"
    assert messages[1].id == second_id
    assert messages[1].line == "neighbor"


def test_edit_middle_entry_is_verbatim_and_leaves_neighbors_unchanged():
    queue = TrackedInputQueue()
    first_id = queue.put("a")
    middle_id = queue.put("b")
    last_id = queue.put("c")

    assert queue.edit(middle_id, "  B with spaces  ") == 1

    messages = queue.snapshot()
    assert [message.id for message in messages] == [first_id, middle_id, last_id]
    assert [message.line for message in messages] == ["a", "  B with spaces  ", "c"]
    assert queue.get_nowait() == "a"
    assert queue.get_nowait() == "  B with spaces  "


def test_edit_unknown_id_returns_none_and_leaves_queue_unchanged():
    queue = TrackedInputQueue()
    queue.put("a")
    queue.put("b")

    assert queue.edit("not-an-id", "x") is None
    assert [message.line for message in queue.snapshot()] == ["a", "b"]


def test_edit_consumed_id_returns_none_and_never_requeues():
    queue = TrackedInputQueue()
    consumed_id = queue.put("consumed")
    queue.put("still-pending")

    assert queue.get_nowait() == "consumed"

    assert queue.edit(consumed_id, "trying to edit") is None
    assert [message.line for message in queue.snapshot()] == ["still-pending"]
    assert queue.get_nowait() == "still-pending"
    with pytest.raises(Empty):
        queue.get_nowait()


# -- Delete ------------------------------------------------------------------


def test_delete_preserves_order_and_repeat_delete_returns_false():
    queue = TrackedInputQueue()
    queue.put("a")
    middle_id = queue.put("b")
    queue.put("c")

    assert queue.delete(middle_id) is True
    assert [message.line for message in queue.snapshot()] == ["a", "c"]
    assert queue.delete(middle_id) is False
    assert queue.delete("not-an-id") is False
    assert queue.get_nowait() == "a"
    assert queue.get_nowait() == "c"


def test_delete_consumed_id_returns_false_and_never_requeues():
    queue = TrackedInputQueue()
    consumed_id = queue.put("consumed")
    queue.put("still-pending")

    assert queue.get_nowait() == "consumed"

    assert queue.delete(consumed_id) is False
    assert [message.line for message in queue.snapshot()] == ["still-pending"]
    assert queue.get_nowait() == "still-pending"
    with pytest.raises(Empty):
        queue.get_nowait()


def test_delete_finishes_join_waiter_and_counter_never_underflows():
    queue = TrackedInputQueue()
    only_id = queue.put("only")
    waiting = threading.Event()
    finished = threading.Event()
    original_wait = queue.all_tasks_done.wait

    def observed_wait(timeout: float | None = None) -> Any:
        waiting.set()
        return original_wait(timeout)

    def wait_for_join() -> None:
        queue.join()
        finished.set()

    queue.all_tasks_done.wait = observed_wait  # type: ignore[method-assign]
    thread = threading.Thread(target=wait_for_join, daemon=True)
    try:
        thread.start()
        assert waiting.wait(timeout=2.0)
        assert queue.unfinished_tasks == 1
        assert queue.delete(only_id) is True
    finally:
        queue.all_tasks_done.wait = original_wait  # type: ignore[method-assign]

    assert finished.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert queue.unfinished_tasks == 0

    assert queue.delete(only_id) is False
    assert queue.delete("not-an-id") is False
    assert queue.unfinished_tasks == 0
    queue.join()


def test_bounded_producer_wakes_after_deletion():
    queue = TrackedInputQueue(maxsize=1)
    first_id = queue.put("first")
    waiting = threading.Event()
    produced: list[str] = []
    errors: list[BaseException] = []
    original_wait = queue.not_full.wait

    def observed_wait(timeout: float | None = None) -> Any:
        waiting.set()
        return original_wait(timeout)

    def produce() -> None:
        try:
            produced.append(queue.put("second"))
        except BaseException as exc:
            errors.append(exc)

    queue.not_full.wait = observed_wait  # type: ignore[method-assign]
    thread = threading.Thread(target=produce, daemon=True)
    try:
        thread.start()
        assert waiting.wait(timeout=2.0)
        assert queue.full()
        assert queue.delete(first_id) is True
    finally:
        queue.not_full.wait = original_wait  # type: ignore[method-assign]

    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert len(produced) == 1
    assert produced[0] and produced[0] != first_id
    assert queue.get_nowait() == "second"


# -- Consume versus edit/delete ---------------------------------------------


def test_edit_wins_while_consumer_is_gated_and_consumer_receives_edit():
    queue = TrackedInputQueue()
    message_id = queue.put("original")
    gate = threading.Event()
    received: list[str] = []
    errors: list[BaseException] = []
    consumed = threading.Event()
    thread = _start_gated_consumer(queue, gate, received, errors, consumed)

    try:
        assert queue.edit(message_id, "edited") == 0
    finally:
        gate.set()

    assert consumed.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert received == ["edited"]
    assert queue.snapshot() == []


def test_consume_wins_edit_returns_none_without_requeue():
    queue = TrackedInputQueue()
    message_id = queue.put("original")
    gate = threading.Event()
    gate.set()
    received: list[str] = []
    errors: list[BaseException] = []
    consumed = threading.Event()
    thread = _start_gated_consumer(queue, gate, received, errors, consumed)

    assert consumed.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert received == ["original"]

    assert queue.edit(message_id, "edited") is None
    assert queue.snapshot() == []
    assert received == ["original"]
    with pytest.raises(Empty):
        queue.get_nowait()


def test_delete_wins_while_consumer_is_gated_and_removed_entry_is_never_seen():
    queue = TrackedInputQueue()
    message_id = queue.put("doomed")
    gate = threading.Event()
    received: list[str] = []
    errors: list[BaseException] = []
    consumed = threading.Event()
    thread = _start_gated_consumer(queue, gate, received, errors, consumed)

    assert queue.delete(message_id) is True
    queue.put("wake")
    gate.set()

    assert consumed.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert received == ["wake"]
    assert "doomed" not in received
    assert queue.snapshot() == []


def test_consume_wins_delete_returns_false_and_keeps_consumed_text():
    queue = TrackedInputQueue()
    message_id = queue.put("original")
    gate = threading.Event()
    gate.set()
    received: list[str] = []
    errors: list[BaseException] = []
    consumed = threading.Event()
    thread = _start_gated_consumer(queue, gate, received, errors, consumed)

    assert consumed.wait(timeout=2.0)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert received == ["original"]

    assert queue.delete(message_id) is False
    assert queue.snapshot() == []
    assert received == ["original"]
    with pytest.raises(Empty):
        queue.get_nowait()


# -- Real queue-mutex contention ---------------------------------------------


class _MutexHoldingConsumerQueue(TrackedInputQueue):
    """Queue whose consumer pauses while Queue.get holds the real mutex."""

    def __init__(self) -> None:
        super().__init__()
        self.get_entered = threading.Event()
        self.release_get = threading.Event()

    def _get(self) -> Any:
        # Queue.get invokes _get while holding self.mutex, so pausing here
        # makes snapshot/edit/delete contend on that same production mutex.
        self.get_entered.set()
        self.release_get.wait(timeout=5.0)
        return super()._get()


def _start_gated_operation(
    operation: Any,
    started: threading.Event,
    done: threading.Event,
    results: list[Any],
    errors: list[BaseException],
) -> threading.Thread:
    def run() -> None:
        started.set()
        try:
            results.append(operation())
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_snapshot_and_edit_contend_on_consumer_mutex():
    queue = _MutexHoldingConsumerQueue()
    first_id = queue.put("first")
    second_id = queue.put("second")

    received: list[str] = []
    consumer_errors: list[BaseException] = []
    snapshot_started = threading.Event()
    snapshot_done = threading.Event()
    edit_started = threading.Event()
    edit_done = threading.Event()
    snapshot_results: list[Any] = []
    edit_results: list[Any] = []
    operation_errors: list[BaseException] = []

    def consume() -> None:
        try:
            received.append(queue.get())
        except BaseException as exc:
            consumer_errors.append(exc)

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    snapshot_thread: threading.Thread | None = None
    edit_thread: threading.Thread | None = None
    try:
        assert queue.get_entered.wait(timeout=2.0)
        snapshot_thread = _start_gated_operation(
            queue.snapshot,
            snapshot_started,
            snapshot_done,
            snapshot_results,
            operation_errors,
        )
        edit_thread = _start_gated_operation(
            lambda: queue.edit(second_id, "edited"),
            edit_started,
            edit_done,
            edit_results,
            operation_errors,
        )
        assert snapshot_started.wait(timeout=2.0)
        assert edit_started.wait(timeout=2.0)
        # The consumer is still inside _get holding the mutex, so neither
        # operation can finish until the consumer is released.
        assert not snapshot_done.wait(timeout=0.2)
        assert not edit_done.wait(timeout=0.2)
    finally:
        queue.release_get.set()

    for thread in (consumer, snapshot_thread, edit_thread):
        if thread is not None:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

    assert consumer_errors == []
    assert operation_errors == []
    assert received == ["first"]
    assert first_id != second_id
    assert edit_results == [0]

    assert len(snapshot_results) == 1
    observed = [(message.id, message.line) for message in snapshot_results[0]]
    assert observed in (
        [(second_id, "second")],
        [(second_id, "edited")],
    )
    assert [(m.id, m.line) for m in queue.snapshot()] == [(second_id, "edited")]
    assert queue.get_nowait() == "edited"
    with pytest.raises(Empty):
        queue.get_nowait()


def test_delete_contends_on_consumer_mutex():
    queue = _MutexHoldingConsumerQueue()
    queue.put("first")
    doomed_id = queue.put("doomed")

    received: list[str] = []
    consumer_errors: list[BaseException] = []
    delete_started = threading.Event()
    delete_done = threading.Event()
    delete_results: list[Any] = []
    operation_errors: list[BaseException] = []

    def consume() -> None:
        try:
            received.append(queue.get())
        except BaseException as exc:
            consumer_errors.append(exc)

    consumer = threading.Thread(target=consume, daemon=True)
    consumer.start()
    delete_thread: threading.Thread | None = None
    try:
        assert queue.get_entered.wait(timeout=2.0)
        delete_thread = _start_gated_operation(
            lambda: queue.delete(doomed_id),
            delete_started,
            delete_done,
            delete_results,
            operation_errors,
        )
        assert delete_started.wait(timeout=2.0)
        # Deletion cannot run while Queue.get holds the mutex around _get.
        assert not delete_done.wait(timeout=0.2)
    finally:
        queue.release_get.set()

    for thread in (consumer, delete_thread):
        if thread is not None:
            thread.join(timeout=2.0)
            assert not thread.is_alive()

    assert consumer_errors == []
    assert operation_errors == []
    assert received == ["first"]
    assert delete_results == [True]
    assert queue.snapshot() == []
    assert queue.delete(doomed_id) is False
    with pytest.raises(Empty):
        queue.get_nowait()
