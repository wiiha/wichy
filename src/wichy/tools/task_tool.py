from typing import Any, Dict, Optional

from pydantic import Field

from wichy.constants import HIDE_FROM_LLM_PREFIX
from wichy.helpers.string import strip_thinking_content
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error
from wichy.tools.registry import get_all_tools
from wichy.tools.task import (
    TASK_AGENT_DEFS,
    TaskAgent,
    TaskAgentDefinitionBase,
    generate_list_from_task_agent_defs,
)


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
        description="Maximum number of agentic turns (API round-trips) before stopping. Each turn is: agent calls tool, gets result, responds. Default: None (unlimited).",
        gt=0,
        le=9007199254740991,
    )
    model_str: Optional[str] = Field(
        None,
        description=HIDE_FROM_LLM_PREFIX
        + " What model to use. Should be a reference to a valid backend and model.",
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
    description = "The Task tool launches specialized agents that autonomously handle complex, multi-step tasks like bash operations, codebase exploration, implementation planning, and general-purpose research. Each agent type has specific capabilities and tools available to it."
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

- If you want to read a specific file path, use the read_file or glob tool instead of the Task tool, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use the glob tool instead of the Task tool, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the read_file tool instead of the task tool, to find the match more quickly
- Other tasks that are not related to the agent descriptions above

Usage notes:

- Always include a short description (3-5 words) summarizing what the agent will do
- Launch multiple agents concurrently whenever possible, to maximize performance; that is, use multiple Task tool calls in a single message.
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- Each agent invocation starts fresh and you should provide a detailed task description with all necessary context.
- Provide clear, detailed prompts so the agent can work autonomously and return exactly the information you need.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple Task tool use content blocks. For example, if you need to launch both a build-validator agent and a test-runner agent in parallel, send a single message with both tool calls.

Example usage:

<example_agent_descriptions>
"test-runner": use this agent after you are done writing code to run tests
"greeting-responder": use this agent when to respond to user greetings with a friendly joke
</example_agent_descriptions>

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
Since a significant piece of code was written, the task was completed, now use the test-runner agent to run the tests
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

    def execute(self, *args: Any, **kwargs: Any) -> str:
        """run task agent"""
        prompt: str = kwargs["prompt"]
        subagent_type: str = kwargs["subagent_type"]
        max_turns: Optional[int] = kwargs.get("max_turns")
        model_str: str = kwargs["model_str"]
        all_task_agent_defs: Dict[str, TaskAgentDefinitionBase] = {}
        # "alias" each def by generating the lower case version
        for x in TASK_AGENT_DEFS.values():
            all_task_agent_defs[x.name] = x
            all_task_agent_defs[x.name.lower()] = x

        agent_def = all_task_agent_defs.get(subagent_type, None)
        if not agent_def:
            return format_error(f"task has no subagent_type named {subagent_type}")

        # Get all tools from registry, excluding TaskAgentTool to prevent infinite recursion
        tools = [t for t in get_all_tools() if t is not TaskAgentTool]

        sa = TaskAgent(
            agent_definition=agent_def,
            prompt=prompt,
            model=model_str,
            all_tools_not_instantiated=tools,
            max_turns=max_turns,
        )

        result: str = sa.run()
        old_result = result
        result = strip_thinking_content(result)
        if result.strip() == "":
            # handles the case of an agent only returning thinking content
            result = old_result
        return result
