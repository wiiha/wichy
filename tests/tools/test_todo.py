"""
Test cases for the TodoTool.
"""

import pytest

from src.wichy.tools.todo import TaskState, TodoTool


@pytest.fixture
def todo_tool():
    """Fixture to create a fresh TodoTool instance for each test."""
    return TodoTool()


def test_create_task(todo_tool):
    """Test creating a new task."""
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    assert "Created task 'Test Task' with ID: task_" in result
    assert len(todo_tool.task_store) == 1


def test_create_duplicate_task(todo_tool):
    """Test creating a task with a duplicate name."""
    # Create the first task
    todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    # Attempt to create a duplicate task
    result = todo_tool.execute(
        action="create",
        task_name="Test Task",
        task_description="This is another test task.",
    )
    assert "error: Task with name 'Test Task' already exists." in result


def test_update_task(todo_tool):
    """Test updating an existing task."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Update the task
    result = todo_tool.execute(
        action="update",
        task_id=task_id,
        task_name="Updated Task",
        task_description="This is an updated test task.",
    )
    assert f"Updated task '{task_id}'" in result
    assert todo_tool.task_store[task_id].task_name == "Updated Task"
    assert (
        todo_tool.task_store[task_id].task_description
        == "This is an updated test task."
    )


def test_update_nonexistent_task(todo_tool):
    """Test updating a non-existent task."""
    result = todo_tool.execute(
        action="update",
        task_id="nonexistent_task_id",
        task_name="Updated Task",
        task_description="This is an updated test task.",
    )
    assert "error: Task with ID 'nonexistent_task_id' not found." in result


def test_update_completed_task(todo_tool):
    """Test updating a completed task."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Mark the task as in progress
    todo_tool.execute(action="in_progress", task_id=task_id)

    # Complete the task
    todo_tool.execute(action="complete", task_id=task_id)

    # Attempt to update the completed task
    result = todo_tool.execute(
        action="update",
        task_id=task_id,
        task_name="Updated Task",
        task_description="This is an updated test task.",
    )
    assert f"error: Cannot update completed task '{task_id}'." in result


def test_complete_task(todo_tool):
    """Test completing a task."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Mark the task as in progress
    todo_tool.execute(action="in_progress", task_id=task_id)

    # Complete the task
    result = todo_tool.execute(action="complete", task_id=task_id)
    assert f"Completed task '{task_id}'" in result
    assert todo_tool.task_store[task_id].state == TaskState.COMPLETED


def test_complete_nonexistent_task(todo_tool):
    """Test completing a non-existent task."""
    result = todo_tool.execute(action="complete", task_id="nonexistent_task_id")
    assert "error: Task with ID 'nonexistent_task_id' not found." in result


def test_complete_already_completed_task(todo_tool):
    """Test completing an already completed task."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Mark the task as in progress
    todo_tool.execute(action="in_progress", task_id=task_id)

    # Complete the task
    todo_tool.execute(action="complete", task_id=task_id)

    # Attempt to complete the task again
    result = todo_tool.execute(action="complete", task_id=task_id)
    assert f"error: Task '{task_id}' is already completed." in result


def test_in_progress_task(todo_tool):
    """Test marking a task as in progress."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Mark the task as in progress
    result = todo_tool.execute(action="in_progress", task_id=task_id)
    assert f"Marked task '{task_id}' as in progress" in result
    assert todo_tool.task_store[task_id].state == TaskState.IN_PROGRESS


def test_in_progress_nonexistent_task(todo_tool):
    """Test marking a non-existent task as in progress."""
    result = todo_tool.execute(action="in_progress", task_id="nonexistent_task_id")
    assert "error: Task with ID 'nonexistent_task_id' not found." in result


def test_in_progress_invalid_state(todo_tool):
    """Test marking a task as in progress when it's not in PENDING state."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Mark the task as in progress
    todo_tool.execute(action="in_progress", task_id=task_id)

    # Attempt to mark the task as in progress again
    result = todo_tool.execute(action="in_progress", task_id=task_id)
    assert "error: Invalid state transition." in result


def test_view_task(todo_tool):
    """Test viewing details of a specific task."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # View the task
    result = todo_tool.execute(action="view", task_id=task_id)
    assert f"Task ID: {task_id}" in result
    assert "Name: Test Task" in result
    assert "Description: This is a test task." in result
    assert "State: PENDING" in result


def test_view_nonexistent_task(todo_tool):
    """Test viewing a non-existent task."""
    result = todo_tool.execute(action="view", task_id="nonexistent_task_id")
    assert "error: Task with ID 'nonexistent_task_id' not found." in result


def test_list_tasks(todo_tool):
    """Test listing all tasks."""
    # Create tasks
    todo_tool.execute(
        action="create",
        task_name="Test Task 1",
        task_description="This is the first test task.",
    )
    todo_tool.execute(
        action="create",
        task_name="Test Task 2",
        task_description="This is the second test task.",
    )

    # List all tasks
    result = todo_tool.execute(action="list")
    assert "Tasks:" in result
    assert "Test Task 1" in result
    assert "Test Task 2" in result


def test_list_empty_tasks(todo_tool):
    """Test listing tasks when there are none."""
    result = todo_tool.execute(action="list")
    assert "No tasks found." in result


def test_current_in_progress_task(todo_tool):
    """Test retrieving the current in-progress task."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Mark the task as in progress
    todo_tool.execute(action="in_progress", task_id=task_id)

    # Retrieve the current in-progress task
    result = todo_tool.execute(action="current_in_progress_task")
    assert f"Task ID: {task_id}" in result
    assert "Name: Test Task" in result
    assert "State: IN_PROGRESS" in result


def test_current_in_progress_task_none(todo_tool):
    """Test retrieving the current in-progress task when none exists."""
    # Retrieve the current in-progress task when none exists
    result = todo_tool.execute(action="current_in_progress_task")
    assert "No task is currently in progress." in result


def test_invalid_action(todo_tool):
    """Test executing an invalid action."""
    result = todo_tool.execute(action="invalid_action")
    assert "error: Invalid action: invalid_action" in result


def test_task_id_generation(todo_tool):
    """Test that task IDs are unique."""
    # Create multiple tasks
    result1 = todo_tool.execute(
        action="create",
        task_name="Test Task 1",
        task_description="This is the first test task.",
    )
    task_id1 = result1.split("ID: ")[1]

    result2 = todo_tool.execute(
        action="create",
        task_name="Test Task 2",
        task_description="This is the second test task.",
    )
    task_id2 = result2.split("ID: ")[1]

    assert task_id1 != task_id2


def test_task_state_transitions(todo_tool):
    """Test valid and invalid state transitions."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Verify initial state
    assert todo_tool.task_store[task_id].state == TaskState.PENDING

    # Transition to IN_PROGRESS
    todo_tool.execute(action="in_progress", task_id=task_id)
    assert todo_tool.task_store[task_id].state == TaskState.IN_PROGRESS

    # Transition to COMPLETED
    todo_tool.execute(action="complete", task_id=task_id)
    assert todo_tool.task_store[task_id].state == TaskState.COMPLETED

    # Attempt invalid transition from COMPLETED to IN_PROGRESS
    result = todo_tool.execute(action="in_progress", task_id=task_id)
    assert "error: Invalid state transition." in result


def test_task_timestamps(todo_tool):
    """Test that timestamps are updated correctly."""
    # Create a task
    result = todo_tool.execute(
        action="create", task_name="Test Task", task_description="This is a test task."
    )
    task_id = result.split("ID: ")[1]

    # Get initial timestamps
    initial_created_at = todo_tool.task_store[task_id].created_at
    initial_updated_at = todo_tool.task_store[task_id].updated_at

    # Update the task
    import time

    time.sleep(1)  # Ensure timestamp changes
    todo_tool.execute(
        action="update",
        task_id=task_id,
        task_name="Updated Task",
        task_description="This is an updated test task.",
    )

    # Verify timestamps
    assert todo_tool.task_store[task_id].created_at == initial_created_at
    assert todo_tool.task_store[task_id].updated_at != initial_updated_at
