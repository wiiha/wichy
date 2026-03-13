# Agent Memory Design for Wichy

**Date:** 2026‑03‑12  
**Status:** Draft  
**Scope:** Long‑term memory for wichy agents using naive conversation logging and selective retrieval.

---

## 1. Problem Statement

Agents in wichy currently have no persistent memory across conversations. We need:

- **Reliability:** Store important facts and context so they survive restarts.
- **Simplicity:** Minimal moving parts; raw conversation chunks are sufficient.
- **Control:** Agent decides when to recall; no automatic injection to avoid context bloat.
- **Extensibility:** Build on existing memory subsystem (`Memory`, `NaiveMemory`, `HybridDocumentStore`).

---

## 2. Existing Infrastructure (Already in Wichy)

- `wichy.memory.core.memory.Memory` – protocol with `add`, `get`, `search`, `count`, `delete`, `get_important`.
- `wichy.memory.core.note.MemoryNote` – Pydantic DTO: `content`, `metadata`, `memory_id`, timestamps, `retrieval_count`, `last_accessed`, `score`, and an `importance` property.
- `wichy.memory.naive.memory.NaiveMemory` – implementation wrapping `HybridDocumentStore`. Adds system metadata (`timestamp`, `retrieval_count`, `last_accessed`). Search uses hybrid (ChromaDB + BM25) with Reciprocal Rank Fusion; updates access stats on every retrieval.
- `HybridDocumentStore` – combines persistent ChromaDB (dense embeddings) and BM25 (sparse keyword). Configurable persistence paths.
- Tool infrastructure: `BaseTool`, `ParametersModel`, registration in `src/wichy/tools/__init__.py`.
- Central Flask server (newly merged) – will host optional future memory tools.

What’s **missing**:

- Agent integration: automatic logging of conversation turns.
- A tool for the agent to perform memory search.
- Wiring of a persistent `NaiveMemory` instance into the agent.

---

## 3. Proposed Design

### 3.1. Write Path – Automatic Turn Logging

Do **not** let the LLM decide. Capture every conversation turn transparently in the agent’s main loop.

**Hook point:** After the final assistant message is produced for a turn.

**Data to capture:**

- User message (raw text).
- Tool calls (names, arguments) – summarized.
- Tool results (one‑line success/failure summary).
- Final assistant message (raw text).
- Metadata: `turn_number`, `conversation_id`, `user_id`, UTC timestamp.

**Chunk format (content string):**

```
User: {user message}
Tools: {comma‑separated list of tool names; optionally include args in compact JSON}
Results: {comma‑separated one‑line summaries, e.g. "BashTool: success", "FetchWebPage: 1024 B"}
Assistant: {final assistant text}
```

Truncate extremely long elements (e.g., tool results > 500 chars) to keep memory chunks modest. The `NaiveMemory` will store the entire content string.

**Implementation:**

```python
from wichy.helpers.memory_formatter import format_turn

class MemoryLogger:
    def __init__(self, memory: Memory):
        self.memory = memory

    def log_turn(self, user_msg: str, tool_calls: list, tool_results: list, assistant_msg: str, metadata: dict):
        content = format_turn(
            user_msg=user_msg,
            tool_calls=tool_calls,
            tool_results=tool_results,
            assistant_msg=assistant_msg,
        )
        self.memory.add(content, metadata=metadata)
```

Hook into `RootAgent` (or equivalent) after each turn.

### 3.2. Read Path – Agent‑Initiated Search Only

Expose a `MemorySearchTool` that the agent can call when it wants to recall.

**Parameters:**

- `query` (string, required) – natural language search query.
- `k` (int, default=5, min=1, max=20) – number of memories to return.

**Execute:**

```python
results = self.memory.search(query, k=k)
return "\n\n".join(note.to_memory_string() for note in results)
```

`MemoryNote.to_memory_string()` gives a compact one‑liner header + content + metadata, suitable for LLM context.

**Tool registration:** Add to `BASIC_TOOLS` or a dedicated `MEMORY_TOOLS` in `src/wichy/tools/__init__.py`.

### 3.3. Storage Persistence

Initialize `NaiveMemory` with a persistent `HybridDocumentStore`:

```python
store = HybridDocumentStore(
    chroma_path=os.path.join(workspace, "memory/agent/chroma"),
    bm25_index_path=os.path.join(workspace, "memory/agent/bm25_index.json"),
    # Optional: tuned thresholds
    min_chroma_score=0.55,
    rrf_k=60,
)
memory = NaiveMemory(store)
```

Create a single global instance at agent startup and share it between the logger and search tool.

### 3.4. Auto‑Injection? – Risk Analysis

**Do not auto‑inject** memories into every turn.

Risks:

- Token budget blowup as memory grows.
- Relevance uncertainty; noise from imperfect search.
- Harder to interpret agent behavior (hidden context).
- Extra plumbing complexity.

Alternative: optional priming for new conversations (inject top N important memories once). That can be added later as an opt‑in flag.

### 3.5. Turn Formatting Helper

Provide a helper function to format a turn into the memory chunk string. This keeps `MemoryLogger` simple and ensures consistency.

**Module:** `wichy.helpers.memory_formatter`

```python
def format_turn(
    user_msg: str,
    tool_calls: list[dict],
    tool_results: list[dict],
    assistant_msg: str,
    max_result_len: int = 500,
) -> str:
    """
    Format a conversation turn into a single memory chunk.

    - Truncates tool results to max_result_len characters (adds "…" if cut).
    - Tool arguments are omitted; only tool names are included.
    - Returns a ready‑to‑store string.
    """
    tools_line = "Tools: " + (", ".join(tc["name"] for tc in tool_calls) if tool_calls else "none")
    if tool_results:
        summaries = []
        for tr in tool_results:
            name = tr.get("name", "UnknownTool")
            result = str(tr.get("result", ""))
            if len(result) > max_result_len:
                result = result[:max_result_len] + "…"
            summaries.append(f"{name}: {result}")
        results_line = "Results: " + "; ".join(summaries)
    else:
        results_line = "Results: none"
    return f"User: {user_msg}\n{tools_line}\n{results_line}\nAssistant: {assistant_msg}"
```

The `MemoryLogger` will call `format_turn(...)` and then `memory.add(content, metadata)`.

### 3.6. Two-Layer Memory (Local + Home)

We introduce two separate `NaiveMemory` instances:

- **Local memory** (project‑scoped): located under the current workspace, e.g. `<workspace>/memory/agent/`. Receives all automatic turn logs. It is ephemeral to the project and can be cleared when the project ends.
- **Home memory** (user‑scoped): located in the user’s wichy config directory, e.g. `~/.wichy/memory/agent/` (or the global OpenClaw workspace). Stores explicit facts, preferences, and lasting knowledge that should persist across projects. Not written automatically.

**Search behavior:** Both memories are searched when the agent calls `memory_search`. Results are merged (by score) and the top k are returned, giving the agent a unified view of project‑specific context and general user knowledge.

**Write policy:**
- Automatic logging → **Local memory only**.
- Explicit additions → **Home memory** via a dedicated `MemoryAddTool` (called when the user asks the agent to remember something permanently).
- Promotion from Local to Home can be manual (copy/paste) or later automated via an importance‑based background job (future).

**Implementation details:**

- Bootstrap two `HybridDocumentStore` instances with distinct persistent paths.
- Create a `CombinedMemory` wrapper that implements the `Memory` protocol:
  - `add(content, metadata)` → forwards to `local_memory`.
  - `search(query, k, filter_metadata)` → query both stores, normalize scores if needed (they’re comparable), deduplicate by `memory_id` (unlikely overlap), sort descending by score, return top k.
  - `get_important(k, filter_metadata)` → similar merge of both stores’ important lists.
  - `get(memory_id)` → try `local_memory`, then `home_memory`.
  - `delete(memory_id)` → attempt on both (or at least local; home deletions manual via another tool).
- `MemoryLogger` receives `local_memory` directly.
- `MemorySearchTool` receives `combined_memory`.
- New `MemoryAddTool`: parameters `content` (string) and optional `metadata` (json); executes `home_memory.add(content, metadata)`.

This design keeps automatic logging lightweight and project‑isolated while allowing the user to build a cross‑project knowledge base deliberately.

---

## 4. Implementation Steps

1. **Memory configuration & bootstrap** – In agent startup, create two `NaiveMemory` instances:
   - Local: with persistent paths `<workspace>/memory/agent/chroma` and `<workspace>/memory/agent/bm25_index.json`.
   - Home: with paths `~/.wichy/memory/agent/chroma` and `~/.wichy/memory/agent/bm25_index.json` (ensure directory exists).
   - Build `CombinedMemory(local, home)` wrapper for search/reads.
2. **Add `MemoryLogger`** – Implement the `log_turn` method (using `format_turn`) and integrate into the agent’s post‑turn hook. Pass `local_memory` to the logger.
3. **Create `CombinedMemory`** – Implement the `Memory` protocol:
   - `add` → `local_memory.add`
   - `search` → query both, merge by score, deduplicate IDs, return top k.
   - `get_important` → merge both stores’ important lists, sort, return top k.
   - `get` → try local, then home.
   - `delete` → attempt on both (or local only).
4. **Create `MemorySearchTool`**:
   - Parameters model: `query`, `k`.
   - Execute: call `combined_memory.search(query, k=k)` and format each note via `note.to_memory_string()`.
5. **Create `MemoryAddTool`** (explicit home writes):
   - Parameters: `content` (string), `metadata` (optional dict).
   - Execute: `home_memory.add(content, metadata)`.
6. **Register tools** – Add `MemorySearchTool` and `MemoryAddTool` to appropriate tool lists (e.g., `BASIC_TOOLS`) in `src/wichy/tools/__init__.py`.
7. **Schema migrations** – None required; stores create themselves on first use.
8. **Testing**:
   - Have a short conversation; verify `local_memory.count() > 0`.
   - Call `memory_search` with a query that should match local content; also add a home memory manually and verify it appears in results.
   - Verify persistence: restart agent, ensure both stores retain data.
   - Call `memory_add` to add to home; ensure it appears in subsequent searches.
9. **Documentation** – Update README with:
   - Two-layer memory concept.
   - Storage locations.
   - Automatic vs explicit writes.
   - How to use `memory_search` and `memory_add`.

---

## 5. Safety & Maintenance

- **Growth:** `HybridDocumentStore` handles thousands of docs; if needed, later implement a retention policy (e.g., prune memories older than 90 days) or use `get_important` to curate.
- **Importance:** `MemoryNote.importance` is computed but not used for retrieval. Could later bias search or inform pruning.
- **Thread safety:** Currently single‑threaded; if multiple agent threads appear, protect memory access with a lock.
- **Human verification:** Not required for memory writes (automatic) or reads (search tool). Keep as is.
- **Privacy:** Memory files are stored under the workspace and home config; user is trusted.

---

## 6. Configuration & Tuning Hooks (optional)

- Expose workspace‑relative memory directory via env var `WICHY_MEMORY_DIR` (default `memory/agent`).
- Allow tuning of `HybridDocumentStore` parameters via config file or flags (e.g., `min_chroma_score`, `rrf_k`).
- Max `k` for search tool could be capped at 20 to prevent over‑fetch.

---

## 7. Future Enhancements

- **ZettelkastenMemory:** for summarization and linking once stable.
- **Opt‑out per‑turn:** allow user to mark a turn as “don’t remember” (skip logging).
- **Manual add tool:** Already covered by `MemoryAddTool`.
- **Bulk operations:** `MemoryListTool`, `MemoryDeleteTool` for admin.
- **Retrieval weighting:** Boost important memories in search (hybrid score + importance).
- **Automatic promotion:** Background job to copy high‑importance local memories to home (configurable threshold).

---

## 8. Decision Summary

- Naive raw turn chunks are sufficient for v1.
- Agent does **not** auto‑inject memories; it must call `memory_search` explicitly.
- Use existing `NaiveMemory` + `HybridDocumentStore` with disk persistence.
- Two‑layer design: local (auto writes) + home (explicit writes). Combined for search.
- Implement logger hook and search tool; minimal code, maximum clarity.
