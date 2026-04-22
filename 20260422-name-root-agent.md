# Name Root Agent

## Overview

`RootAgent._name` is a template identifier used for selection/dispatch (e.g., `"root-agent-basic"`). It must NOT change. Currently, the display name "Assistant" is hardcoded in two places in the output. Add a `_display_name` attribute with fallback to "Assistant", a `--name` CLI flag, and a `/name` slash command for in-session changes.

## Files to Modify

### 1. `src/wichy/root_agent/root_agent.py`

**Add `display_name` parameter to `__init__()`** (Line 38):

```python
def __init__(
    self,
    model_str,
    tools: List[BaseTool],
    name: str = "NOT SET",
    display_name: Optional[str] = None,  # NEW
    context=None,
    ...
):
```

**Store display_name** (After line 51, after `self._name = name`):

```python
self._display_name = display_name
```

**Add `display_name` property** (After line 100, after the `name` property):

```python
@property
def display_name(self) -> str:
    """Return the display name for terminal output. Falls back to 'Assistant'."""
    return self._display_name or "Assistant"
```

**Replace hardcoded "### Assistant" in `display_thinking_content`** (Line 141):

Before:
```python
"\n---\n\n### Assistant\n"
```

After:
```python
f"\n---\n\n### {self.display_name}\n"
```

Note: `self` is available here since `display_thinking_content` is defined inside `handle_tools()` which is a method of RootAgent.

**Optionally add display_name to Root Agent Info** (Line 65):

After the template name line, add:
```python
if self._display_name:
    f"\n- **display name:** {self._display_name}"
```

### 2. `src/wichy/repl.py`

**Replace hardcoded "### Assistant"** (Line 122):

Before:
```python
user_console.print(Markdown("\n---\n\n### Assistant\n"))
```

After:
```python
user_console.print(Markdown(f"\n---\n\n### {self.root_agent.display_name}\n"))
```

### 3. `src/wichy/cli_parser.py`

**Add to `CliConfig` dataclass** (Around line 28, after `model_str`):

```python
display_name: str = ""
```

**Add CLI argument** (In `_add_global_arguments()`, around line 99 after `--model-str`):

```python
self.parser.add_argument(
    "--name",
    dest="display_name",
    default="",
    help="Set a display name for the root agent (shown in terminal headers instead of 'Assistant')",
)
```

**Add to `parse()` method** (Around line 286):

```python
display_name=parsed.display_name,
```

### 4. `src/wichy/agent_builder.py`

**Add `display_name` kwarg in `build()` method** (Lines 119-128):

```python
root_agent = RootAgent(
    model_str=model_str,
    tools=self.tools,
    name=selected_root_agent.props.get("name"),
    display_name=self.cli_config.display_name or None,  # NEW: CLI override, None = use "Assistant"
    context=context,
    ...
)
```

Empty string (`""`) evaluates to falsy, so `or None` converts it to `None`, which means `_display_name = None`, which makes the property return `"Assistant"` as fallback.

### 5. `src/wichy/slash_commands.py`

**Add `/name` handler closure** in `__init__()`:

```python
def handle_name(line: str) -> str | None:
    """Handle /name - set or show the agent display name."""
    parts = line.strip().split(maxsplit=1)
    if len(parts) > 1:
        new_name = parts[1].strip()
        self.root_agent._display_name = new_name
        return f"[green]Display name set to:[/green] {new_name}"
    current = self.root_agent.display_name
    return f"Current display name: {current}"
```

**Add to `_handlers` dict:**
```python
"/name": handle_name,
```

**Add to NestedCompleter dict:**
```python
"/name": None,
```

**Add to `_descriptions` dict:**
```python
"/name": "Set or show the agent display name",
```

## Key Design Notes

- `_name` is the agent type identifier (e.g., `"root-agent-basic"`) — used by `AgentBuilder` for selection/dispatch. It must NOT be changed or repurposed.
- `_display_name` is purely for user-facing display. `None` means "use default 'Assistant'".
- The `display_name` property provides the fallback: `self._display_name or "Assistant"`.
- CLI `--name ""` (default) → `None` → fallback to "Assistant" (unchanged behavior).
- The `/name` slash command allows mid-session changes by directly setting `_display_name`.

## Size

Small — 5 files: root_agent.py, repl.py, cli_parser.py, agent_builder.py, slash_commands.py