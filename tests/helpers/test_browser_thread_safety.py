"""
Thread-safety tests for BrowserManager's execute_serialized() method.

Tests cover:
- Sequential calls through execute_serialized
- Concurrent call serialization (no overlap in execution)
- Timeout handling
- Browser thread lock contention
- Singleton creation thread safety

Note: execute_serialized() uses asyncio.run_coroutine_threadsafe() which requires
a running event loop. These tests start an event loop in a background thread to
properly test the serialization behavior.
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from wichy.helpers.browser import BrowserManager


class EventLoopFixture:
    """Helper to manage a running event loop in a background thread."""

    def __init__(self):
        self.loop = None
        self.thread = None
        self._stop_event = None
        self._ready_event = None

    def start(self):
        """Start the event loop in a background thread."""
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()

        def run_loop():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self._ready_event.set()
            # Run forever until stop is requested
            self.loop.run_forever()

        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()
        self._ready_event.wait(timeout=5.0)  # Wait for loop to be ready

    def stop(self):
        """Stop the event loop and clean up."""
        if self.loop:
            # Schedule stop on the loop
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread:
            self.thread.join(timeout=2.0)


class TestBrowserManagerThreadSafety:
    """Tests for thread-safety of BrowserManager."""

    def setup_method(self):
        """Reset singleton before each test."""
        if (
            hasattr(BrowserManager, "_instance")
            and BrowserManager._instance is not None
        ):
            BrowserManager._instance = None
        BrowserManager._instance = None

    def teardown_method(self):
        """Cleanup after each test."""
        if (
            hasattr(BrowserManager, "_instance")
            and BrowserManager._instance is not None
        ):
            BrowserManager._instance = None

    def test_execute_serialized_sequential_calls(self):
        """Test that execute_serialized handles sequential calls correctly."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            # Track execution order
            execution_order = []

            async def operation(url_suffix):
                execution_order.append(f"start-{url_suffix}")
                await asyncio.sleep(0.01)  # Simulate some work
                execution_order.append(f"end-{url_suffix}")
                return {"status": "success", "url": f"https://example.com/{url_suffix}"}

            # Execute sequential calls
            result1 = manager.execute_serialized(
                lambda: operation("page1"), timeout=5.0
            )
            result2 = manager.execute_serialized(
                lambda: operation("page2"), timeout=5.0
            )

            # Verify results
            assert result1["status"] == "success"
            assert result1["url"] == "https://example.com/page1"
            assert result2["status"] == "success"
            assert result2["url"] == "https://example.com/page2"

            # Verify execution order is sequential (no interleaving)
            assert execution_order == [
                "start-page1",
                "end-page1",
                "start-page2",
                "end-page2",
            ]
        finally:
            loop_fixture.stop()

    def test_execute_serialized_concurrent_calls_are_serialized(self):
        """Test that concurrent calls through execute_serialized are properly serialized."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            # Use events for deterministic synchronization tracking
            execution_order = []
            execution_lock = threading.Lock()

            # Events to track when first operation starts and ends
            first_started = threading.Event()
            first_finished = threading.Event()
            second_started = threading.Event()
            second_finished = threading.Event()
            third_started = threading.Event()
            third_finished = threading.Event()

            events = {
                0: (first_started, first_finished),
                1: (second_started, second_finished),
                2: (third_started, third_finished),
            }

            async def tracked_operation(op_id):
                start_event, end_event = events[op_id]
                with execution_lock:
                    execution_order.append(f"start_{op_id}")
                    start_event.set()
                await asyncio.sleep(0.01)  # Very short, just to yield control
                with execution_lock:
                    execution_order.append(f"end_{op_id}")
                    end_event.set()
                return {"op_id": op_id}

            results = [None] * 3
            exceptions = [None] * 3

            def thread_worker(call_id):
                try:
                    results[call_id] = manager.execute_serialized(
                        lambda: tracked_operation(call_id), timeout=10.0
                    )
                except Exception as e:
                    exceptions[call_id] = e

            # Start three threads concurrently
            threads = [
                threading.Thread(target=thread_worker, args=(0,)),
                threading.Thread(target=thread_worker, args=(1,)),
                threading.Thread(target=thread_worker, args=(2,)),
            ]

            for t in threads:
                t.start()

            # Wait for all operations to complete
            for t in threads:
                t.join()

            # Verify no exceptions occurred
            for i, exc in enumerate(exceptions):
                assert exc is None, f"Thread {i} raised exception: {exc}"

            # Verify all results are present
            for i, result in enumerate(results):
                assert result is not None, f"Thread {i} result is None"
                assert result["op_id"] == i

            # Verify serialization: operations should not overlap
            # Each operation must complete before the next one starts
            # We verify this by checking that "end_N" appears before "start_M" for any N != M
            # where start_M is the next start after end_N in the execution order

            with execution_lock:
                order = list(execution_order)

            # Verify no overlapping: for any "start_X", if there's a previous "start_Y",
            # then "end_Y" must appear before "start_X"
            started_operations = []
            for entry in order:
                if entry.startswith("start_"):
                    op_id = int(entry.split("_")[1])
                    started_operations.append(op_id)
                    # All previously started operations must have ended
                    for prev_op in started_operations[:-1]:  # Exclude current
                        assert (
                            f"end_{prev_op}" in order[: order.index(entry)]
                        ), f"Operation {op_id} started before operation {prev_op} ended - serialization failed!"
                elif entry.startswith("end_"):
                    op_id = int(entry.split("_")[1])
                    if op_id in started_operations:
                        started_operations.remove(op_id)

            # Verify each start has a corresponding end
            starts = [e for e in order if e.startswith("start_")]
            ends = [e for e in order if e.startswith("end_")]
            assert len(starts) == 3, f"Expected 3 starts, got {len(starts)}"
            assert len(ends) == 3, f"Expected 3 ends, got {len(ends)}"
        finally:
            loop_fixture.stop()

    def test_execute_serialized_timeout(self):
        """Test that execute_serialized raises RuntimeError on timeout.

        Note: Both asyncio.TimeoutError and concurrent.futures.TimeoutError
        are wrapped in RuntimeError by execute_serialized.
        """
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            async def slow_operation():
                await asyncio.sleep(10.0)  # This will exceed timeout
                return {"status": "should not reach here"}

            # Should raise RuntimeError wrapping the timeout (actual behavior)
            # Note: concurrent.futures.TimeoutError is raised by future.result(),
            # which gets wrapped in RuntimeError by execute_serialized
            with pytest.raises(RuntimeError) as exc_info:
                manager.execute_serialized(lambda: slow_operation(), timeout=0.5)

            # Verify error message indicates timeout
            error_msg = str(exc_info.value).lower()
            assert (
                "timeout" in error_msg
            ), f"Expected 'timeout' in error message: {error_msg}"
        finally:
            loop_fixture.stop()

    def test_browser_thread_lock_contention(self):
        """Test that browser thread lock prevents concurrent browser access."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            # Track lock acquisition and release
            lock_events = []
            event_lock = threading.Lock()

            async def browser_operation(op_id):
                thread_id = threading.get_ident()
                with event_lock:
                    lock_events.append(("acquire", op_id, thread_id, time.time()))
                await asyncio.sleep(0.03)  # Simulate browser work
                with event_lock:
                    lock_events.append(("release", op_id, thread_id, time.time()))
                return {"op_id": op_id}

            results = [None] * 2

            def thread_worker(op_id):
                results[op_id] = manager.execute_serialized(
                    lambda: browser_operation(op_id), timeout=5.0
                )

            # Start two threads
            threads = [
                threading.Thread(target=thread_worker, args=(0,)),
                threading.Thread(target=thread_worker, args=(1,)),
            ]

            for t in threads:
                t.start()

            for t in threads:
                t.join()

            # Verify both operations completed
            assert results[0] is not None
            assert results[1] is not None
            assert results[0]["op_id"] == 0
            assert results[1]["op_id"] == 1

            # Verify operations didn't overlap (lock worked)
            # Extract times
            op_times = {}
            with event_lock:
                for event, op_id, thread_id, timestamp in lock_events:
                    if op_id not in op_times:
                        op_times[op_id] = {"acquire": None, "release": None}
                    op_times[op_id][event] = timestamp

            # Verify serialization: one must release before other acquires
            if len(op_times) == 2:
                op0 = op_times[0]
                op1 = op_times[1]

                # Either op0 released before op1 acquired, or vice versa
                serialized = (
                    op0["release"] <= op1["acquire"] or op1["release"] <= op0["acquire"]
                )
                assert serialized, "Operations overlapped - lock didn't work!"
        finally:
            loop_fixture.stop()

    def test_singleton_creation_race_condition(self):
        """Test that singleton creation is thread-safe under race conditions."""
        # Reset singleton completely
        BrowserManager._instance = None

        instances = []
        instances_lock = threading.Lock()
        barrier = threading.Barrier(5)  # 5 threads will race

        def create_instance():
            # Synchronize all threads to start at the same time
            barrier.wait()

            instance = BrowserManager()

            with instances_lock:
                instances.append(instance)

        # Create 5 threads that will all try to create the singleton at once
        threads = [threading.Thread(target=create_instance) for _ in range(5)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # All instances should be the same (singleton pattern)
        assert len(instances) == 5, f"Expected 5 instances, got {len(instances)}"

        first_instance = instances[0]
        for instance in instances:
            assert (
                instance is first_instance
            ), "Singleton pattern broken - different instances!"

        # Verify singleton lock was used properly
        assert hasattr(BrowserManager, "_singleton_lock")

        # Cleanup
        BrowserManager._instance = None


class TestExecuteSerializedEdgeCases:
    """Tests for edge cases in execute_serialized method."""

    def setup_method(self):
        """Reset singleton before each test."""
        if (
            hasattr(BrowserManager, "_instance")
            and BrowserManager._instance is not None
        ):
            BrowserManager._instance = None
        BrowserManager._instance = None

    def teardown_method(self):
        """Cleanup after each test."""
        if (
            hasattr(BrowserManager, "_instance")
            and BrowserManager._instance is not None
        ):
            BrowserManager._instance = None

    def test_execute_serialized_propagates_exception(self):
        """Test that exceptions from operations are wrapped in RuntimeError."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            async def failing_operation():
                raise ValueError("Intentional test error")

            # Should raise RuntimeError wrapping the ValueError
            with pytest.raises(RuntimeError) as exc_info:
                manager.execute_serialized(lambda: failing_operation(), timeout=5.0)

            assert "Intentional test error" in str(exc_info.value)
            assert "ValueError" in str(exc_info.value)
        finally:
            loop_fixture.stop()

    def test_execute_serialized_returns_coroutine_result(self):
        """Test that execute_serialized properly returns coroutine results."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            async def return_complex_data():
                return {
                    "key": "value",
                    "nested": {"a": 1, "b": 2},
                    "list": [1, 2, 3],
                }

            result = manager.execute_serialized(
                lambda: return_complex_data(), timeout=5.0
            )

            assert result["key"] == "value"
            assert result["nested"]["a"] == 1
            assert result["list"] == [1, 2, 3]
        finally:
            loop_fixture.stop()

    def test_execute_serialized_with_different_event_loops(self):
        """Test execute_serialized creates/handles event loop correctly."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            async def simple_operation():
                return {"status": "ok"}

            # First call should use the loop
            result1 = manager.execute_serialized(
                lambda: simple_operation(), timeout=5.0
            )
            assert result1["status"] == "ok"

            # Second call should reuse the loop
            result2 = manager.execute_serialized(
                lambda: simple_operation(), timeout=5.0
            )
            assert result2["status"] == "ok"

            # Verify loop is still our running loop
            assert manager._loop is loop_fixture.loop
        finally:
            loop_fixture.stop()


class TestThreadLockBehavior:
    """Tests for specific threading lock behaviors."""

    def setup_method(self):
        """Reset singleton before each test."""
        if (
            hasattr(BrowserManager, "_instance")
            and BrowserManager._instance is not None
        ):
            BrowserManager._instance = None
        BrowserManager._instance = None

    def teardown_method(self):
        """Cleanup after each test."""
        if (
            hasattr(BrowserManager, "_instance")
            and BrowserManager._instance is not None
        ):
            BrowserManager._instance = None

    def test_browser_thread_lock_is_class_level(self):
        """Test that _browser_thread_lock is a class-level lock."""
        # Verify the lock exists at class level
        assert hasattr(BrowserManager, "_browser_thread_lock")
        # Check for lock methods (more reliable than isinstance check)
        assert hasattr(BrowserManager._browser_thread_lock, "acquire")
        assert hasattr(BrowserManager._browser_thread_lock, "release")
        assert hasattr(BrowserManager._browser_thread_lock, "locked")

    def test_singleton_lock_is_class_level(self):
        """Test that _singleton_lock is a class-level lock."""
        # Verify the lock exists at class level
        assert hasattr(BrowserManager, "_singleton_lock")
        # Check for lock methods (more reliable than isinstance check)
        assert hasattr(BrowserManager._singleton_lock, "acquire")
        assert hasattr(BrowserManager._singleton_lock, "release")
        assert hasattr(BrowserManager._singleton_lock, "locked")

    def test_multiple_managers_same_lock(self):
        """Test that BrowserManager singleton pattern works correctly.

        Since BrowserManager is a singleton, multiple instantiations return
        the same instance. The class-level locks are shared by definition
        across all instances (since there's only one instance).
        """
        # Create two manager instances (should be same singleton)
        manager1 = BrowserManager()
        manager2 = BrowserManager()

        # Both should be the same instance (singleton pattern)
        assert manager1 is manager2

    def test_execute_serialized_multiple_calls_same_thread(self):
        """Test that calling execute_serialized multiple times from same thread works correctly."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            call_count = [0]

            async def counting_operation():
                call_count[0] += 1
                return {"count": call_count[0]}

            # Call multiple times from same thread
            result1 = manager.execute_serialized(
                lambda: counting_operation(), timeout=5.0
            )
            result2 = manager.execute_serialized(
                lambda: counting_operation(), timeout=5.0
            )
            result3 = manager.execute_serialized(
                lambda: counting_operation(), timeout=5.0
            )

            # Each call should have executed
            assert result1["count"] == 1
            assert result2["count"] == 2
            assert result3["count"] == 3
            assert call_count[0] == 3
        finally:
            loop_fixture.stop()

    def test_lock_blocks_other_threads_during_execution(self):
        """Test that the lock blocks other threads while one thread is executing."""
        manager = BrowserManager()

        # Setup mocks
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed = MagicMock(return_value=False)

        manager._browser = mock_browser
        manager._context = mock_context
        manager._page = mock_page

        # Start a running event loop
        loop_fixture = EventLoopFixture()
        loop_fixture.start()

        try:
            # Set the manager's loop to our running loop
            manager._loop = loop_fixture.loop

            # Track when each thread starts waiting and when it acquires the lock
            events = []
            events_lock = threading.Lock()

            async def slow_operation(thread_id):
                with events_lock:
                    events.append(("acquired", thread_id, time.time()))
                await asyncio.sleep(0.1)  # Hold the lock for a while
                with events_lock:
                    events.append(("released", thread_id, time.time()))
                return {"thread_id": thread_id}

            results = [None] * 3

            def thread_worker(thread_id):
                results[thread_id] = manager.execute_serialized(
                    lambda: slow_operation(thread_id), timeout=5.0
                )

            # Create threads that will contend for the lock
            threads = [
                threading.Thread(target=thread_worker, args=(i,)) for i in range(3)
            ]

            # Start threads with slight delays to ensure they contend
            for t in threads:
                t.start()
                time.sleep(0.01)  # Small delay to let threads contend

            for t in threads:
                t.join()

            # All should complete successfully
            assert all(r is not None for r in results)

            # Verify that operations were serialized (no overlapping)
            # Each thread should have acquired and released in order
            acquired_times = {}
            released_times = {}

            with events_lock:
                for event, thread_id, timestamp in events:
                    if event == "acquired":
                        acquired_times[thread_id] = timestamp
                    else:
                        released_times[thread_id] = timestamp

            # For serialization: each release must be before the next acquire
            # Sort threads by acquire time
            threads_by_acquire = sorted(
                acquired_times.keys(), key=lambda t: acquired_times[t]
            )

            # Verify each thread released before the next acquired
            for i in range(len(threads_by_acquire) - 1):
                current_thread = threads_by_acquire[i]
                next_thread = threads_by_acquire[i + 1]
                assert (
                    released_times[current_thread] <= acquired_times[next_thread]
                ), f"Thread {current_thread} didn't release before Thread {next_thread} acquired"
        finally:
            loop_fixture.stop()
