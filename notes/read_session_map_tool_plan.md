# Implementation Plan: `read_session_map` Tool

**Date:** 2026-04-06
**Status:** Ready for Implementation

## Overview

Create a new `read_session_map` tool that allows the LLM agent to read the session map (investigation progress tracker) using a clean `BaseTool` interface with explicit parameters.

## Requirements

1. **Clean interface** - Follow `ReadGraphTool` pattern with explicit Pydantic parameters
2. **NO hidden parameters** - Do NOT use `HIDE_FROM_LLM_PREFIX` pattern
3. **Global state access** - Use module-level globals set by root agent (same pattern as `session_map.api`)
4. **Explicit parameters:**
   - `node_types`: `list[str] | None` - Filter to specific node types, default `None` (all types)
   - `detail`: `str` - `"quick"` for summary counts, `"full"` for complete content with edges
   - `limit`: `int` - Max nodes to return, default `100`

---

## Files to Create/Modify

### 1. NEW: `src/wichy/tools/session_map_tools.py`

Main implementation file. Contains:

```python
"""
Session map tools for reading investigation progress.
"""

from typing import Literal, Optional
from pydantic import Field

from wichy.session_map.store import SessionMapStore
from wichy.session_map.models import NodeType, Node, Edge, SessionMap
from wichy.tools.base import BaseTool, ParametersModel
from wichy.tools.errors import format_error
from wichy.config import settings


# --- Global State Access (set by root_agent) ---
_session_map_store: SessionMapStore | None = None
_context_handler = None


def set_session_map_globals(store: SessionMapStore | None, context_handler) -> None:
    """Set global references for session map tools.
    
    Called by root_agent when initializing session map feature.
    """
    global _session_map_store, _context_handler
    _session_map_store = store
    _context_handler = context_handler


# --- Parameters Model ---
class ReadSessionMapParameters(ParametersModel):
    """Parameters for read_session_map tool."""
    
    node_types: Optional[list[str]] = Field(
        None,
        description="Filter to specific node types. Valid types: 'question', 'finding', 'decision', 'file', 'dead_end', 'note'. If not provided, returns all types.",
    )
    detail: Literal["quick", "full"] = Field(
        "quick",
        description="Output detail level. 'quick' returns summary counts. 'full' returns complete node content and edges.",
    )
    limit: int = Field(
        100,
        description="Maximum number of nodes to return. Default 100. Use to control output size.",
        ge=1,
        le=500,
    )

    def info(self) -> str:
        parts = []
        if self.node_types:
            parts.append(f"types={self.node_types}")
        parts.append(f"detail={self.detail}")
        parts.append(f"limit={self.limit}")
        return " ".join(parts)


# --- Tool Implementation ---
class ReadSessionMapTool(BaseTool):
    """Tool to read the current session map showing investigation progress."""
    
    name = "read_session_map"
    description = "Read the current session map showing investigation progress, questions, findings, and decisions"
    description_long = """Read the session map to see the investigation progress.

A session map tracks:
- Questions asked during the investigation
- Findings and discoveries
- Decisions made
- Files explored
- Dead ends encountered

Use this to:
1. Review what has been discovered so far
2. See relationships between findings
3. Understand the investigation timeline
4. Check if a topic has already been explored

Parameters:
- node_types: Filter to specific types ('question', 'finding', 'decision', 'file', 'dead_end', 'note')
- detail: 'quick' for summary with counts, 'full' for complete content with edges
- limit: Max nodes to return (default 100)"""
    
    parameters_model = ReadSessionMapParameters
    
    # Valid node types for validation
    VALID_NODE_TYPES = {"question", "finding", "decision", "file", "dead_end", "note"}
    
    def execute(
        self,
        node_types: Optional[list[str]] = None,
        detail: str = "quick",
        limit: int = 100,
    ) -> str:
        # Implementation... (see full code below)
    
    def _format_quick(self, session_map, nodes, limited, limit) -> str:
        # Format summary output
        
    def _format_full(self, session_map, nodes, limited, limit) -> str:
        # Format detailed output with edges
```

### 2. MODIFY: `src/wichy/tools/__init__.py`

Add imports for new tools:

```python
# Add to imports section
from wichy.tools.session_map_tools import (
    ReadSessionMapTool,
)

# Add to __all__ list
__all__ = [
    # ... existing tools ...
    "ReadSessionMapTool",
]
```

### 3. MODIFY: `src/wichy/root_agent/root_agent.py`

Add call to sync globals after setting context handler. In `_maybe_extract_session_map()` method:

```python
def _maybe_extract_session_map(self):
    if self._session_map_model is None:
        return
    
    self._init_session_map()
    
    from wichy.session_map.api import set_context_handler
    set_context_handler(self.context)
    
    # NEW: Also update session_map_tools globals
    from wichy.tools.session_map_tools import set_session_map_globals
    set_session_map_globals(self._session_map_store, self.context)
    
    # ... rest of existing code ...
```

---

## Output Formats

### Quick Mode (default)

```
# Session Map Summary

Total nodes: 40, Total edges: 28
Last extracted turn: 42

## Node Counts by Type
  - question: 8
  - finding: 12
  - decision: 3
  - file: 15
  - dead_end: 2
  - note: 0

## Nodes (filtered, showing 25 of 40)
- [question] [abc123] What is the authentication flow?
- [finding] [def456] Auth uses JWT tokens with 24h expiry
- [decision] [ghi789] Use Redis for session storage
...

---
(Limited to 100 nodes. Use higher limit or filter by node_types to see more.)
```

### Full Mode

```
# Session Map (Full Details)

Context: /workspace/.wichy/contexts/2026-04-06_1234567890.jsonl
Total nodes: 40, Total edges: 28
Last extracted turn: 42

## Nodes (3)

### [abc123] question
Turn: 5
Content: What is the authentication flow?
Connects to: [def456]

### [def456] finding
Turn: 6
Content: Auth uses JWT tokens with 24h expiry. Tokens are validated on each request.
Source message index: 12
Connects to: [ghi789]

### [ghi789] decision
Turn: 10
Content: Use Redis for session storage
Alternatives considered: Memcached, in-memory

## Edges

- What is the authentication flow?
  --[answers]-->
  Auth uses JWT tokens with 24h expiry...

- Auth uses JWT tokens...
  --[led_to]-->
  Use Redis for session storage

---
(Showing 3 nodes. Use limit parameter to see more.)
```

---

## Error Handling

| Scenario | Response |
|----------|----------|
| Session map feature disabled | `"error: Session map not available. The session map feature is not enabled. Use --session-map flag when starting wichy."` |
| No context handler set | `"error: Session map not initialized."` |
| Empty session map (no nodes) | `"# Session Map\n\nNo session map data available yet. The map will be populated automatically as the investigation progresses."` |
| Invalid node_types | `"error: Invalid node types: {invalid}. Valid types are: question, finding, decision, file, dead_end, note"` |
| Limit exceeded | Truncate output + show `"(Showing X of Y nodes. Use limit parameter to see more.)"` |
| Exception during execution | `"error: Failed to read session map: {ExceptionName}: {message}"` |

---

## Implementation Details

### Context Resolution

The tool accesses the current session map via:

```python
context_id = str(_context_handler.path)
session_map = _session_map_store.get(context_id)
```

This matches the pattern used in `session_map.api` routes.

### Node Type Validation

```python
VALID_NODE_TYPES = {"question", "finding", "decision", "file", "dead_end", "note"}

if node_types:
    node_types_lower = [t.lower() for t in node_types]
    invalid = set(node_types_lower) - self.VALID_NODE_TYPES
    if invalid:
        return format_error(f"Invalid node types: {invalid}. Valid types are: {self.VALID_NODE_TYPES}")
```

### Edge Resolution (Full Mode)

Build a node ID to node lookup for resolving edges:

```python
node_map = {n.id: n for n in session_map.nodes}
filtered_ids = {n.id for n in nodes}

# Only show edges between filtered nodes
relevant_edges = [
    e for e in session_map.edges
    if e.from_id in filtered_ids or e.to_id in filtered_ids
]
```

---

## Pattern Comparison: Hidden vs Clean

### ❌ AVOID: Hidden Parameter Pattern

```python
# DON'T DO THIS
class ReadSessionMapParameters(ParametersModel):
    context_id: str = Field(
        default="",
        description=HIDE_FROM_LLM_PREFIX + " Context ID from agent",
    )
```

Problems:
- Requires `inject_*` flags in agent/core code
- Creates coupling between tool and agent implementation
- Not visible to LLM in tool schema

### ✅ USE: Clean Pattern (Global State Access)

```python
# DO THIS
_session_map_store: SessionMapStore | None = None
_context_handler = None

def set_session_map_globals(store, context_handler):
    global _session_map_store, _context_handler
    _session_map_store = store
    _context_handler = context_handler

class ReadSessionMapTool(BaseTool):
    def execute(self, node_types, detail, limit) -> str:
        context_id = str(_context_handler.path)
        session_map = _session_map_store.get(context_id)
```

This matches:
- `ReadGraphTool`'s use of `settings` singleton
- `session_map.api`'s use of `_context_handler` global

---

## Testing Checklist

Tests should cover:

- [ ] Tool appears in registry after import
- [ ] Parameters validation: invalid node_types rejected
- [ ] Parameters validation: limit bounds respected (1-500)
- [ ] Quick mode returns summary format correctly
- [ ] Full mode returns detailed format with edges
- [ ] node_types filter works correctly (each type)
- [ ] Limit parameter truncates correctly
- [ ] Empty map returns appropriate message
- [ ] Disabled feature returns error
- [ ] No context handler returns error
- [ ] `set_session_map_globals()` properly updates globals

---

## Integration Order

1. Create `src/wichy/tools/session_map_tools.py`
2. Modify `src/wichy/tools/__init__.py` - add imports
3. Modify `src/wichy/root_agent/root_agent.py` - add globals sync call
4. Create `tests/test_session_map_tools.py`
5. Run tests and verify tool appears in tool list

---

## Optional Future Enhancement: `clear_session_map`

A companion tool for clearing the session map:

```python
class ClearSessionMapParameters(ParametersModel):
    confirm: bool = Field(
        False,
        description="Must be set to true to confirm clearing the session map.",
    )

class ClearSessionMapTool(BaseTool):
    name = "clear_session_map"
    description = "Clear all nodes and edges from the current session map"
    # ...
```

This could be added in the same file if needed.

---

## Session Map Behavior Notes

### Context Changes (Reset/Compaction)

When context is reset or compacted, a **new context** with a **new path** is created. The session map uses `context_id` (derived from `context.path`) as the primary key in SQLite.

**Result:** Session map data becomes orphaned after reset/compaction:
- Old session map persists in DB under old `context_id`
- New empty session map starts for new context
- No migration/cleanup mechanism exists

This is **acceptable behavior** - the tool should simply read the current context's session map.

### Auto-Registration

Tools auto-register via `ToolMeta` metaclass in `src/wichy/tools/registry.py`. No manual registration needed - just importing the tool class triggers registration.

---

## References

- `src/wichy/tools/graph_tools.py` - Reference implementation for `ReadGraphTool`
- `src/wichy/tools/base.py` - `BaseTool` and `ParametersModel` base classes
- `src/wichy/session_map/store.py` - `SessionMapStore` class for data access
- `src/wichy/session_map/models.py` - `Node`, `Edge`, `SessionMap` data models
- `src/wichy/session_map/api.py` - Global state pattern (`_context_handler`, `set_context_handler`)
- `src/wichy/tools/errors.py` - `format_error()` helper