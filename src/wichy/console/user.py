"""Thread-safe user console with pluggable output backends."""

from __future__ import annotations

import queue
import threading
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional


from rich.console import Console as RichConsole
from rich.markdown import Markdown
from rich.table import Table


def _table_to_markdown(table: Table) -> str:
    """Convert a rich.table.Table into a Markdown table string.

    Handles title, headers, row data, and column alignment.
    """
    lines: list[str] = []

    # Optional title rendered as a Markdown heading
    if table.title:
        lines.append(f"**{table.title}**")
        lines.append("")

    # Build header row
    headers = [col.header for col in table.columns]
    lines.append("| " + " | ".join(headers) + " |")

    # Build separator row with alignment indicators
    sep_parts: list[str] = []
    for col in table.columns:
        justify = getattr(col, "justify", "left") or "left"
        if justify == "right":
            sep_parts.append("---:")
        elif justify == "center":
            sep_parts.append(":---:")
        else:
            sep_parts.append("---")
    lines.append("| " + " | ".join(sep_parts) + " |")

    # Build data rows
    row_count = len(table.columns[0]._cells) if table.columns else 0
    for row_idx in range(row_count):
        cells: list[str] = []
        for col in table.columns:
            val = str(col._cells[row_idx]) if row_idx < len(col._cells) else ""
            # Escape pipe characters inside cell content
            val = val.replace("|", "\\|")
            cells.append(val)
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _plain_text(*args, **__) -> str:
    """Convert print args into plain text for server mode.

    - Rich Table objects are converted to clean Markdown tables.
    - Markdown objects yield their raw source markup.
    - Everything else becomes their str() representation.
    """
    parts: list[str] = []
    for arg in args:
        if isinstance(arg, Markdown):
            parts.append(arg.markup)
        elif isinstance(arg, Table):
            parts.append(_table_to_markdown(arg))
        else:
            parts.append(str(arg))
    return " ".join(parts)


# ── REPL backend (existing, extracted) ───────────────


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

    def __init__(self, rich_console: Optional[RichConsole] = None):
        self._rich_console = rich_console or RichConsole(quiet=False)
        self._queue: queue.Queue[Optional[_PrintItem]] = queue.Queue(maxsize=1000)
        self._output_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
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
        self._queue = queue.Queue(maxsize=1000)

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

    def _enqueue(self, method: str, args: tuple, kwargs: dict) -> None:
        self._ensure_started()
        item = _PrintItem(method=method, args=args, kwargs=kwargs)
        try:
            self._queue.put(item, timeout=1.0)
        except queue.Full:
            warnings.warn("Console queue full, dropping message")

    # ── public API ──────────────────────────────────

    def print(self, *args, **kwargs) -> None:
        self._enqueue("print", args, kwargs)

    def log(self, *args, **kwargs) -> None:
        self._enqueue("log", args, kwargs)

    def rule(self, *args, **kwargs) -> None:
        self._enqueue("rule", args, kwargs)

    def pause(self) -> None:
        with self._state_lock:
            self._pause_count += 1

    def resume(self) -> None:
        with self._state_lock:
            if self._pause_count == 0:
                raise RuntimeError("resume() without pause()")
            self._pause_count -= 1
            if self._pause_count == 0:
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
            warnings.warn(f"flush() timed out after {timeout}s")

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


# ── Server backend (captured text, no threads) ─────


class ServerConsole:
    """Lightweight capture backend for API server mode."""

    def __init__(self):
        self._messages: list[str] = []
        self._lock = threading.Lock()
        self._quiet = False

    # ── capture API ─────────────────────────────────

    def get_messages(self) -> list[str]:
        with self._lock:
            msgs = self._messages.copy()
            self._messages.clear()
            return msgs

    # ── console interface (same as ThreadSafeConsole) ─

    def print(self, *args, **kwargs) -> None:
        if self._quiet:
            return
        text = _plain_text(*args, **kwargs)
        with self._lock:
            self._messages.append(text)

    def log(self, *args, **kwargs) -> None:
        self.print(*args, **kwargs)

    def rule(self, *args, **kwargs) -> None:
        if self._quiet:
            return
        title = args[0] if args else kwargs.get("title", "")
        text = f"[rule]{title}[/rule]" if title else "[rule][/rule]"
        self.print(text)

    def flush(self, timeout: float = 5.0) -> None:
        pass  # No background thread: already synchronous

    def pause(self) -> None:
        pass  # No visual interference in server mode

    def resume(self) -> None:
        pass

    @contextmanager
    def paused(self):
        yield self

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            self._messages.clear()

    def direct_print(self, *args, **kwargs) -> None:
        self.print(*args, **kwargs)

    @property
    def quiet(self) -> bool:
        return self._quiet

    @quiet.setter
    def quiet(self, value: bool) -> None:
        self._quiet = value

    @property
    def is_paused(self) -> bool:
        return False


# ── Transparent proxy module singleton ───────────────


class _ConsoleProxy:
    """Delegates to swappable backend. Never rebind `user_console` itself."""

    _impl: ThreadSafeConsole | ServerConsole

    def __init__(self):
        self._impl = ThreadSafeConsole()

    def set_impl(self, impl: ThreadSafeConsole | ServerConsole) -> None:
        # Shut down previous cleanly if possible
        if hasattr(self._impl, "shutdown"):
            self._impl.shutdown()
        self._impl = impl

    def print(self, *args, **kwargs) -> None:
        self._impl.print(*args, **kwargs)

    def log(self, *args, **kwargs) -> None:
        self._impl.log(*args, **kwargs)

    def rule(self, *args, **kwargs) -> None:
        self._impl.rule(*args, **kwargs)

    def pause(self) -> None:
        self._impl.pause()

    def resume(self) -> None:
        self._impl.resume()

    @contextmanager
    def paused(self):
        self._impl.pause()
        try:
            yield self._impl
        finally:
            self._impl.resume()

    def flush(self, timeout: float = 5.0) -> None:
        self._impl.flush(timeout=timeout)

    def direct_print(self, *args, **kwargs) -> None:
        self._impl.direct_print(*args, **kwargs)

    def shutdown(self, timeout: float = 2.0) -> None:
        self._impl.shutdown(timeout=timeout)

    @property
    def quiet(self) -> bool:
        return self._impl.quiet

    @quiet.setter
    def quiet(self, value: bool) -> None:
        self._impl.quiet = value

    @property
    def is_paused(self) -> bool:
        return self._impl.is_paused

    # ServerConsole extension — safe to call from any mode
    def get_messages(self) -> list[str]:
        if isinstance(self._impl, ServerConsole):
            return self._impl.get_messages()
        return []


# ── Module-level singletons ────────────────────────

user_console = _ConsoleProxy()


def set_user_output_quiet(quiet: bool) -> None:
    """Set whether user_console suppresses output."""
    user_console.quiet = quiet
