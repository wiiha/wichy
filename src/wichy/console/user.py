"""Thread-safe user console with queue-based output and pause/resume."""

from __future__ import annotations

import queue
import threading
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from rich.console import Console


@dataclass
class _PrintItem:
    """Encapsulates a single print request."""

    method: str  # 'print', 'log', 'rule', etc.
    args: tuple
    kwargs: dict


class _FlushSentinel:
    """Sentinel injected by flush() to signal when the queue is drained."""

    def __init__(self, done_event: threading.Event):
        self.done_event = done_event


class ThreadSafeConsole:
    """
    A thread-safe console wrapper that serializes all output through a queue.

    Features:
    - All print() calls from any thread go through a single queue
    - Dedicated output thread handles actual Rich Console rendering
    - Pause/resume mechanism buffers output during user interactions
    - Clean context manager API for pause/resume
    """

    def __init__(self, rich_console: Optional[Console] = None):
        self._rich_console = rich_console or Console(quiet=False)
        self._queue: queue.Queue[Optional[_PrintItem]] = queue.Queue(maxsize=1000)
        self._output_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        # Pause/resume state
        self._state_lock = threading.Lock()
        self._pause_count: int = 0
        self._pause_buffer: list[_PrintItem] = []

        self._started = False

    def _ensure_started(self) -> None:
        """Lazy initialization of output thread."""
        if self._started:
            return
        with self._state_lock:
            if self._started:
                return
            self._shutdown_event.clear()
            self._output_thread = threading.Thread(
                target=self._output_loop,
                name="console-output",
                daemon=True,
            )
            self._output_thread.start()
            self._started = True

    def shutdown(self, timeout: float = 2.0) -> None:
        """Gracefully shut down the output thread."""
        if not self._started:
            return
        self._shutdown_event.set()
        self._queue.put(None)  # Sentinel
        if self._output_thread and self._output_thread.is_alive():
            self._output_thread.join(timeout=timeout)
        self._started = False
        # Reset state for potential restart
        with self._state_lock:
            self._pause_count = 0
            self._pause_buffer = []
        self._queue = queue.Queue(maxsize=1000)  # Fresh queue

    def _output_loop(self) -> None:
        """Main output thread loop."""
        while not self._shutdown_event.is_set():
            try:
                item = self._queue.get(timeout=0.1)
                if item is None:
                    break
                # Flush sentinel: signal waiter and continue draining
                if isinstance(item, _FlushSentinel):
                    item.done_event.set()
                    continue
                # Check pause state
                with self._state_lock:
                    if self._pause_count > 0:
                        self._pause_buffer.append(item)
                        continue
                self._process_item(item)
            except queue.Empty:
                continue
            except Exception as e:
                import sys

                print(f"[console-error] {e}", file=sys.stderr)

    def _process_item(self, item: _PrintItem) -> None:
        """Process a single print item."""
        method = getattr(self._rich_console, item.method, None)
        if method:
            try:
                method(*item.args, **item.kwargs)
            except Exception:
                pass

    def print(self, *args, **kwargs) -> None:
        """Thread-safe print, queued to output thread."""
        self._enqueue("print", args, kwargs)

    def log(self, *args, **kwargs) -> None:
        """Thread-safe log."""
        self._enqueue("log", args, kwargs)

    def rule(self, *args, **kwargs) -> None:
        """Thread-safe rule."""
        self._enqueue("rule", args, kwargs)

    def _enqueue(self, method: str, args: tuple, kwargs: dict) -> None:
        """Enqueue a print request."""
        self._ensure_started()
        item = _PrintItem(method=method, args=args, kwargs=kwargs)
        try:
            self._queue.put(item, timeout=1.0)  # Wait up to 1 second
        except queue.Full:
            warnings.warn("Console queue full, dropping message")

    def pause(self) -> None:
        """Pause output to buffer prints."""
        with self._state_lock:
            self._pause_count += 1

    def resume(self) -> None:
        """Resume output, flushing buffered prints."""
        with self._state_lock:
            if self._pause_count == 0:
                raise RuntimeError("resume() called without matching pause()")
            self._pause_count -= 1
            if self._pause_count == 0:
                # Flush buffer inside lock
                for item in self._pause_buffer:
                    self._queue.put_nowait(item)
                self._pause_buffer = []

    def flush(self, timeout: float = 5.0) -> None:
        """
        Block until all items currently in the queue have been processed.

        Use this before any synchronous terminal operation (e.g. prompt_toolkit's
        prompt()) to guarantee that queued output has been rendered on screen.
        """
        if not self._started:
            return
        event = threading.Event()
        self._queue.put(_FlushSentinel(event))
        if not event.wait(timeout=timeout):
            warnings.warn(f"user_console.flush() timed out after {timeout}s")

    @contextmanager
    def paused(self):
        """
        Context manager for pause/resume.

        Usage:
            with user_console.paused():
                ask_user_question(...)
        """
        self.pause()
        try:
            yield self
        finally:
            self.resume()

    def direct_print(self, *args, **kwargs) -> None:
        """Print directly to Rich console, bypassing the queue."""
        self._rich_console.print(*args, **kwargs)

    @property
    def quiet(self) -> bool:
        return self._rich_console.quiet

    @quiet.setter
    def quiet(self, value: bool) -> None:
        self._rich_console.quiet = value

    @property
    def is_paused(self) -> bool:
        with self._state_lock:
            return self._pause_count > 0


# Singleton instance
user_console = ThreadSafeConsole()


def set_user_output_quiet(quiet: bool) -> None:
    """Set whether user_console suppresses output."""
    user_console.quiet = quiet
