# `context_add()` Hook

## Overview

Hooks currently have `print()` and 8 hook types, but no way to access the conversation context. Add `context_add(role, content)` allowing hooks to inject messages into the active context. Follow the same pattern as the context editor's `_active_context = None` + `set_active_context(ctx)` module-level global pattern. Source of truth is always the JSONL on disk — `context_add()` goes through `ContextHandler.add()` → `_write_line()`.

## Files to Create

### `src/wichy/hooks/context_access.py` (new file)

```python
"""Allow hooks to read/modify the active conversation context."""
from wichy.console import user_console

_active_context = None  # Will be ContextHandler

def set_active_context(ctx):
    """Set the currently active context handler (called after RootAgent construction)."""
    global _active_context
    _active_context = ctx

def context_add(role: str, content: str) -> bool:
    """Add a message to the active conversation context.

    Returns True if successful, False if no active context.
    If no context is set, prints a warning and returns False (no-op).
    """
    if _active_context is None:
        user_console.print(
            "[yellow]Warning: context_add() called but no active context is set. "
            "Message not added.[/yellow]"
        )
        return False
    _active_context.add(role=role, content=content)
    return True
```

## Files to Modify

### 1. `src/wichy/hooks/__init__.py`

**Add import** (around line 78):
```python
from wichy.hooks.context_access import context_add, set_active_context
```

**Add to `__all__` list** (after `"print"`, around line 130):
```python
"context_add",
"set_active_context",
```

### 2. `src/wichy/__main__.py`

**Insert between lines 336 and 338** (after `build_agent_from_config`, before `SESSION_START` hook fires):

```python
# Set active context for hooks (works in all modes: REPL, pipeline, with/without server)
from wichy.hooks.context_access import set_active_context as hooks_set_active_context
hooks_set_active_context(root_agent.context)
```

This sets the context in `main()` (not `start_server()`) so it works in pipeline mode too.

### 3. `src/wichy/root_agent/root_agent.py` — `reset_context()` method

Inside the existing try block, **after** the line `context_editor_api.set_active_context(self.context)`, add:

```python
from wichy.hooks.context_access import set_active_context as hooks_set_active_context
hooks_set_active_context(self.context)
```

This ensures the hook context tracks context replacements (reset creates a new ContextHandler).

### 4. `src/wichy/root_agent/root_agent.py` — `compact_context()` method

Same pattern as `reset_context()` — after the existing `context_editor_api.set_active_context(self.context)` line, add:

```python
from wichy.hooks.context_access import set_active_context as hooks_set_active_context
hooks_set_active_context(self.context)
```

This ensures the hook context tracks context replacements (compact creates a new ContextHandler).

## Key Design Notes

- `set_active_context()` is called in `main()` (not `start_server()`) so it works in pipeline mode too.
- It's also updated when context is replaced (reset/compact) so hooks always point to the current ContextHandler.
- Thread safety: `ContextHandler.add()` already acquires `self._lock` and atomically appends to JSONL.
- Source of truth is always the JSONL on disk — `context_add()` goes through the same `add()` → `_write_line()` path.
- `context_add()` returns `bool` so hooks can check if the operation succeeded.
- The import paths use `from wichy.hooks.context_access import ...` which follows the existing module structure pattern in `hooks/__init__.py`.

## Size

Medium — 1 new file + 3 modified files