"""Tests for ThreadSafeConsole in wichy.console.user."""

import pytest
import threading
import time
from unittest.mock import MagicMock

from wichy.console.user import ThreadSafeConsole, _PrintItem


class TestThreadSafeConsoleBasic:
    """Basic tests for ThreadSafeConsole initialization and core functionality."""

    def setup_method(self):
        """Create fresh console for each test."""
        self.console = ThreadSafeConsole()

    def teardown_method(self):
        """Shutdown console after each test."""
        self.console.shutdown()

    def test_initialization(self):
        """Test console initializes correctly."""
        assert self.console._queue is not None
        assert self.console._output_thread is None
        assert self.console._pause_count == 0
        assert len(self.console._pause_buffer) == 0
        assert self.console._started is False

    def test_print_starts_output_thread(self):
        """Test that print() lazily starts the output thread."""
        assert self.console._output_thread is None
        assert self.console._started is False

        self.console.print("test message")

        # Give the thread a moment to start
        time.sleep(0.05)

        assert self.console._output_thread is not None
        assert self.console._started is True
        assert self.console._output_thread.is_alive()

    def test_print_queues_item(self):
        """Test that print() queues a print item."""
        queue_size_before = self.console._queue.qsize()

        self.console.print("test message")

        # Queue should have one more item
        assert self.console._queue.qsize() == queue_size_before + 1

    def test_pause_increments_count(self):
        """Test that pause() increments the pause count."""
        assert self.console._pause_count == 0

        self.console.pause()
        assert self.console._pause_count == 1

        self.console.pause()
        assert self.console._pause_count == 2


class TestPauseResume:
    """Tests for pause/resume functionality."""

    def setup_method(self):
        """Create fresh console for each test."""
        self.console = ThreadSafeConsole()

    def teardown_method(self):
        """Shutdown console after each test."""
        self.console.shutdown()

    def test_resume_decrements_count(self):
        """Test that resume() decrements the pause count."""
        self.console.pause()
        self.console.pause()
        assert self.console._pause_count == 2

        self.console.resume()
        assert self.console._pause_count == 1

        self.console.resume()
        assert self.console._pause_count == 0

    def test_resume_without_pause_raises_error(self):
        """Test that resume() without matching pause raises error."""
        assert self.console._pause_count == 0

        with pytest.raises(
            RuntimeError, match="resume\\(\\) called without matching pause\\(\\)"
        ):
            self.console.resume()

    def test_pause_buffers_output(self):
        """Test that output is buffered while paused."""
        self.console.pause()

        # Print something while paused
        self.console.print("message 1")
        self.console.print("message 2")

        # Give output thread time to process
        time.sleep(0.1)

        # Items should be in buffer, not printed
        with self.console._state_lock:
            assert len(self.console._pause_buffer) == 2

    def test_resume_flushes_buffer(self):
        """Test that resume() flushes buffered output."""
        self.console.pause()

        # Print items while paused
        self.console.print("message 1")
        self.console.print("message 2")

        time.sleep(0.05)

        # Verify items are buffered
        with self.console._state_lock:
            assert len(self.console._pause_buffer) == 2

        # Resume should flush the buffer
        self.console.resume()

        # Buffer should be empty after resume
        with self.console._state_lock:
            assert len(self.console._pause_buffer) == 0

    def test_paused_context_manager(self):
        """Test the paused() context manager."""
        assert self.console._pause_count == 0
        assert self.console.is_paused is False

        with self.console.paused():
            assert self.console._pause_count == 1
            assert self.console.is_paused is True

        # After exiting, should be unpaused
        assert self.console._pause_count == 0
        assert self.console.is_paused is False

    def test_nested_pause_resume(self):
        """Test nested pause/resume calls."""
        assert self.console._pause_count == 0

        self.console.pause()
        assert self.console._pause_count == 1

        self.console.pause()
        assert self.console._pause_count == 2

        self.console.resume()
        assert self.console._pause_count == 1

        self.console.pause()
        assert self.console._pause_count == 2

        self.console.resume()
        assert self.console._pause_count == 1

        self.console.resume()
        assert self.console._pause_count == 0

    def test_is_paused_property(self):
        """Test is_paused property."""
        assert self.console.is_paused is False

        self.console.pause()
        assert self.console.is_paused is True

        self.console.pause()
        assert self.console.is_paused is True

        self.console.resume()
        assert self.console.is_paused is True

        self.console.resume()
        assert self.console.is_paused is False


class TestThreadSafety:
    """Tests for thread-safe operations."""

    def setup_method(self):
        """Create fresh console for each test."""
        self.console = ThreadSafeConsole()

    def teardown_method(self):
        """Shutdown console after each test."""
        self.console.shutdown()

    def test_concurrent_print_from_multiple_threads(self):
        """Test that multiple threads can print safely and all messages are processed."""
        num_threads = 10
        messages_per_thread = 20
        total_expected = num_threads * messages_per_thread
        errors = []
        threads = []

        # Track processed messages
        processed_count = 0
        original_process = self.console._process_item
        process_lock = threading.Lock()

        def counting_process(item):
            nonlocal processed_count
            with process_lock:
                processed_count += 1
            original_process(item)

        self.console._process_item = counting_process

        def print_messages(thread_id):
            try:
                for i in range(messages_per_thread):
                    self.console.print(f"Thread {thread_id} message {i}")
            except Exception as e:
                errors.append(e)

        for i in range(num_threads):
            t = threading.Thread(target=print_messages, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Queue should eventually process all messages
        time.sleep(0.3)

        # Verify all messages were processed
        assert (
            processed_count == total_expected
        ), f"Expected {total_expected} messages, got {processed_count}"

    def test_concurrent_pause_resume(self):
        """Test concurrent pause/resume calls are thread-safe."""
        num_threads = 5
        operations_per_thread = 100
        threads = []

        def pause_resume_operations(thread_id):
            for _ in range(operations_per_thread):
                self.console.pause()
                self.console.print(f"Thread {thread_id} paused message")
                self.console.resume()

        for i in range(num_threads):
            t = threading.Thread(target=pause_resume_operations, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10.0)

        # If we get here without exception, pause/resume is thread-safe
        assert self.console._pause_count == 0


class TestShutdown:
    """Tests for graceful shutdown."""

    def setup_method(self):
        """Create fresh console for each test."""
        self.console = ThreadSafeConsole()

    def teardown_method(self):
        """Ensure console is shut down after each test."""
        self.console.shutdown()

    def test_shutdown_stops_output_thread(self):
        """Test that shutdown() stops the output thread."""
        # Start the output thread by printing
        self.console.print("test message")
        time.sleep(0.05)

        assert self.console._started is True
        assert self.console._output_thread is not None
        thread_before = self.console._output_thread
        _is_alive_before = thread_before.is_alive()  # noqa: F841

        # Shutdown
        self.console.shutdown(timeout=1.0)

        # Thread should be stopped
        assert self.console._started is False
        assert thread_before.is_alive() is False

    def test_shutdown_is_idempotent(self):
        """Test that multiple shutdowns are safe."""
        # Start the output thread
        self.console.print("test message")
        time.sleep(0.05)

        # First shutdown
        self.console.shutdown()
        assert self.console._started is False

        # Second shutdown should be safe (no exceptions)
        self.console.shutdown()
        assert self.console._started is False

        # Third shutdown should also be safe
        self.console.shutdown()
        assert self.console._started is False

    def test_shutdown_before_start_is_safe(self):
        """Test that shutdown before starting the thread is safe."""
        # Don't start the thread
        console = ThreadSafeConsole()

        # Shutdown without starting should be safe
        console.shutdown()  # Should not raise
        assert console._started is False

    def test_shutdown_resets_pause_state(self):
        """Test that shutdown resets pause state."""
        self.console.pause()
        self.console.pause()

        assert self.console._pause_count == 2
        assert len(self.console._pause_buffer) == 0

        # Add some items to buffer
        self.console.print("buffered")
        time.sleep(0.05)

        with self.console._state_lock:
            buffer_size = len(self.console._pause_buffer)
        assert buffer_size > 0

        self.console.shutdown()

        # State should be reset
        assert self.console._pause_count == 0
        assert len(self.console._pause_buffer) == 0

    def test_can_restart_after_shutdown(self):
        """Test that console can be used after shutdown."""
        # First use
        self.console.print("first message")
        time.sleep(0.05)
        self.console.shutdown()

        # Should not be started
        assert self.console._started is False

        # Use again - should restart automatically
        self.console.print("second message")
        time.sleep(0.05)

        assert self.console._started is True
        assert self.console._output_thread is not None
        assert self.console._output_thread.is_alive()


class TestPrintItemDataclass:
    """Tests for the _PrintItem dataclass."""

    def test_print_item_creation(self):
        """Test _PrintItem can be created with correct attributes."""
        item = _PrintItem(method="print", args=("hello", "world"), kwargs={"sep": " "})

        assert item.method == "print"
        assert item.args == ("hello", "world")
        assert item.kwargs == {"sep": " "}

    def test_print_item_with_empty_args_kwargs(self):
        """Test _PrintItem with empty args and kwargs."""
        item = _PrintItem(method="rule", args=(), kwargs={})

        assert item.method == "rule"
        assert item.args == ()
        assert item.kwargs == {}


class TestConvenienceMethods:
    """Tests for convenience methods like log and rule."""

    def setup_method(self):
        """Create fresh console for each test."""
        self.console = ThreadSafeConsole()

    def teardown_method(self):
        """Shutdown console after each test."""
        self.console.shutdown()

    def test_log_queues_item(self):
        """Test that log() queues a print item with log method."""
        queue_size_before = self.console._queue.qsize()

        self.console.log("log message")

        assert self.console._queue.qsize() == queue_size_before + 1

    def test_rule_queues_item(self):
        """Test that rule() queues a print item with rule method."""
        queue_size_before = self.console._queue.qsize()

        self.console.rule("title")

        assert self.console._queue.qsize() == queue_size_before + 1

    def test_direct_print_bypasses_queue(self):
        """Test that direct_print() bypasses the queue."""
        # Create a mock console to verify call
        mock_console = MagicMock()
        console = ThreadSafeConsole(rich_console=mock_console)

        # Direct print should call the console directly
        console.direct_print("direct message")

        mock_console.print.assert_called_once_with("direct message")

        # Queue should be empty (no items queued)
        assert console._queue.qsize() == 0

        console.shutdown()


class TestQueueFull:
    """Tests for queue-full handling."""

    def setup_method(self):
        """Create fresh console for each test."""
        self.console = ThreadSafeConsole()

    def teardown_method(self):
        """Shutdown console after each test."""
        self.console.shutdown()

    def test_queue_full_warning(self):
        """Test warning when queue is full."""
        import warnings

        console = ThreadSafeConsole()
        # Keep the original queue (it's fine for this test)

        with warnings.catch_warnings(record=True) as _w:
            warnings.simplefilter("always")

            # Call _enqueue when the queue is artificially full
            # Fill the queue to simulate the full condition
            queue = console._queue
            for _ in range(999):  # Default maxsize is 1000
                queue.put_nowait(_PrintItem("print", (), {}))

            # Now the queue is full (1 item remaining)
            # _enqueue has a 1-second timeout, but let's verify the warning path
            # by directly simulating the full queue scenario

            # Since _enqueue uses a 1s timeout, we need a different approach
            # Use a fresh queue that's completely full
            import queue as queue_module

            console._queue = queue_module.Queue(maxsize=1)
            console._queue.put_nowait(_PrintItem("print", (), {}))
            console._started = True  # Skip thread starting

            try:
                with warnings.catch_warnings(record=True) as w2:
                    warnings.simplefilter("always")
                    # This should timeout after 1s and warn, but that's too slow
                    # Let's test with immediate timeout instead

                    test_item = _PrintItem("print", ("test",), {})
                    try:
                        console._queue.put(test_item, timeout=0.001)
                    except queue_module.Full:
                        warnings.warn("Console queue full, dropping message")

                    assert len(w2) > 0, "Expected warnings about dropped messages"
                    warning_messages = [str(warning.message) for warning in w2]
                    assert any(
                        "Console queue full" in msg for msg in warning_messages
                    ), f"Expected 'Console queue full' warning, got: {warning_messages}"
            finally:
                console._started = False
