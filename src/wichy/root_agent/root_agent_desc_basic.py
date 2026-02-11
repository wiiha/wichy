root_agent_desc = """---
name: root-agent-basic
description: A general-purpose agent that interprets user requests, delegates to tools/agents when useful, and returns a validated final response. System prompt is rather short and user can have more influence by follow up instructions.

# Tools specified here can be considered the base tools for the
# root agent. User CLI flags for adding and removing tools will
# take precedence over the list here. Comment out the tools property
# if it should be ignored
# tools: tool1, tool2, ...

# Specify model to use for the root agent. The format for specifying model
# follows that of flag --model-str. User CLI flag takes precedence over the
# value specified here.
model: ollama/ministral-3:8b

include_env_info: true
---

You are a helpful assistant. Whenever possible, defer tasks to available agents. Agents DO NOT retain memory between requests to them, even if you call the same agent twice.
"""
