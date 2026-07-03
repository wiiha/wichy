"""Skill directory auto-reloader using watchdog.

Provides a lightweight observer that reloads all skills when any file under the
configured skills directories changes. Runs as a daemon thread in both CLI and
server modes.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, List

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from wichy.config import settings
from wichy.skills.loader import SkillLoader
from wichy.skills.registry import SkillRegistry


class _SkillReloadHandler(FileSystemEventHandler):
    """Watchdog event handler that triggers skill reloads."""

    def __init__(
        self,
        loader_factory: Callable[[], SkillLoader],
        debounce_seconds: float = 1.0,
    ) -> None:
        self._loader_factory = loader_factory
        self._debounce_seconds = debounce_seconds
        self._pending = False
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def _reload(self) -> None:
        """Perform the actual reload and reset pending flag."""
        with self._lock:
            self._pending = False
            self._timer = None
        try:
            loader = self._loader_factory()
            registry = SkillRegistry()
            registry.clear()
            loader.load_all_skills()
        except Exception as exc:  # pragma: no cover - observer thread
            # Swallow reload errors so a malformed skill does not crash the app.
            # The next edit will retry automatically.
            import logging

            logging.getLogger(__name__).warning(f"Skill auto-reload failed: {exc}")

    def _schedule_reload(self) -> None:
        """Debounce reload requests."""
        with self._lock:
            self._pending = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._reload)
            self._timer.daemon = True
            self._timer.start()

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Ignore directories themselves and temporary editor files.
        if event.is_directory:
            return
        if event.event_type == "closed_no_write":
            return
        if event.src_path and Path(event.src_path).name.startswith("."):
            return
        self._schedule_reload()


class SkillReloader:
    """Watch configured skills directories and reload skills on changes."""

    _observer: Observer | None = None
    _lock = threading.Lock()
    _override_local_dir: Path | None = None
    _override_home_dir: Path | None = None

    @classmethod
    def set_source_dirs(cls, local_dir: Path | None, home_dir: Path | None) -> None:
        """Override the directories to watch (mainly for tests).

        Use ``None`` to fall back to ``settings``.
        """
        with cls._lock:
            cls._override_local_dir = local_dir
            cls._override_home_dir = home_dir

    @classmethod
    def _source_dirs(cls) -> List[Path]:
        """Return directories to watch in precedence order."""
        dirs: List[Path] = []
        local_dir = cls._override_local_dir or settings.skills_dir_local
        if local_dir.exists():
            dirs.append(local_dir)
        home_dir = cls._override_home_dir or settings.skills_dir_home
        if home_dir.exists() and home_dir.resolve() != local_dir.resolve():
            dirs.append(home_dir)
        return dirs

    @classmethod
    def start(cls, debounce_seconds: float = 1.0) -> None:
        """Start watching skills directories.

        Safe to call multiple times; subsequent calls are ignored while an
        observer is already running.
        """
        with cls._lock:
            if cls._observer is not None and cls._observer.is_alive():
                return

            source_dirs = cls._source_dirs()
            if not source_dirs:
                return

            def factory() -> SkillLoader:
                local_dir = cls._override_local_dir
                home_dir = cls._override_home_dir
                if local_dir is not None or home_dir is not None:
                    return SkillLoader(
                        project_skills_dir=local_dir,
                        home_skills_dir=home_dir,
                    )
                return SkillLoader()

            handler = _SkillReloadHandler(
                loader_factory=factory, debounce_seconds=debounce_seconds
            )
            observer = Observer()
            for directory in source_dirs:
                observer.schedule(handler, str(directory), recursive=True)
            observer.daemon = True
            observer.start()
            cls._observer = observer

            # Give the observer a moment to start so tests can rely on it.
            time.sleep(0.05)

    @classmethod
    def stop(cls) -> None:
        """Stop the running observer, if any, and clear test overrides."""
        with cls._lock:
            # Cancel any pending debounced reload before the observer goes away.
            cls._cancel_pending_reload()

            observer = cls._observer
            cls._override_local_dir = None
            cls._override_home_dir = None
            if observer is None:
                return
            observer.stop()
            observer.join(timeout=2.0)
            cls._observer = None

    @classmethod
    def _cancel_pending_reload(cls) -> None:
        """Cancel any pending debounced reload timer."""
        # The handler instance is private; look it up from the observer schedules.
        observer = cls._observer
        if observer is None:
            return
        for emitter in getattr(observer, "_emitters", []):
            handler = getattr(emitter, "event_handler", None)
            if isinstance(handler, _SkillReloadHandler):
                with handler._lock:
                    handler._pending = False
                    if handler._timer is not None:
                        handler._timer.cancel()
                        handler._timer = None

    @classmethod
    def is_running(cls) -> bool:
        """Return True if the observer thread is active."""
        with cls._lock:
            return cls._observer is not None and cls._observer.is_alive()
