"""Tests for MCP async bridge."""

import asyncio
import threading

import pytest

from wichy.mcp_host.async_bridge import MCPAsyncBridge


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton between tests."""
    MCPAsyncBridge._reset_singleton()
    yield
    MCPAsyncBridge._reset_singleton()


class TestMCPAsyncBridge:
    """Test the sync-to-async bridge."""

    def test_singleton_pattern(self):
        """Multiple calls to MCPAsyncBridge() return the same instance."""
        bridge1 = MCPAsyncBridge()
        bridge2 = MCPAsyncBridge()
        assert bridge1 is bridge2

    def test_reset_singleton(self):
        """_reset_singleton creates a new instance on next access."""
        bridge1 = MCPAsyncBridge()
        MCPAsyncBridge._reset_singleton()
        bridge2 = MCPAsyncBridge()
        assert bridge1 is not bridge2

    def test_run_sync_async_function(self):
        """Test running a simple async function via run_sync."""
        bridge = MCPAsyncBridge()

        async def add(a, b):
            return a + b

        result = bridge.run_sync(add(3, 4))
        assert result == 7

    def test_run_sync_preserves_exceptions(self):
        """Test that exceptions from async functions are propagated."""
        bridge = MCPAsyncBridge()

        async def failing():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            bridge.run_sync(failing())

    def test_run_sync_timeout(self):
        """Test that run_sync raises TimeoutError on timeout."""
        bridge = MCPAsyncBridge()

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(TimeoutError):
            bridge.run_sync(slow(), timeout=0.1)

    def test_run_sync_timeout_cancels_future(self):
        """Test that timed-out coroutines get cancelled (not leaked)."""
        bridge = MCPAsyncBridge()
        cancelled = []

        async def slow_with_cleanup():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        with pytest.raises(TimeoutError):
            bridge.run_sync(slow_with_cleanup(), timeout=0.1)

        # Give the event loop a moment to process the cancellation
        async def wait():
            await asyncio.sleep(0.05)

        bridge.run_sync(wait())
        assert len(cancelled) > 0, "Timed-out coroutine was not cancelled"

    def test_concurrent_calls_from_threads(self):
        """Test that independent threads can call run_sync concurrently."""
        bridge = MCPAsyncBridge()
        results = []
        errors = []

        async def add(a, b):
            await asyncio.sleep(0.05)
            return a + b

        def worker(a, b):
            try:
                result = bridge.run_sync(add(a, b), timeout=5.0)
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i, i * 10)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors, f"Errors in concurrent threads: {errors}"
        assert sorted(results) == [0, 11, 22]  # 0+0, 1+10, 2+20

    def test_shutdown(self):
        """Test that shutdown cleans up the event loop."""
        bridge = MCPAsyncBridge()

        async def simple():
            return 42

        # Verify bridge works before shutdown
        result = bridge.run_sync(simple())
        assert result == 42

        bridge.shutdown()
        assert bridge._loop is None
        assert bridge._thread is None

    def test_run_sync_after_shutdown_resurrects(self):
        """Test that a live (non-reset) bridge creates a new loop after shutdown."""
        bridge = MCPAsyncBridge()

        async def simple():
            return 1

        bridge.run_sync(simple())  # Start the loop
        bridge.shutdown()

        # Calling run_sync on a shut-down (but non-reset) bridge should
        # resurrect via _ensure_loop creating a new loop
        result = bridge.run_sync(simple())
        assert result == 1
