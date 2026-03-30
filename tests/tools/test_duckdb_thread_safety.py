"""Thread-safety tests for DuckDB connection pool and manager."""

import os
import tempfile
import threading

import pytest

from wichy.tools.duckdb_manager import (
    ConnectionPool,
    DuckDBManager,
    PoolExhaustedError,
)


class TestConnectionPoolThreadSafety:
    """Tests for ConnectionPool thread safety."""

    @pytest.fixture(autouse=True)
    def reset_duckdb(self):
        """Reset DuckDBManager before/after each test."""
        DuckDBManager.reset()
        yield
        DuckDBManager.reset()

    def test_pool_exhaustion_raises_error(self):
        """Test that pool exhaustion raises PoolExhaustedError."""
        pool = ConnectionPool(db_path=None, pool_size=2)

        with pool.get_connection() as _conn1:
            with pool.get_connection() as _conn2:
                # Third acquisition should fail with short timeout
                with pytest.raises(PoolExhaustedError) as exc_info:
                    pool._acquire(timeout=0.1)

                assert "No connections available" in str(exc_info.value)
                assert "Pool size: 2" in str(exc_info.value)

        pool.close()

    def test_pool_returns_connections_after_use(self):
        """Test that connections are returned to pool after use."""
        pool = ConnectionPool(db_path=None, pool_size=2)

        # Use connections and release them
        with pool.get_connection() as conn1:
            conn1.execute("SELECT 1").fetchall()

        with pool.get_connection() as conn2:
            conn2.execute("SELECT 2").fetchall()

        # Both should be released - we can acquire 2 again
        with pool.get_connection() as conn1:
            with pool.get_connection() as conn2:
                # Third should fail since pool is exhausted
                with pytest.raises(PoolExhaustedError):
                    pool._acquire(timeout=0.1)

        # Now both are released, we should be able to get 2 again
        with pool.get_connection() as conn1:
            with pool.get_connection() as conn2:
                pass  # Successfully acquired both

        pool.close()

    def test_concurrent_queries_from_multiple_threads(self):
        """Test that concurrent queries from different threads work correctly."""
        num_threads = 10
        num_queries_per_thread = 5
        results = []
        errors = []
        results_lock = threading.Lock()

        def execute_queries(thread_id: int):
            try:
                for i in range(num_queries_per_thread):
                    with DuckDBManager.get_connection() as conn:
                        result = conn.execute(
                            f"SELECT {thread_id} + {i} as value"
                        ).fetchone()
                        if result:
                            with results_lock:
                                results.append((thread_id, i, result[0]))
            except Exception as e:
                with results_lock:
                    errors.append((thread_id, str(e)))

        # Run concurrent queries
        threads = []
        for thread_id in range(num_threads):
            t = threading.Thread(target=execute_queries, args=(thread_id,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == num_threads * num_queries_per_thread

        # Verify each thread's queries were executed correctly
        for thread_id, i, value in results:
            expected = thread_id + i
            assert value == expected, f"Expected {expected}, got {value}"

    def test_singleton_thread_safety(self):
        """Test that singleton creation is thread-safe under race conditions."""
        num_threads = 20
        instances = []
        instances_lock = threading.Lock()

        # Use a barrier to ensure all threads try to get instance simultaneously
        barrier = threading.Barrier(num_threads)

        def get_instance():
            barrier.wait()  # Wait for all threads to be ready
            instance = DuckDBManager.get_instance()
            with instances_lock:
                instances.append(id(instance))

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=get_instance)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All instances should be the same (singleton pattern)
        assert len(set(instances)) == 1, "Multiple singleton instances detected!"

    def test_pool_size_boundary(self):
        """Test pool behavior at boundary conditions (size 1)."""
        pool = ConnectionPool(db_path=None, pool_size=1)

        # Should be able to get the single connection
        with pool.get_connection() as conn:
            result = conn.execute("SELECT 1").fetchone()
            assert result[0] == 1

            # Trying to get another connection should fail
            with pytest.raises(PoolExhaustedError):
                pool._acquire(timeout=0.1)

        # After release, should be able to get connection again
        with pool.get_connection() as conn:
            result = conn.execute("SELECT 2").fetchone()
            assert result[0] == 2

        pool.close()

    def test_concurrent_load_operations(self):
        """Test concurrent data load operations."""
        num_threads = 5
        results = []
        errors = []
        results_lock = threading.Lock()

        # Create temporary CSV files for each thread
        temp_files = []
        for i in range(num_threads):
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False
            ) as f:
                f.write("id,value\n")
                f.write(f"{i},{i * 10}\n")
                f.flush()
                temp_files.append(f.name)

        try:

            def load_data(thread_id: int):
                try:
                    csv_path = temp_files[thread_id]
                    table_name = f"table_{thread_id}"
                    result = DuckDBManager.load_data(csv_path, table_name=table_name)
                    with results_lock:
                        results.append((thread_id, result))
                except Exception as e:
                    with results_lock:
                        errors.append((thread_id, str(e)))

            # Run concurrent loads
            threads = []
            for i in range(num_threads):
                t = threading.Thread(target=load_data, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Verify all tables loaded
            assert len(errors) == 0, f"Errors occurred: {errors}"
            assert len(results) == num_threads

            # Verify each table exists and has correct data
            tables = DuckDBManager.list_tables()
            for i in range(num_threads):
                table_name = f"table_{i}"
                assert table_name in tables, f"Table {table_name} not found"

                # Query and verify data
                with DuckDBManager.get_connection() as conn:
                    result = conn.execute(f"SELECT * FROM {table_name}").fetchall()
                    assert len(result) == 1
                    assert result[0][0] == i
                    assert result[0][1] == i * 10

        finally:
            # Clean up temp files
            for path in temp_files:
                os.unlink(path)

    def test_metadata_lock_contention(self):
        """Test concurrent access to _loaded_tables metadata with actual contention."""
        num_threads = 10
        results = []
        errors = []
        results_lock = threading.Lock()

        # Create a single shared CSV file that all threads will load into the SAME table
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name\n")
            f.write("1,shared_data\n")
            f.flush()
            shared_csv = f.name

        try:

            def load_same_table(thread_id: int):
                try:
                    # All threads try to load into the SAME table with overwrite=True
                    # This tests actual lock contention on the metadata
                    table_name = "shared_contention_table"
                    DuckDBManager.load_data(
                        shared_csv, table_name=table_name, overwrite=True
                    )

                    # Access metadata concurrently
                    with DuckDBManager._metadata_lock:
                        # Simulate metadata access
                        _tables_copy = dict(DuckDBManager._loaded_tables)  # noqa: F841

                    # Query the data
                    with DuckDBManager.get_connection() as conn:
                        result = conn.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()

                    with results_lock:
                        results.append((thread_id, result[0]))

                except Exception as e:
                    with results_lock:
                        errors.append((thread_id, str(e)))

            # Use a barrier to ensure all threads start simultaneously for maximum contention
            barrier = threading.Barrier(num_threads)

            def load_with_barrier(thread_id: int):
                barrier.wait()  # Synchronize start
                load_same_table(thread_id)

            # Run concurrent operations that access metadata
            threads = []
            for i in range(num_threads):
                t = threading.Thread(target=load_with_barrier, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Verify all operations completed successfully
            assert len(errors) == 0, f"Errors occurred: {errors}"
            assert len(results) == num_threads

            # Verify metadata integrity - there should be exactly one table
            with DuckDBManager._metadata_lock:
                loaded_tables = dict(DuckDBManager._loaded_tables)

            assert (
                len(loaded_tables) == 1
            ), f"Expected 1 table, found {len(loaded_tables)}"
            assert "shared_contention_table" in loaded_tables

            # Verify all query results returned 1 row
            for thread_id, count in results:
                assert count == 1, f"Thread {thread_id}: expected 1 row, got {count}"

        finally:
            os.unlink(shared_csv)

    def test_pool_thread_safety_with_many_concurrent_acquisitions(self):
        """Test pool behavior under heavy concurrent acquisition load."""
        # Pool size should be large enough for all threads to succeed
        pool_size = 20
        num_threads = 20
        iterations_per_thread = 10
        pool = ConnectionPool(db_path=None, pool_size=pool_size)

        successful_acquisitions = []
        errors = []
        counter_lock = threading.Lock()

        def acquire_and_release(thread_id: int):
            """Acquire connection, do work, release it."""
            for i in range(iterations_per_thread):
                # All operations should succeed with adequate pool size and timeout
                with pool.get_connection(timeout=10.0) as conn:
                    result = conn.execute("SELECT 1").fetchone()
                    assert result[0] == 1
                    with counter_lock:
                        successful_acquisitions.append((thread_id, i))

        threads = []
        for thread_id in range(num_threads):
            t = threading.Thread(target=acquire_and_release, args=(thread_id,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        pool.close()

        # Should not have any errors
        assert (
            len(errors) == 0
        ), f"Errors occurred during concurrent acquisitions: {errors}"

        # ALL acquisitions should succeed - no allowance for failures
        expected_acquisitions = num_threads * iterations_per_thread
        assert (
            len(successful_acquisitions) == expected_acquisitions
        ), f"Expected {expected_acquisitions} successful acquisitions, got {len(successful_acquisitions)}"


class TestDuckDBManagerReset:
    """Tests for DuckDBManager reset functionality under concurrent access."""

    @pytest.fixture(autouse=True)
    def reset_duckdb(self):
        """Reset DuckDBManager before/after each test."""
        DuckDBManager.reset()
        yield
        DuckDBManager.reset()

    def test_reset_is_thread_safe(self):
        """Test that reset operations can be safely interleaved with connections."""
        num_threads = 10
        iterations = 5
        errors = []
        errors_lock = threading.Lock()
        barrier = threading.Barrier(num_threads)

        def reset_and_verify(thread_id: int):
            try:
                for i in range(iterations):
                    # Use barrier to synchronize threads at each iteration
                    barrier.wait()

                    # Create a connection and do some work
                    with DuckDBManager.get_connection() as conn:
                        conn.execute("SELECT 1").fetchall()

                    # Use barrier again to ensure all connections are released
                    barrier.wait()

                    # One thread performs reset at each iteration
                    if thread_id == 0:
                        DuckDBManager.reset()

                    # Wait for reset to complete
                    barrier.wait()

                    # Verify we can create a new connection after reset
                    with DuckDBManager.get_connection() as conn:
                        conn.execute("SELECT 2").fetchall()
            except Exception as e:
                with errors_lock:
                    errors.append((thread_id, str(e)))

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=reset_and_verify, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Should not have any errors
        assert len(errors) == 0, f"Errors during concurrent reset: {errors}"

    def test_concurrent_mixed_operations(self):
        """Test concurrent mixed operations (load, query, schema)."""
        num_threads = 15
        results = {
            "loads": 0,
            "queries": 0,
            "schemas": 0,
        }
        errors = []
        counters_lock = threading.Lock()

        # Create temp CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("id,name,value\n")
            f.write("1,test,100\n")
            f.write("2,other,200\n")
            temp_csv = f.name

        try:

            def mixed_operation(thread_id: int):
                if thread_id % 3 == 0:
                    # Load operation
                    table_name = f"mixed_table_{thread_id}"
                    DuckDBManager.load_data(temp_csv, table_name=table_name)
                    with counters_lock:
                        results["loads"] += 1
                elif thread_id % 3 == 1:
                    # Query operation - use DuckDB-appropriate query
                    with DuckDBManager.get_connection() as conn:
                        conn.execute("SHOW TABLES").fetchall()
                    with counters_lock:
                        results["queries"] += 1
                else:
                    # Schema operation
                    DuckDBManager.get_schema()
                    with counters_lock:
                        results["schemas"] += 1

            threads = []
            for i in range(num_threads):
                t = threading.Thread(target=mixed_operation, args=(i,))
                threads.append(t)
                t.start()

            for t in threads:
                t.join()

            # Should not have any errors
            assert len(errors) == 0, f"Errors during mixed operations: {errors}"

            # Loads should equal number of load threads (threads where thread_id % 3 == 0)
            expected_loads = (num_threads + 2) // 3  # ceil division
            assert results["loads"] == expected_loads

        finally:
            os.unlink(temp_csv)
