import asyncio
import threading
import time
from typing import TypeVar, Coroutine
from concurrent.futures import Future

T = TypeVar("T")


class MCPAsyncBridge:
    """
    Provides sync-to-async bridge for MCP operations.

    Runs an asyncio event loop in a daemon thread, allowing
    synchronous code to execute async MCP operations.
    """

    _instance = None
    _lock = threading.Lock()  # Class-level lock for thread-safe singleton creation

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._initialized = True

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Ensure event loop is running. Creates a new loop in a daemon thread if needed."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()

            def run_loop():
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()

            self._thread = threading.Thread(
                target=run_loop,
                daemon=True,
                name="mcp-async-bridge",
            )
            self._thread.start()

            # Poll until loop is running — avoids race condition where
            # an event-based signal could fire before run_forever() starts.
            while not self._loop.is_running():
                time.sleep(0.001)

        return self._loop

    def run_sync(self, coro: Coroutine[None, None, T], timeout: float = 60.0) -> T:
        """
        Run an async coroutine from sync context.

        Each call is submitted independently to the event loop.
        Independent MCP servers can execute concurrently.

        Args:
            coro: The coroutine to run.
            timeout: Maximum time to wait in seconds.

        Returns:
            The result of the coroutine.

        Raises:
            TimeoutError: If timeout is exceeded.
            Exception: Any exception raised by the coroutine.
        """
        loop = self._ensure_loop()
        future: Future[T] = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except (TimeoutError, Exception) as exc:
            if isinstance(exc, (TimeoutError,)):
                # Cancel the future to prevent leaked coroutines on the event loop
                future.cancel()
                try:
                    future.result(timeout=1.0)
                except (asyncio.CancelledError, Exception):
                    pass
            raise

    def shutdown(self):
        """Stop the event loop and clean up the daemon thread."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=2)
            self._loop = None
            self._thread = None

    @classmethod
    def _reset_singleton(cls):
        """Reset the singleton instance. For testing only."""
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.shutdown()
                except Exception:
                    pass
                cls._instance = None


# Global singleton
mcp_async_bridge = MCPAsyncBridge()
