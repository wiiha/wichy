# In-Session Model Swap

## Overview

`model_str` is set once in `RootAgent.__init__()` and never mutated. The `call()` function is stateless per-call (creates a fresh OpenAI client each time), so changing `model_str` would immediately take effect on the next LLM call. Add a `@property` setter for `model_str` and a `/model` slash command to swap the model mid-session.

## Files to Modify

### 1. `src/wichy/root_agent/root_agent.py`

**Change attribute from public to private** (Line 52):

Before:
```python
self.model_str = model_str
```

After:
```python
self._model_str = model_str
```

**Add `model_str` property with setter** (After the `display_name` property, around line 105):

```python
@property
def model_str(self) -> str:
    """Return the current model string."""
    return self._model_str

@model_str.setter
def model_str(self, value: str) -> None:
    """Set a new model string for subsequent LLM calls."""
    self._model_str = value
    # Reset session map model so it re-initializes with new model on next extraction
    self._session_map_model = None
```

**No other `self.model_str` read sites need changes.** The property transparently returns `self._model_str`. All read sites (lines 56, 65, 216, 309, 323, 350, 362, 480) remain `self.model_str` and will work via the property.

The only assignment site was line 52 (`self.model_str = model_str`), which is now `self._model_str = model_str`.

### 2. `src/wichy/slash_commands.py`

**Add `/model` handler closure** in `__init__()`:

```python
def handle_model(line: str) -> str | None:
    """Handle /model - swap the LLM model mid-session."""
    parts = line.strip().split(maxsplit=1)
    if len(parts) < 2:
        return f"Current model: {self.root_agent.model_str}"
    new_model = parts[1].strip()
    if "/" not in new_model:
        return "[red]Invalid model format. Expected: <backend>/<model> (e.g., ollama/llama3)[/red]"
    old_model = self.root_agent.model_str
    self.root_agent.model_str = new_model
    return f"[green]Model changed:[/green] {old_model} → {new_model}"
```

**Add to `_handlers` dict:**
```python
"/model": handle_model,
```

**Add to NestedCompleter dict** (with sub-completions for known backends):
```python
"/model": {"ollama": None, "openai": None, "anthropic": None},
```

Note: The NestedCompleter sub-dict provides tab-completion for backends. After selecting a backend (e.g., `ollama`), the user types the model name manually.

**Add to `_descriptions` dict:**
```python
"/model": "Swap the LLM model mid-session (format: <backend>/<model>)",
```

## Key Design Notes

- `model_str` setter also resets `_session_map_model` to `None` so session map extraction re-initializes with the new model on its next extraction attempt.
- **Active task agents are NOT affected** — they have their own `model_str` set at construction time, independent of the root agent's model.
- Next `inject_model_str=True` tool call will automatically pick up the new model via the property getter.
- `call()` is stateless per-call (creates fresh OpenAI client each time), so no client state needs invalidation.
- The `"/"` format validation (`if "/" not in new_model`) catches obvious typos like just `"llama3"` without the backend prefix.

## Size

Small-medium — 1 file for property (root_agent.py) + 1 file for command (slash_commands.py)