from enum import Enum
from typing import Dict, Optional

from pydantic import Field

from wichy.helpers.string import strip_thinking_content, truncate_to_len
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.bash import BashTool
from wichy.tools.fetch_webpage import FetchWebPageTool
from wichy.tools.file_explorer import CatFileContentTool, ListFilesTool, WriteFileTool
from wichy.tools.file_search_in import SearchRecursiveTool
from wichy.tools.glob import GlobTool
from wichy.tools.search_ddg import SearchDDGTool
from wichy.tools.task import (
    TASK_AGENT_DEFS,
    TaskAgent,
    TaskAgentDefinitionBase,
    generate_list_from_task_agent_defs,
)
from wichy.tools.todo import TodoTool
from wichy.tools.tree import TreeTool

TOOLS_FOR_TASK_AGENTS: list[BaseTool] = [
    BashTool,
    CatFileContentTool,
    FetchWebPageTool,
    GlobTool,
    ListFilesTool,
    SearchDDGTool,
    SearchRecursiveTool,
    TodoTool,
    TreeTool,
    WriteFileTool,
]


class TaskAgentParameters(ParametersModel):
    description: str = Field(
        ...,
        description="A short (3-5 word) description of the task",
    )
    prompt: str = Field(
        ...,
        description="The task for the agent to perform",
    )
    subagent_type: str = Field(
        ...,
        description="The type of specialized agent to use for this task",
    )
    max_turns: Optional[int] = Field(
        None,
        description="Maximum number of agentic turns (API round-trips) before stopping. Used internally for warmup.",
        gt=0,
        le=9007199254740991,
    )
    model_str: str = Field(
        None,
        description="HIDE_FROM_LLM What model to use. Should be a reference to a valid backend and model.",
    )

    def info(self):

        info_parts = []

        info_parts.append(f'type="{self.subagent_type}"')
        info_parts.append(f'description="{self.description}"')

        if self.model_str:
            info_parts.append(f'model="{self.model_str}"')

        return " ".join(info_parts)


class TaskAgentTool(BaseTool):
    name = "task"
    description = "The Task tool launches specialized agents that autonomously handle complex, multi-step tasks like bash operations, codebase exploration, implementation planning, and general-purpose research. Each agent type has specific capabilities and tools."
    parameters_model = TaskAgentParameters
    description_long = (
        """
Launch a new agent to handle complex, multi-step tasks autonomously.

The Task tool launches specialized agents that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

Available agent types and the tools they have access to:
"""
        + generate_list_from_task_agent_defs(TASK_AGENT_DEFS)
        + """
When using the Task tool, you must specify a subagent_type parameter to select which agent type to use.

When NOT to use the Task tool:

- If you want to read a specific file path, use the cat or glob tool instead of the Task tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the glob tool instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the cat tool instead of the task tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above

Usage notes:

- Always include a short description (3-5 words) summarizing what the agent will do
- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- Each agent invocation starts fresh and you should provide a detailed task description with all necessary context.
- Provide clear, detailed prompts so the agent can work autonomously and return exactly the information you need.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your judgement.
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple Task tool use content blocks. For example, if you need to launch both a build-validator agent and a test-runner agent in parallel, send a single message with both tool calls.

Example usage:

<example_agent_descriptions>
"test-runner": use this agent after you are done writing code to run tests
"greeting-responder": use this agent when to respond to user greetings with a friendly joke
</example_agent_description>

<example>
user: "Please write a function that checks if a number is prime"
assistant: Sure let me write a function that checks if a number is prime
assistant: First let me use the Write tool to write a function that checks if a number is prime
assistant: I'm going to use the Write tool to write the following code:
<code>
function isPrime(n) {
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {
    if (n % i === 0) return false
  }
  return true
}
</code>
<commentary>
Since a significant piece of code was written and the task was completed, now use the test-runner agent to run the tests
</commentary>
assistant: Now let me use the test-runner agent to run the tests
assistant: Uses the Task tool to launch the test-runner agent
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the Task tool to launch the greeting-responder agent"
</example>
"""
    )

    def execute(
        self,
        description: str,
        prompt: str,
        subagent_type: str,
        max_turns: Optional[int],
        model_str: str,
    ) -> str:
        """run task agent"""
        all_task_agent_defs: Dict[str, TaskAgentDefinitionBase] = {}
        # "alias" each def by generating the lower case version
        for x in TASK_AGENT_DEFS.values():
            all_task_agent_defs[x.name] = x
            all_task_agent_defs[x.name.lower()] = x

        agent_def = all_task_agent_defs.get(subagent_type, None)
        if not agent_def:
            return f"error: task has no subagent_type named " + subagent_type

        sa = TaskAgent(
            agent_definition=agent_def,
            prompt=prompt,
            model=model_str,
            all_tools_not_instantiated=TOOLS_FOR_TASK_AGENTS,
        )

        result = sa.run()
        old_result = result
        result = strip_thinking_content(result)
        if result.strip() == "":
            # handles the case of an agent only returning thinking content
            result = old_result
        return result
