# Task Tool Max Rounds

## Overview

`TaskAgentParameters.max_turns` is defined and accepted in the `execute()` method but is **completely unused** — it's silently discarded. The `_process()` method runs an unbounded while-loop. This feature makes `max_turns` functional: when the limit is reached, the agent is forced to produce a summary response via `_gen_summary()`.

## Files to Modify

### 1. `src/wichy/tools/task_tool.py`

**Unhide max_turns from LLM** (Line ~33):

Change the `max_turns` field description from:
```python
description="Maximum number of agentic turns (API round-trips) before stopping. Used internally for warmup.",
```
to:
```python
description="Maximum number of agentic turns (API round-trips) before stopping. Each turn is: agent calls tool, gets result, responds. Default: None (unlimited).",
```

**Pass max_turns to TaskAgent** (Lines ~150-155):

Change from:
```python
sa = TaskAgent(
    agent_definition=agent_def,
    prompt=prompt,
    model=model_str,
    all_tools_not_instantiated=tools,
)
```
to:
```python
sa = TaskAgent(
    agent_definition=agent_def,
    prompt=prompt,
    model=model_str,
    all_tools_not_instantiated=tools,
    max_turns=max_turns,
)
```

### 2. `src/wichy/tools/task/base.py`

**Add max_turns parameter to `__init__()`** (Lines 37-43):

```python
def __init__(
    self,
    agent_definition: TaskAgentDefinitionBase,
    prompt: str,
    model: str,
    all_tools_not_instantiated: list[BaseTool],
    max_turns: Optional[int] = None,  # NEW
):
```

**Store max_turns and initialize turns_remaining** (After line ~47, after `self._name = agent_definition.name`):

```python
self._max_turns = max_turns
self._turns_remaining = None  # Set during _process
```

**Inject turns-remaining into system prompt** (Lines ~95-98, after `context.add(role=ROLE_USER, content=prompt)`):

```python
if self._max_turns is not None:
    context.context[0]["content"] += f"\n\nYou have {self._max_turns} turns available for this task."
    self._turns_remaining = self._max_turns
else:
    self._turns_remaining = None
```

**Add round counter and max_turns enforcement in `_process()`** (Lines ~170-242):

The current loop structure:
```python
while self._handle_tools(tools, response.message):
    try:
        response = call(...)
    ...
```

Becomes:
```python
turns_used = 1  # Initial call (lines 181-197) counts as turn 1

while self._handle_tools(tools, response.message):
    turns_used += 1

    # Max turns enforcement — exceeded limit, force summary
    if self._max_turns is not None and turns_used > self._max_turns:
        return self._gen_summary()

    # Penultimate round warning
    if self._max_turns is not None and turns_used == self._max_turns:
        warning = ("This is your last turn with tools available. "
                   "The next turn will be tool-free and you must provide a final answer. "
                   "Use your remaining tools wisely.")
        self.context.add(role=ROLE_USER, content=warning)

    # Update turns-remaining in system prompt
    if self._turns_remaining is not None:
        remaining = self._max_turns - turns_used
        base = context_data[0]["content"].split("\n\nYou have ")[0] if "\n\nYou have " in context_data[0]["content"] else context_data[0]["content"]
        self.context.context[0]["content"] = base + f"\n\nYou have {remaining} turns remaining for this task."

    try:
        response = call(...)
    ...
```

**No changes to `_gen_summary()`** — it already calls the LLM with `tool_defs=None`, forcing a text-only response. This is exactly what we need for the forced summary when max turns is exceeded.

## Key Design Notes

- `max_turns=None` (default) = current unbounded behavior, no changes to system prompt
- Each `call()` invocation = 1 turn (initial call + each while-loop iteration)
- On penultimate turn: inject user message warning, then one more tool-capable turn
- On turn after max: call `_gen_summary()` with `tool_defs=None` for forced text-only summary
- System prompt dynamically updated with remaining turns count
- Active task agents are independent — their own `max_turns` is set at construction time

## Size

Small — 2 files: `task_tool.py` + `base.py`