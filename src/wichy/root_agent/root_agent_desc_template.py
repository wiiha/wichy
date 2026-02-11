root_agent_desc_template = """---
name: <agent-name>
description: <Short description of the agent's purpose and capabilities. Keep this concise, what the agent is for and how it should behave. No line breaks allowed>

# Tools specified here can be considered the base tools for the
# agent. User CLI flags for adding and removing tools will
# take precedence over the list here. Comment out the tools property
# if it should be ignored. Use `wichy ls tools` to see available tools
# tools: tool1, tool2, ...

# Specify model to use for the agent. The format for specifying model
# follows that of flag --model-str. User CLI flag takes precedence over the
# value specified here.
model: <model-identifier>

include_env_info: <true|false>
---

<Instructional system prompt text for the agent. Keep it focused and prescriptive. Include any important behavior rules such as delegating to tools when available, whether agents retain memory, response style, or safety constraints. Example sentence below can be edited or replaced.>
<conditional><condition><tool>my_condition_tool</tool></condition>
Text that be part of the system prompt if my_condition_tool us among the available tools for the root agent.
</conditional>

You are a helpful assistant. Whenever possible, defer tasks to available agents/tools. Agents DO NOT retain memory between requests to them, even if you call the same agent twice."""
