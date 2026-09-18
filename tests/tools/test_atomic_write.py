"""
Test cases for file_safety.atomic_write.

Covers temp-file uniqueness and cleanup, mode handling for both new and
existing files, parent-directory creation, encoding pass-through, and that a
concurrent reader never observes a partially written file.
"""

import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from wichy.tools.file_safety import _TMP_PREFIX, atomic_write


def _leftover_temps(directory) -> list[str]:
    return [n for n in os.listdir(directory) if n.startswith(_TMP_PREFIX)]


def test_atomic_write_creates_and_overwrites(tmp_path):
    """Basic write, then overwrite with shorter content (no stale tail)."""
    p = tmp_path / "f.txt"
    atomic_write(str(p), "hello")
    assert p.read_text() == "hello"

    atomic_write(str(p), "hi")
    assert p.read_text() == "hi"


def test_atomic_write_creates_parent_dirs(tmp_path):
    """write_file relied on makedirs; the helper must keep doing it."""
    p = tmp_path / "deep" / "nested" / "dir" / "file.txt"
    atomic_write(str(p), "nested")
    assert p.read_text() == "nested"


def test_atomic_write_preserves_existing_mode(tmp_path):
    """An existing file's permission bits must survive the inode swap."""
    p = tmp_path / "f.txt"
    p.write_text("old")
    os.chmod(p, 0o600)

    atomic_write(str(p), "new")

    assert p.read_text() == "new"
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_atomic_write_new_file_honours_umask(tmp_path):
    """A NEW file must not become private.

    open(path, "w") honoured the umask (0o644 at umask 022); tempfile.mkstemp
    would force 0o600. Guard the regression.
    """
    old_umask = os.umask(0o022)
    try:
        p = tmp_path / "new.txt"
        atomic_write(str(p), "x")
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o644
    finally:
        os.umask(old_umask)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    """Success must leave the directory containing only the target."""
    p = tmp_path / "f.txt"
    atomic_write(str(p), "x")
    assert _leftover_temps(tmp_path) == []


def test_atomic_write_cleans_temp_on_failure(tmp_path, monkeypatch):
    """A failed write must not leak its temp file."""
    p = tmp_path / "f.txt"

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        atomic_write(str(p), "x")

    assert _leftover_temps(tmp_path) == []
    assert not p.exists()


def test_atomic_write_accepts_locale_default_encoding(tmp_path):
    """encoding=None must mean the platform default (write_file's behaviour)."""
    p = tmp_path / "f.txt"
    atomic_write(str(p), "plain ascii", None)
    assert p.read_text() == "plain ascii"


def test_atomic_write_respects_encoding(tmp_path):
    p = tmp_path / "f.txt"
    atomic_write(str(p), "line 1\nline 2\n", "utf-16")
    assert p.read_text(encoding="utf-16") == "line 1\nline 2\n"


def test_atomic_write_over_symlink_replaces_link(tmp_path):
    """Documented behaviour change: the link is replaced, not written through."""
    real = tmp_path / "real.txt"
    real.write_text("original")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    atomic_write(str(link), "new")

    assert not link.is_symlink()
    assert link.read_text() == "new"
    assert real.read_text() == "original"


def test_atomic_write_concurrent_writers_never_torn(tmp_path):
    """Every concurrent write must be observed whole, never spliced."""
    p = tmp_path / "f.txt"
    payloads = [f"payload-{i}-" + "x" * 2000 for i in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(atomic_write, str(p), s) for s in payloads]
        for f in futs:
            f.result(timeout=30)

    final = p.read_text()
    assert final in payloads  # exactly one writer's content, undamaged
    assert _leftover_temps(tmp_path) == []


def test_atomic_write_readers_see_whole_file(tmp_path):
    """A reader must never observe a partially written file."""
    p = tmp_path / "f.txt"
    short, long = "a" * 50, "b" * 200_000
    atomic_write(str(p), short)

    stop = threading.Event()
    counts = {"short": 0, "long": 0, "reads": 0}

    def reader():
        while not stop.is_set():
            content = p.read_text()
            assert content in (short, long), f"torn read: {len(content)} chars"
            # Compare by value: read_text returns a fresh object every time.
            counts["short" if content == short else "long"] += 1
            counts["reads"] += 1

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for _ in range(50):
            atomic_write(str(p), long)
            atomic_write(str(p), short)
            # Hold the short state briefly so the reader is guaranteed to
            # observe both versions; a 50-char read window is otherwise too
            # small to hit reliably.
            time.sleep(0.002)
    finally:
        stop.set()
        t.join(timeout=10)

    assert not t.is_alive()
    # Guard against a vacuous pass: the reader must actually have observed
    # both versions while the writer was swapping them.
    assert counts["reads"] > 0, "reader never ran"
    assert (
        counts["short"] > 0 and counts["long"] > 0
    ), f"did not observe both versions: {counts}"


def test_atomic_write_overwrites_read_only_file_when_dir_writable(tmp_path):
    """Documented: os.replace needs directory permission, not file permission.

    Pinning this so the semantic is a decision rather than an accident. It
    matches the repo's other atomic writers (context/handler.py,
    config/backend_resolver.py).
    """
    p = tmp_path / "f.txt"
    p.write_text("old")
    os.chmod(p, 0o444)

    atomic_write(str(p), "new")

    assert p.read_text() == "new"
    # The read-only bit is carried over, so the file is not left writable.
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o444


def test_atomic_write_fails_loudly_on_read_only_directory(tmp_path):
    """The failure path must stay loud: no silent loss, no temp leak."""
    sub = tmp_path / "sub"
    sub.mkdir()
    p = sub / "f.txt"
    p.write_text("old")
    os.chmod(sub, 0o555)
    try:
        with pytest.raises(OSError):
            atomic_write(str(p), "new")
        assert p.read_text() == "old"
        assert _leftover_temps(sub) == []
    finally:
        os.chmod(sub, 0o755)
