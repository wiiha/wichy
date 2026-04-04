# Lifecycle Hooks: Result Handling Considerations

_Date: 2026-04-04_

## Current State

Lifecycle hooks execute but their results are ignored. This document captures potential future directions.

---

## Tool Hooks (for comparison)

| Result          | PRE_TOOL meaning      | POST_TOOL meaning     |
| --------------- | --------------------- | --------------------- |
| `APPROVE`       | Continue execution    | Continue (no-op)      |
| `DENY`          | Block the tool call   | Block (return error)  |
| `MODIFY_INPUT`  | Change tool arguments | N/A                   |
| `MODIFY_OUTPUT` | N/A                   | Change tool output    |
| `LOG`           | Record data, continue | Record data, continue |

---

## Potential Result Meanings for Lifecycle Hooks

| Hook                   | DENY                 | MODIFY_INPUT           | MODIFY_OUTPUT       |
| ---------------------- | -------------------- | ---------------------- | ------------------- |
| `session_start`        | Block session start? | Change startup params? | N/A                 |
| `session_end`          | Block exit?          | N/A                    | N/A                 |
| `context_reset_pre`    | Block reset?         | Change reset strategy? | N/A                 |
| `context_reset_post`   | N/A                  | N/A                    | Modify new context? |
| `context_compact_pre`  | Block compaction?    | Change guidelines?     | N/A                 |
| `context_compact_post` | N/A                  | N/A                    | Modify summary?     |

---

## Potential Use Cases

### 1. Auto-compact DENY

`context_compact_pre` could return `DENY` to skip auto-compact:

- "Skip auto-compact this time, I'm in the middle of something"
- Only makes sense for auto-compact, not manual `/compact`
- Manual compaction should probably not be blockable

### 2. Compaction guideline injection

`context_compact_pre` could return `MODIFY_INPUT` to add guidelines:

- Hook injects: "Make sure to preserve the API key we discovered"
- Influences what the LLM summarizes
- Could be useful for preserving critical context

### 3. Reset strategy modification

`context_reset_pre` could return `MODIFY_INPUT` to change strategy:

- Force SUMMARY instead of NUKE for certain conditions
- E.g., "Always summarize before reset for this project"

---

## Decision (2026-04-04)

**Ignore results for now.** Add handling when there's a concrete use case.

Most lifecycle hooks are informational/observational - the event is happening regardless. The most likely candidate for future result handling is:

- `context_compact_pre` returning `DENY` to block auto-compact

---

## Implementation Note

If we add result handling later, it would go in:

- `src/wichy/hooks/executor.py` - `run_context_hooks()` method
- The caller would need to check the result and act accordingly

Separate logic would be needed for:

- Auto-compact (DENY could block)
- Manual compact (DENY probably shouldn't block)
