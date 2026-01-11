import time
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .base import BaseTool


# Task state enum
class TaskState(Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


# Todo actions type
TodoActions = Literal[
    "create",
    "update",
    "complete",
    "view",
    "list",
    "in_progress",
    "current_in_progress_task",
]


# Task data model
class Task(BaseModel):
    task_id: str
    task_name: str
    task_description: Optional[str] = None
    state: TaskState = TaskState.PENDING
    created_at: str = Field(default_factory=lambda: "")
    updated_at: str = Field(default_factory=lambda: "")


# Todo tool parameters
class TodoParameters(BaseModel):
    action: TodoActions = Field(
        ...,
        description="Action to perform on the todo item: create, update, complete, view, list, in_progress",
    )
    task_name: Optional[str] = Field(None, description="Name of the task")
    task_description: Optional[str] = Field(None, description="Description of the task")
    task_id: Optional[str] = Field(
        None, description="ID of the task to perform action on"
    )


# Todo tool implementation
class TodoTool(BaseTool):
    """
    A tool for managing todo tasks.
    Supports create, update, complete, view, list, and in_progress actions.
    Todo tasks are not shared between instances of the tool. I.e. for two
    agents to have the same tasks, the tool has to be instantiated and then
    passed to each agent separately.
    """

    name = "todo"
    description = "Manage todo tasks with create, update, complete, view, list, and in_progress actions."
    parameters_model = TodoParameters

    def __init__(self):
        super().__init__()
        # Initialize in-memory task store
        self.task_store: dict[str, Task] = {}

    def execute(
        self,
        action: TodoActions,
        task_name: Optional[str] = None,
        task_description: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> str:
        """
        Execute the appropriate todo action based on parameters.
        """
        try:
            if action == "create":
                return self._create_task(task_name, task_description)
            elif action == "update":
                return self._update_task(task_id, task_name, task_description)
            elif action == "complete":
                return self._complete_task(task_id)
            elif action == "view":
                return self._view_task(task_id)
            elif action == "list":
                return self._list_tasks()
            elif action == "in_progress":
                return self._in_progress_task(task_id)
            elif action == "current_in_progress_task":
                return self._list_current_in_progress()
            else:
                return f"error: Invalid action: {action}"
        except Exception as e:
            return f"error: {str(e)}"

    def _create_task(self, task_name: str, task_description: Optional[str]) -> str:
        """
        Creates a new task with PENDING state.
        """
        # Check if task already exists with same name
        for existing_task in self.task_store.values():
            if existing_task.task_name == task_name:
                return f"error: Task with name '{task_name}' already exists."

        # Create new task
        new_task = Task(
            task_id=self._generate_task_id(),
            task_name=task_name,
            task_description=task_description,
            state=TaskState.PENDING,
            created_at=self._get_timestamp(),
            updated_at=self._get_timestamp(),
        )

        # Store in task store
        self.task_store[new_task.task_id] = new_task

        return f"Created task '{task_name}' with ID: {new_task.task_id}"

    def _update_task(
        self, task_id: str, task_name: Optional[str], task_description: Optional[str]
    ) -> str:
        """
        Updates an existing task with new name or description.
        """
        # Validate task exists
        if task_id not in self.task_store:
            return f"error: Task with ID '{task_id}' not found."

        # Get current task
        current_task = self.task_store[task_id]

        # Validate task is not completed
        if current_task.state == TaskState.COMPLETED:
            return f"error: Cannot update completed task '{task_id}'."

        # Update task
        if task_name is not None:
            current_task.task_name = task_name
        if task_description is not None:
            current_task.task_description = task_description

        # Update timestamps
        current_task.updated_at = self._get_timestamp()

        # Update in store
        self.task_store[task_id] = current_task

        return f"Updated task '{task_id}'"

    def _complete_task(self, task_id: str) -> str:
        """
        Completes a task (only allowed for IN_PROGRESS state).
        """
        # Validate task exists
        if task_id not in self.task_store:
            return f"error: Task with ID '{task_id}' not found."

        # Get current task
        current_task = self.task_store[task_id]

        # Validate state
        if current_task.state == TaskState.COMPLETED:
            return f"error: Task '{task_id}' is already completed."

        # Validate state transition
        if current_task.state != TaskState.IN_PROGRESS:
            return f"error: Invalid state transition. Task '{task_id}' is in '{current_task.state.value}' state. Only IN_PROGRESS tasks can be marked as complete."

        # Complete task
        current_task.state = TaskState.COMPLETED
        current_task.updated_at = self._get_timestamp()

        # Update in store
        self.task_store[task_id] = current_task

        return f"Completed task '{task_id}'"

    def _in_progress_task(self, task_id: str) -> str:
        """
        Marks a task as in progress (only allowed for PENDING state).
        """
        # Validate task exists
        if task_id not in self.task_store:
            return f"error: Task with ID '{task_id}' not found."

        # Get current task
        current_task = self.task_store[task_id]

        # Validate state
        if current_task.state != TaskState.PENDING:
            return f"error: Invalid state transition. Task '{task_id}' is in '{current_task.state.value}' state. Only PENDING tasks can be marked as in progress."

        for existing_task in self.task_store.values():
            if existing_task.state == TaskState.IN_PROGRESS:
                return f"error: task {existing_task.task_id} is already IN_PROGRESS. Only one task at a time can be in progress"

        # Mark task as in progress
        current_task.state = TaskState.IN_PROGRESS
        current_task.updated_at = self._get_timestamp()

        # Update in store
        self.task_store[task_id] = current_task

        return f"Marked task '{task_id}' as in progress"

    def _view_task(self, task_id: str) -> str:
        """
        Views details of a specific task by ID.
        """
        # Validate task exists
        if task_id not in self.task_store:
            return f"error: Task with ID '{task_id}' not found."

        # Get task
        task = self.task_store[task_id]

        # Format task details
        details = (
            f"Task ID: {task.task_id}\n"
            f"Name: {task.task_name}\n"
            f"Description: {task.task_description or 'N/A'}\n"
            f"State: {task.state.value}\n"
            f"Created: {task.created_at}\n"
            f"Updated: {task.updated_at}"
        )

        return details

    def _list_current_in_progress(self) -> str:
        """
        Lists task that is currently in progress.
        """
        if not self.task_store:
            return "No task is currently in progress."

        # List all tasks
        task_in_progress = "No task is currently in progress."
        for _, task in self.task_store.items():
            if task.state == TaskState.IN_PROGRESS:
                task_in_progress = (
                    f"Task ID: {task.task_id}\n"
                    f"Name: {task.task_name}\n"
                    f"Description: {task.task_description or 'N/A'}\n"
                    f"State: {task.state.value}\n"
                    f"Created: {task.created_at}\n"
                    f"Updated: {task.updated_at}"
                )

        return task_in_progress

    def _list_tasks(self) -> str:
        """
        Lists all tasks.
        """
        if not self.task_store:
            return "No tasks found."

        # List all tasks
        task_list = "Tasks:\n"
        for task_id, task in self.task_store.items():
            task_list += (
                f"\nID: {task.task_id}\n"
                f"Name: {task.task_name}\n"
                f"State: {task.state.value}\n"
                f"Created: {task.created_at}\n"
                f"Updated: {task.updated_at}\n"
            )

        return task_list

    def _generate_task_id(self) -> str:
        """
        Generates a unique task ID in the format task_YYYYMMDDHHMMSSNNN.
        """
        timestamp = time.strftime("%Y%m%d%H%M%S")
        # Add a counter to ensure uniqueness
        counter = 0
        while True:
            id_str = f"task_{timestamp}_{counter}"
            if id_str not in self.task_store:
                return id_str
            counter += 1

    def _get_timestamp(self) -> str:
        """
        Returns current timestamp in YYYY-MM-DD HH:MM:SS format.
        """
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
