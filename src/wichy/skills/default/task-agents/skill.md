---
name: task-agents
description: Guidelines for effectively delegating work to task agents — when to use them, how to brief them, and how to avoid common failure modes.
metadata:
  tags: [workflow, agents, delegation, process]
---

# Task Agents

## Core Principle

Task agents are single-use, stateless workers. They do one job and return. They retain no memory between requests — even to the same agent twice. Every invocation must be self-contained.

## When to Use Task Agents

- **Research tasks**: Exploring codebases, searching patterns, reading files. Use `Explore` agents.
- **Parallelizable work**: Independent subtasks that can run simultaneously. Use `general-purpose` or `Bash` agents.
- **Execution tasks**: Running commands, tests, scripts. Use `Bash` agents.
- **Web research**: Fetching URLs, searching the web. Use `web-research` agents.

Do NOT use task agents for:

- Tasks that require shared mutable state.
- Tasks that need results from each other in the same turn (brief each independently with all needed context).

## Parallel vs Sequential Invocation

**Launch parallel agents in a SINGLE message** when tasks are independent:

```
# ✅ Good — all 4 research agents launch simultaneously
[task agent 1: explore tool execution flow]
[task agent 2: explore human verification flow]
[task agent 3: explore context handler thread safety]
[task agent 4: web research on parallel patterns]
```

**Use sequential agents** when each step depends on the previous:

```
# Sequential: investigate → understand → fix → verify
[task agent: investigate failing tests, report back only]
# ... analyze findings yourself ...
[task agent: fix the specific bug identified]
[task agent: run tests to verify]
```

## Decomposing Feature Work

Break features into **increments** that can be implemented independently:

1. **Research increment**: Understand the problem before coding.
2. **Configuration increment**: Add settings, flags, config fields.
3. **Core implementation increment**: The actual feature logic.
4. **Integration increment**: Wiring everything together.

Example: "Parallel tool execution" decomposed into:

- Increment 1: Human verification lock (isolated, single file)
- Increment 2: Config field for `parallel_exec`
- Increment 3: CLI flag `--seq-exec`
- Increment 4: Core implementation in `_handle_tools_base`

Each increment gets its own agent with **exact file paths and line numbers**.

## How to Brief a Task Agent

A good task agent prompt includes:

1. **What to do** — specific, concrete instructions
2. **All needed context** — code snippets, file paths, relevant conventions
3. **Output format** — what to return, how structured
4. **Constraints** — what NOT to do, what to avoid
5. **Boundaries** — "do NOT modify any files" vs. "implement the change and run tests"

Bad: "fix the bug in the auth module"
Good: "Read src/auth/login.py and find why the session check at line 47 always returns None. Focus on the `SessionCache` class. Return a concise description of the root cause. Do NOT modify any files."

## Providing Exact Changes

When tasking an agent to modify code:

- **Provide exact file paths** — `src/wichy/agent/core.py`, not "the agent core file"
- **Provide line numbers or context** — "around line 92" or "in the `_handle_tools_base` method"
- **Provide exact old_content/new_content for replace_text** — whitespace and newlines must match exactly
- **Show the desired code snippet** — agents can implement from a sketch

Example:

```
Change line 92 from:
    context = ContextHandler(custom_suffix=self._name, sub_dir="task_agents")
to:
    context = ContextHandler(custom_suffix=f"{self._name}_{gen_id()}", sub_dir="task_agents")
```

## Searching in Files

When searching for patterns that may have many matches:

- Run a count/search first to estimate scope.
- If results exceed ~500 matches, narrow the search before continuing.
- `search_in_files` returns a WARNING with a 20-line sample when over 500 matches — it does NOT return full content.
- Each output line is truncated at 300 characters.
- When briefing agents to search, tell them to count first, warn if over 500, and narrow before continuing.

## File Modification Rules

When a task agent is told to modify a file:

- It must READ the file first before making any changes.
- If told to replace text, give exact `old_content` and `new_content` — whitespace and newlines must match exactly.
- If told to create a file, write the full content (never partial).
- After any modification that might affect imports or types, run the test suite.

## Error Recovery Pattern

If tests fail after a change:

1. **Investigate first**: Task an Explore or Bash agent to investigate and report back — do NOT fix.
2. **Understand**: Analyze the findings yourself. Determine if the bug is in implementation or tests.
3. **Fix**: Task an agent with the specific fix based on your diagnosis.
4. **Verify**: Run tests again.

Never task an agent to "fix failing tests" without first understanding why they fail.

## Investigation Agents

For complex debugging, use a two-phase approach:

**Phase 1: Investigate (report-only)**

```
Task: Run the tests and report all failures. For each failure, include:
- Test name and file
- Full error traceback
- Brief analysis of what the test checks and why it likely fails
Do NOT attempt to fix anything.
```

**Phase 2: Fix (after you analyze)**

```
Task: Fix the specific bug identified. In src/cli_parser.py, add the missing
`seq_exec: bool = False` field to the CliConfig dataclass at line 24.
```

## Common Failure Modes

| Failure                         | Cause                              | Fix                                      |
| ------------------------------- | ---------------------------------- | ---------------------------------------- |
| Agent can't find file           | Vague path ("the config file")     | Use exact paths                          |
| Agent modifies wrong code       | No line numbers or context         | Pinpoint location                        |
| Agent changes too much          | Unclear boundaries                 | Specify what NOT to touch                |
| Tests fail after change         | Forgot to import, wrong field name | Run tests after implementation           |
| Two agents collide              | Same file modified in parallel     | Different files per agent, or sequential |
| Agent asks clarifying questions | Prompt was vague                   | Rewrite with full context                |
