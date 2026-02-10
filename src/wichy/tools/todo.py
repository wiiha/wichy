import time
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from wichy.tools.base import BaseTool, ParametersModel


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
class TodoParameters(ParametersModel):
    action: TodoActions = Field(
        ...,
        description="Action to perform on the todo item: create, update, complete, view, list, in_progress",
    )
    task_name: Optional[str] = Field(None, description="Name of the task")
    task_description: Optional[str] = Field(None, description="Description of the task")
    task_id: Optional[str] = Field(
        None, description="ID of the task to perform action on"
    )

    def info(self):
        out = f"{self.action}"
        if self.task_name:
            out += " " + self.task_name
        if self.task_id:
            out += " " + self.task_id

        return out


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
    description = (
        "Manage todo tasks with create, update, complete, view, list, and in_progress actions. "
        + "Todo lists are not shared between agents."
    )
    parameters_model = TodoParameters
    description_long = """
Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

#### When to Use This Tool

Use this tool proactively in these scenarios:

1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
3. User explicitly requests todo list - When the user directly asks you to use the todo list
4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
5. After receiving new instructions - Immediately capture user requirements as todos
6. When you start working on a task - Mark it as in_progress BEFORE beginning work. Ideally you should only have one todo as in_progress at a time
7. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

#### When NOT to Use This Tool

Skip using this tool when:

1. There is only a single, straightforward task
2. The task is trivial and tracking it provides no organizational benefit
3. The task can be completed in less than 3 trivial steps
4. The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

#### Task States and Management

1. **Task States**: Use these states to track progress:
   - PENDING: Task not yet started
   - IN_PROGRESS: Currently working on (limit to ONE task at a time)
   - COMPLETED: Task finished successfully

2. **Task Management**:
   - Update task status in real-time as you work
   - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
   - Exactly ONE task must be in_progress at any time (not less, not more)
   - Complete current tasks before starting new ones
   - Remove tasks that are no longer relevant from the list entirely

3. **Task Completion Requirements**:
   - ONLY mark a task as completed when you have FULLY accomplished it
   - If you encounter errors, blockers, or cannot finish, keep the task as in_progress
   - When blocked, create a new task describing what needs to be resolved
   - Never mark a task as completed if:
     - Tests are failing
     - Implementation is partial
     - You encountered unresolved errors
     - You couldn't find necessary files or dependencies

4. **Task Breakdown**:
   - Create specific, actionable items
   - Break complex tasks into smaller, manageable steps
   - Use clear, descriptive task names
   - Always provide both forms:
     - content: "Fix authentication bug"
     - activeForm: "Fixing authentication bug"

When in doubt, use this tool. Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully.
"""

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
