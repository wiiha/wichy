"""Tests for the ServerConsole history ring."""

from __future__ import annotations

import threading
import time

import pytest

from wichy.config.settings import settings
from wichy.console.user import ServerConsole


def test_drain_and_history_are_disjoint():
    console = ServerConsole()
    console.print("alpha")
    console.print("beta")

    assert console.get_messages() == ["alpha", "beta"]
    assert console.read_since(0, 100) == (["alpha", "beta"], 2, False, False)
    assert console.get_messages() == []
    assert console.read_since(0, 100) == (["alpha", "beta"], 2, False, False)


def test_seq_monotonic_and_cursor():
    console = ServerConsole()
    console.print("a")
    console.print("b")
    console.print("c")

    assert console.read_since(0, 1) == (["a"], 1, True, False)
    assert console.read_since(1, 1) == (["b"], 2, True, False)
    assert console.read_since(2, 10) == (["c"], 3, False, False)


def test_eviction_drop_oldest_and_overflow_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "console_history_max", 3)
    console = ServerConsole()
    for i in range(5):
        console.print(f"m{i}")

    assert console.read_since(0, 100) == (["m2", "m3", "m4"], 5, False, True)
    assert console.read_since(4, 10) == (["m4"], 5, False, False)


def test_cap_zero_disables_ring(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "console_history_max", 0)
    console = ServerConsole()
    console.print("a")
    console.print("b")

    assert console.read_since(0, 10) == ([], 0, False, False)
    assert console.get_messages() == ["a", "b"]


def test_concurrent_print_and_reads(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "console_history_max", 2000)
    console = ServerConsole()
    total = 200
    errors: list[BaseException] = []
    reader_seen: list[set[str]] = [set(), set()]
    deadline = time.monotonic() + 10.0

    def writer(worker: int) -> None:
        try:
            for i in range(50):
                console.print(f"t-{worker}-{i}")
        except BaseException as exc:
            errors.append(exc)

    def reader(index: int) -> None:
        cursor = 0
        try:
            while len(reader_seen[index]) < total and time.monotonic() < deadline:
                texts, last_id, _, _ = console.read_since(cursor, 7)
                for text in texts:
                    if text in reader_seen[index]:
                        raise AssertionError(f"duplicate delivery: {text}")
                    reader_seen[index].add(text)
                if last_id:
                    cursor = last_id
                time.sleep(0.001)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(4)]
    threads += [threading.Thread(target=reader, args=(r,)) for r in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    expected = {f"t-{w}-{i}" for w in range(4) for i in range(50)}
    for seen in reader_seen:
        assert seen == expected


def test_drain_untouched_by_history_and_vice_versa():
    console = ServerConsole()
    drained: list[str] = []
    history: list[str] = []
    cursor = 0
    for i in range(4):
        console.print(f"m{i}")
        texts, last_id, _, _ = console.read_since(cursor, 1)
        history.extend(texts)
        cursor = last_id or cursor
        drained.extend(console.get_messages())
    console.print("tail")
    history.extend(console.read_since(cursor, 10)[0])
    drained.extend(console.get_messages())

    expected = [f"m{i}" for i in range(4)] + ["tail"]
    assert drained == expected
    assert history == expected


def test_history_survives_drain_cycling():
    console = ServerConsole()
    console.print("x1")
    console.print("x2")
    assert console.get_messages() == ["x1", "x2"]
    console.print("x3")
    console.print("x4")
    assert console.get_messages() == ["x3", "x4"]

    assert console.read_since(0, 100) == (["x1", "x2", "x3", "x4"], 4, False, False)


def test_cap_shrink_multi_drop_sets_overflowed_correctly(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "console_history_max", 2000)
    console = ServerConsole()
    for i in range(1, 11):
        console.print(f"m{i}")

    monkeypatch.setattr(settings, "console_history_max", 3)
    console.print("m11")

    assert console.read_since(5, 100) == (["m9", "m10", "m11"], 11, False, True)
    assert console.read_since(9, 100) == (["m10", "m11"], 11, False, False)


def test_cap_zero_after_prints_disables_history(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "console_history_max", 2000)
    console = ServerConsole()
    console.print("a")
    console.print("b")
    console.print("c")

    monkeypatch.setattr(settings, "console_history_max", 0)
    assert console.read_since(0, 10) == ([], 0, False, False)
    assert console.get_messages() == ["a", "b", "c"]


def test_read_since_zero_limit_still_advances():
    console = ServerConsole()
    console.print("a")
    console.print("b")
    console.print("c")

    assert console.read_since(0, 0) == (["a"], 1, True, False)
    assert console.read_since(1, 0) == (["b"], 2, True, False)


def test_cap_zero_window_signals_gap_on_reenable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "console_history_max", 2000)
    console = ServerConsole()
    console.print("a")
    console.print("b")
    console.print("c")

    monkeypatch.setattr(settings, "console_history_max", 0)
    console.print("d")
    console.print("e")

    monkeypatch.setattr(settings, "console_history_max", 3)
    console.print("f")

    assert console.read_since(3, 10) == (["f"], 6, False, True)
    assert console.read_since(6, 10) == ([], 0, False, False)
