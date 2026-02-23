# Wichy Memory System Design

## Overview

This document outlines the design for implementing an agent learning/evolution system in Wichy, inspired by OpenClaw's memory architecture but adapted to Wichy's per-project philosophy.

## 1. Background & Analysis

### OpenClaw's Learning System

**Architecture:**
- Single global workspace with persistent memory files
- Daily logs (`memory/YYYY-MM-DD.md`) and curated long-term memory (`MEMORY.md`)
- Full conversation transcripts stored as JSONL
- Vector + BM25 hybrid search using SQLite (`sqlite-vec`)
- Temporal decay to prevent stale information dominance
- No traditional ML - learning via explicit knowledge transfer

**Key Flows:**
1. Inbound message → load transcript → build context → LLM inference → tool execution → persist
2. Memory search: query → embed → vector+BM25 hybrid → MMR re-ranking → temporal decay
3. Compaction: summarize old history, flush to memory before context window limits hit

### Wichy's Per-Project Architecture

**Structure:**
```
src/wichy/
├── __main__.py          # Main CLI loop
├── root_agent/          # Root agent + descriptions
├── tools/               # 20+ tool implementations
├── helpers/             # Utilities (context, console, file)
├── llm_backend.py       # LLM abstraction (Ollama, Llama.cpp, OpenRouter)
└── slash_commands.py    # Slash command handling
```

**Per-Project Storage:**
- `<project>/.wichy/contexts/` - Conversation logs (JSON, date-prefixed)
- `<project>/.wichy/root_agent_defs/` - Custom agent configs (markdown with frontmatter)
- `~/.wichy_history` - Global prompt history

**Agent System:**
- RootAgent with tools + sub-agent spawning via `TaskAgentTool`
- Configuration via markdown files (name, description, model, tools)
- Context management with slash commands (`/context reset`, `/context reset_by_summary`)

**Key Difference:** OpenClaw has single workspace; Wichy creates `.wichy/` in each project for isolation.

---

## 2. Proposed Architecture: Hybrid Global + Per-Project Memory

### Why Hybrid?

1. **Preserves per-project isolation** - Project-specific details stay in `.wichy/`
2. **Enables cross-project learning** - General patterns, common errors, user preferences learned globally
3. **Flexible** - Projects can opt-out of global memory or configure weights
4. **Privacy-conscious** - Sensitive data isolated by default, global memory only contains learnings deemed shareable
5. **Scalable** - Local indexes remain fast; global aggregates insights

### Storage Layout

```
Global Memory (user-level):
~/.wichy/memory/
├── global_store/           # Project-agnostic knowledge
│   ├── index_sqlite/      # Combined vector + BM25 index (memories.db)
│   └── memories/          # Raw memory files (markdown, timestamped)
├── project_index/         # Project name → memory index mapping (JSON)
└── config.yaml           # Global memory configuration

Per-Project Memory (project-level):
<project>/.wichy/memory/
├── local_store/           # Project-specific knowledge
│   ├── index_sqlite/     # Project-only vector + BM25 index (memories.db)
│   └── memories/         # Raw memory files (markdown, timestamped)
└── config.yaml           # Project memory configuration (may inherit from global)
```

---

## 3. Storage Format: Markdown + SQLite

### Memory Entry (Markdown File)

Filename: `YYYY-MM-DD-HHMMSS-NNN.md` (timestamped with sequence number)

```markdown
---
id: <uuid-or-timestamp>
created: 2026-02-23T14:30:00Z
updated: 2026-02-23T14:30:00Z
tags: [project:myproject, tool:bash, category:setup]
importance: 0.85  # 0-1 score based on usage, references, recency
source: tool_call  # or: user_input, agent_reflection, sub_agent_output
project: myproject  # or "global" for cross-project memories
retrieval_count: 12  # How many times referenced/used
last_retrieved: 2026-02-23T15:00:00Z
---

# Summary
Brief 1-2 sentence summary of the memory for quick scanning

# Content
Full detailed content that was learned or observed. Can be multi-paragraph,
include code snippets, error messages, user preferences, etc.

# Context (optional)
- Related tasks: task IDs or descriptions
- Referenced files: file paths involved
- User intent: what the user wanted to achieve
- Related memories: IDs of linked memories
```

### SQLite Schema (`index_sqlite/memories.db`)

```sql
-- Main memories table
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    project TEXT NOT NULL,
    tags TEXT,  -- JSON array stringified
    importance REAL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    source TEXT,
    summary TEXT,
    content TEXT,  -- cached for search; can be regenerated from markdown
    embedding BLOB,  -- vector embedding as float32 array (sqlite-vec)
    retrieval_count INTEGER DEFAULT 0,
    last_retrieved TIMESTAMP
);

-- BM25 full-text search index (FTS5)
CREATE VIRTUAL TABLE memories_fts USING fts5(
    summary, content, tags,
    content=memories, content_rowid=rowid
);

-- Usage tracking for importance decay
CREATE TABLE memory_usage (
    memory_id TEXT,
    retrieved_at TIMESTAMP,
    context_window_included BOOLEAN,
    PRIMARY KEY (memory_id, retrieved_at)
);

-- Indexes for fast filtering
CREATE INDEX idx_memories_project ON memories(project);
CREATE INDEX idx_memories_importance ON memories(importance DESC);
CREATE INDEX idx_memories_updated ON memories(updated_at DESC);
```

### Indexing Strategy

1. **Initial Index**: On memory creation, compute embedding using local model (e.g., `nomic-embed-text` via Ollama or `sentence-transformers`)
2. **Hybrid Search**: Query both BM25 (keyword match) and vector (semantic similarity)
3. **Result Fusion**: Reciprocal Rank Fusion (RRF) combines results:
   ```
   RRF_score = 1/(k1 + rank_bm25) + 1/(k2 + rank_vector)
   ```
4. **Automatic Re-index**: Watcher on markdown directory triggers SQLite update (debounced 1.5s)
5. **Temporal Decay**: Optional boost for recent memories (configurable half-life, default 30 days)

---

## 4. Tool Definitions

### 4.1 `memory_remember`

Store important information for future retrieval.

**Parameters (Pydantic model):**

```python
class MemoryRememberParameters(ParametersModel):
    content: str = Field(
        ...,
        description="The content to remember. Can be observations, learnings, user preferences, code patterns, errors, etc."
    )
    summary: str = Field(
        ...,
        description="Brief 1-2 sentence summary for quick scanning"
    )
    tags: Optional[List[str]] = Field(
        [],
        description="Tags for categorization, e.g., ['python', 'setup', 'error:connection']"
    )
    importance: Optional[float] = Field(
        0.5,
        ge=0.0,
        le=1.0,
        description="Importance score 0-1. Default 0.5. Higher = more likely to be retrieved"
    )
    project: Optional[str] = Field(
        None,
        description="Project name (auto-detected if omitted). Use 'global' for cross-project"
    )
    source: str = Field(
        "agent_initiated",
        description="Source: agent_initiated, user_input, tool_output, sub_agent_output"
    )
    related_tasks: Optional[List[str]] = Field(
        [],
        description="Task IDs or descriptions related to this memory"
    )
    referenced_files: Optional[List[str]] = Field(
        [],
        description="File paths mentioned or involved"
    )
    link_to: Optional[List[str]] = Field(
        [],
        description="Memory IDs to link this memory to (creates bidirectional relationship)"
    )
```

**Behavior:**
1. Auto-detect current project from `.wichy/` location or git remote
2. Create timestamped markdown file in appropriate store (global if `project="global"` or `importance > 0.8` and `use_global=true`)
3. Generate embedding via configured embedding model
4. Insert into SQLite (both main table and FTS)
5. If `link_to` provided, update linked memories with reciprocal reference
6. Return `{ "memory_id": "...", "file_path": "...", "project": "..." }`

**Auto-detection of project:**
- Look for `.wichy/` in current or parent directories
- If found, read `<project>/.wichy/root_agent_defs/` or use directory name
- Fallback to `default` if not in a project

---

### 4.2 `memory_search`

Semantic + keyword search through memories with filters.

**Parameters:**

```python
class MemorySearchParameters(ParametersModel):
    query: str = Field(
        ...,
        description="Search query in natural language"
    )
    memory_type: Optional[str] = Field(
        "all",
        description="Filter by source: 'all', 'agent_initiated', 'user_input', 'tool_output', 'sub_agent_output'"
    )
    project_filter: Optional[str] = Field(
        "current",
        description="Which project: 'current', 'global', 'all', or specific project name"
    )
    min_importance: Optional[float] = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Only return memories with importance >= this value"
    )
    tags: Optional[List[str]] = Field(
        [],
        description="Filter by tags (any match, OR logic)"
    )
    max_results: Optional[int] = Field(
        10,
        gt=0,
        le=100,
        description="Maximum number of results"
    )
    include_content: Optional[bool] = Field(
        False,
        description="Include full memory content in results (otherwise just summary + metadata)"
    )
    hybrid_weight: Optional[float] = Field(
        0.7,
        ge=0.0,
        le=1.0,
        description="Weight for semantic search vs BM25 (0=pure BM25, 1=pure vector)"
    )
    use_global: Optional[bool] = Field(
        None,
        description="Include global memories in search (defaults to project config)"
    )
    temporal_boost: Optional[bool] = Field(
        True,
        description="Apply temporal decay (recent memories ranked higher)"
    )
```

**Behavior:**
1. Determine which indexes to search based on `project_filter` and `use_global`:
   - `current`: search project local + optionally global (based on project config)
   - `global`: search global only
   - `all`: search both local and global (with deduplication)
   - specific name: search that project's local only
2. Generate embedding for query (if hybrid_weight > 0)
3. Query BM25 index (FTS5) → get ranked list A
4. Query vector index (sqlite-vec) → get ranked list B
5. Apply Reciprocal Rank Fusion:
   ```
   RRF_score(i) = 1/(k + rank_A(i)) + 1/(k + rank_B(i))
   where k=60 (typical)
   ```
6. Apply filters (tags, importance, memory_type)
7. Apply temporal boost if enabled:
   ```
   adjusted_score = RRF_score * (1 + decay_factor * days_ago)
   where decay_factor = ln(2) / half_life_days
   ```
8. Limit to `max_results`, sort by adjusted score
9. For each result, fetch full memory markdown (if `include_content=true`) or just metadata
10. Increment `retrieval_count` and update `last_retrieved` for each memory
11. Return list of memory dicts:
    ```json
    [
      {
        "id": "mem_123",
        "summary": "...",
        "project": "myproject",
        "tags": ["python", "debug"],
        "importance": 0.85,
        "created": "2026-02-23T14:30:00Z",
        "retrieval_count": 12,
        "content": "..."  // only if include_content=true
      }
    ]
    ```

---

### 4.3 `memory_forget`

Remove memories that are outdated, incorrect, or no longer needed.

**Parameters:**

```python
class MemoryForgetParameters(ParametersModel):
    memory_id: Optional[str] = Field(
        None,
        description="Specific memory ID to delete"
    )
    query: Optional[str] = Field(
        None,
        description="Search query to find memories to delete (filters by content match)"
    )
    project: Optional[str] = Field(
        "current",
        description="Project scope: 'current', 'global', 'all'"
    )
    older_than_days: Optional[int] = Field(
        None,
        description="Delete memories older than this many days"
    )
    tag: Optional[str] = Field(
        None,
        description="Delete all memories with this exact tag"
    )
    min_importance: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Delete memories with importance below this threshold"
    )
    dry_run: Optional[bool] = Field(
        False,
        description="If true, only show what would be deleted without actually deleting"
    )
    confirm: Optional[bool] = Field(
        False,
        description="Required for actual deletion when using query-based deletion (safety)"
    )
```

**Behavior:**
1. **Safety check**: Require at least one filter criterion (memory_id, query, older_than_days, tag, or min_importance). If none provided, return error.
2. Determine target project(s) based on `project` param
3. Build SQL query with WHERE clauses for each provided filter:
   - `memory_id = ?` (exact match)
   - Full-text search on `query` against `memories_fts`
   - `created_at < (now - older_than_days)`
   - `tags LIKE ?` (JSON array contains tag)
   - `importance < min_importance`
4. If `dry_run=true`: count matching memories and return preview (metadata only)
5. If `dry_run=false` and `confirm=true`: perform deletion
   - Soft delete first: update markdown files to add `deleted: true` in frontmatter, move to `<store>/deleted/` archive
   - Remove from SQLite indexes (main + FTS)
   - Keep a deletion log entry in `~/.wichy/memory/deletion_log.jsonl`
6. Return `{ "deleted_count": N, "preview": [...] }`

**Safety Enhancements:**
- Never delete memories with `importance > 0.9` without explicit `memory_id` and `confirm=true`
- Require `confirm=true` for query-based deletion (user must explicitly acknowledge)
- Keep 30-day backup of deleted files before permanent purge

---

## 5. Integration with Existing Wichy

### 5.1 Tool Registration

Add to `src/wichy/tools/__init__.py`:

```python
# Existing imports...
from wichy.tools.memory import (
    MemoryRememberTool,
    MemorySearchTool,
    MemoryForgetTool,
)

# Add to FILE_SYSTEM_TOOLS or appropriate category
ALL_TOOLS_NOT_INSTANTIATED = [
    # ... existing tools ...
    MemoryRememberTool,
    MemorySearchTool,
    MemoryForgetTool,
]
```

Create new file `src/wichy/tools/memory.py` containing the three tool implementations.

### 5.2 Dependency Management

Update `pyproject.toml`:

```toml
dependencies = [
    # ... existing dependencies ...
    "sqlite-vec>=0.1.1",           # Vector extension for SQLite
    "sentence-transformers>=3.0.0", # Embedding models
    "numpy>=1.24.0",               # Vector operations
    "scikit-learn>=1.3.0",         # For RRF and potentially relevance scoring
]
```

Note: `sqlite-vec` works with `sqlite3` CLI extension loading. May need system SQLite 3.38+.

### 5.3 Configuration Discovery

#### Global Config: `~/.wichy/config.yaml`

```yaml
memory:
  enabled: true
  global_store: ~/.wichy/memory/global_store
  embedding_model: nomic-embed-text  # Options: nomic-embed-text, all-MiniLM-L6-v2
  embedding_source: ollama          # Options: ollama, sentence_transformers, openai
  ollama_base_url: http://localhost:11434
  auto_remember:
    errors: true                    # Auto-remember errors from tool outputs
    successful_tasks: false        # Auto-remember successful completions
    user_preferences: true         # Detect and remember user preferences
    learned_patterns: true         # Patterns discovered during problem-solving
  importance_calculation:
    retrieval_boost: 0.1            # +0.1 per retrieval (up to max)
    use_count_weight: 0.2          # How much retrieval count matters
    recency_weight: 0.3            # Recent memories get small boost
  temporal_decay:
    enabled: true
    half_life_days: 30
  retention:
    min_importance_threshold: 0.05
    max_age_days: 365
    auto_compaction: true          # Periodic summarization of old low-importance memories
  search:
    default_hybrid_weight: 0.7     # 0=BW25 only, 1=vector only
    max_results: 10
    context_injection: 3           # Memories to auto-inject before each turn
    mmr_diversity: 0.3             # Diversity penalty for MMR reranking (0-1)
  project_defaults:
    use_global: true               # Projects include global memories by default
    auto_remember_errors: true
    auto_remember_successes: false
    importance_boost_local: 0.2    # Local memories get +0.2 importance boost
```

#### Project Config: `<project>/.wichy/memory/config.yaml`

```yaml
memory:
  enabled: true                      # Enable memory for this project (false = disable)
  use_global: true                   # Include global memories in searches
  write_to_global: false             # Allow agent to write memories to global store
  auto_remember:
    errors: true                     # Override global config
    successful_tasks: true
  importance_boost_local: 0.2        # Boost local memories over global ones
  tags:
    exclude_from_global: ["sensitive", "confidential", "password", "api_key"]
    always_include: ["project_specific"]
  context:
    injection_count: 3               # Override global context_injection
    injection_mode: "balanced"      # balanced, local_first, global_first
```

### 5.4 Agent Context Enrichment

Modify `RootAgent` to enrich context with relevant memories before each user turn.

**Modify `src/wichy/root_agent/root_agent.py`:**

```python
class RootAgent:
    def __init__(self, model_str, tools, name="NOT SET", memory_enabled=True):
        # ... existing init ...
        self.memory_enabled = memory_enabled
        self.project_name = self._detect_project_name()
        self.memory_config = self._load_memory_config()
        
    def _detect_project_name(self):
        """Walk up from current dir to find .wichy/ or .git/"""
        cwd = Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            if (parent / ".wichy").exists():
                return parent.name
            if (parent / ".git").exists():
                return parent.name
        return "default"
    
    def _load_memory_config(self):
        """Load project-specific or fallback to global config"""
        project_config_path = Path.cwd() / ".wichy" / "memory" / "config.yaml"
        if project_config_path.exists():
            with open(project_config_path) as f:
                return yaml.safe_load(f)
        # Fallback to global
        global_config_path = Path.home() / ".wichy" / "config.yaml"
        if global_config_path.exists():
            with open(global_config_path) as f:
                global_config = yaml.safe_load(f)
                return global_config.get("project_defaults", {})
        return {}
    
    def enrich_context_with_memories(self, query: str, max_memories: int = None):
        """Inject relevant memories into context before processing"""
        if not self.memory_enabled:
            return
            
        if max_memories is None:
            max_memories = self.memory_config.get("context", {}).get("injection_count", 3)
            
        # Use memory_search tool
        search_params = {
            "query": query,
            "project_filter": "current",
            "max_results": max_memories * 2,  # Get extra for diversity filtering
            "include_content": True,
            "use_global": self.memory_config.get("use_global", True)
        }
        
        # Execute search (tool call or direct method)
        results = memory_search_tool.execute(**search_params)
        
        # Apply MMR diversity if multiple memories
        if len(results) > max_memories:
            results = self._apply_mmr_diversify(results, max_memories)
        
        if results:
            memories_text = "\n\n".join([
                f"Memory [{i+1}] (from {r['project']}, importance={r['importance']:.2f}):\n{r['content']}"
                for i, r in enumerate(results[:max_memories])
            ])
            self.context.append(
                role="system",
                content=f"Relevant past memories that may help:\n\n{memories_text}"
            )
    
    def _apply_mmr_diversify(self, memories, target_count, diversity_lambda=0.3):
        """Maximal Marginal Relevance to increase diversity"""
        if not memories:
            return memories
            
        selected = [memories[0]]
        remaining = memories[1:]
        
        while len(selected) < target_count and remaining:
            best_score = -1
            best_idx = 0
            
            for i, candidate in enumerate(remaining):
                # Relevance score (normalized importance)
                rel_score = candidate["importance"]
                
                # Diversity: 1 - max similarity to already selected
                max_sim = max([
                    self._memory_similarity(candidate, sel)
                    for sel in selected
                ])
                
                mmr_score = (1 - diversity_lambda) * rel_score + diversity_lambda * (1 - max_sim)
                
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = i
            
            selected.append(remaining.pop(best_idx))
        
        return selected
    
    def _memory_similarity(self, mem1, mem2):
        """Simple similarity based on tag overlap and text (placeholder)"""
        # In full impl, could use cached embeddings
        tags1 = set(mem1.get("tags", []))
        tags2 = set(mem2.get("tags", []))
        if not tags1 or not tags2:
            return 0.0
        return len(tags1 & tags2) / len(tags1 | tags2)
```

**Modify main loop in `src/wichy/__main__.py`:**

```python
# Before calling root_agent.append(user_message):
root_agent.enrich_context_with_memories(user_message)
response = root_agent.append(user_message)
```

### 5.5 Automatic Memory Writing

Implement auto-remembering based on config.

**Add to `RootAgent.process_tool_result` or similar hook:**

```python
def _maybe_auto_remember(self, tool_name: str, result: str, is_error: bool):
    """Auto-store important events based on configuration"""
    if not self.memory_enabled:
        return
        
    config = self.memory_config.get("auto_remember", {})
    
    if is_error and config.get("errors", False):
        self._auto_remember_error(tool_name, result)
    elif not is_error and config.get("successful_tasks", False):
        self._auto_remember_success(tool_name, result)
    
def _auto_remember_error(self, tool_name: str, result: str):
    """Extract and store error information"""
    # Extract error type from result (heuristics)
    error_summary = f"Error in {tool_name}: {result[:200]}"
    
    # Call memory_remember tool
    memory_remember_tool.execute(
        content=f"Tool: {tool_name}\nError: {result}",
        summary=error_summary,
        tags=["error", tool_name],
        importance=0.85,
        source="tool_output"
    )

def _auto_remember_success(self, tool_name: str, result: str):
    """Store successful patterns"""
    if len(result) > 1000:  # Only store substantial results
        summary = f"Successful {tool_name} execution: {result[:100]}..."
        memory_remember_tool.execute(
            content=f"Tool: {tool_name}\nResult: {result}",
            summary=summary,
            tags=["success", tool_name],
            importance=0.6,
            source="tool_output"
        )
```

### 5.6 Slash Commands

Add memory management slash commands in `src/wichy/slash_commands.py`:

```python
@register_slash_command("/memory status")
def cmd_memory_status(args: str, context):
    """Show memory statistics for current project"""
    # Query SQLite for counts, sizes, etc.
    # Return: "Global memories: 245, Project memories: 12, Total size: 2.1 MB"
    pass

@register_slash_command("/memory config")
def cmd_memory_config(args: str, context):
    """Show current memory configuration"""
    # Display effective config (project overrides global)
    pass

@register_slash_command("/memory clear")
def cmd_memory_clear(args: str, context):
    """Clear all memories for current project (with confirmation)"""
    # Use memory_forget with dry_run first, then confirm
    pass

@register_slash_command("/memory search <query>")
def cmd_memory_search(args: str, context):
    """Quick memory search with default parameters"""
    # Wrapper around memory_search tool
    pass
```

---

## 6. Implementation Steps

### Phase 1: Foundation Infrastructure

**Goals:** Set up storage directories, SQLite schema, embedding provider (supporting both Ollama and Llama.cpp)

**Tasks:**
1. Create directory discovery utilities:
   - `src/wichy/memory/path_resolver.py` - Find global and project memory paths
   - Handle creation of directories if missing
2. Implement SQLite schema with `sqlite-vec`:
   - `src/wichy/memory/database.py` - Database connection, migrations, schema creation
   - Test with in-memory DB
3. Implement embedding provider abstraction supporting multiple backends:
   - Create `src/wichy/memory/embedding.py` with abstract `EmbeddingProvider` class
   - Implement `OllamaEmbeddingProvider` - uses Ollama HTTP API (e.g., `nomic-embed-text`)
   - Implement `LlamaCppEmbeddingProvider` - uses llama-cpp-python library or llama.cpp server
   - Implement `SentenceTransformerEmbeddingProvider` - pure Python local embeddings
   - Configuration selects provider and model name
4. Implement basic CRUD operations:
   - `create_memory(metadata, content)` → writes markdown + inserts to SQLite
   - `read_memory(memory_id)` → parse markdown
   - `update_memory(memory_id, updates)` → modify markdown + re-embed if content changes
   - `delete_memory(memory_id, soft=True)` → mark as deleted or remove
5. Write unit tests for path resolution, database schema, and embedding providers (mocked)

### Phase 2: Tool Implementation

**Goals:** Implement three memory tools with Pydantic validation

**Tasks:**
1. Create `src/wichy/tools/memory.py`:
   - `MemoryRememberTool` class inheriting from `BaseTool`
   - `MemorySearchTool` class
   - `MemoryForgetTool` class
   - Parameter models with Pydantic
2. Implement `memory_remember`:
   - Project detection
   - Markdown generation with frontmatter
   - Embedding computation (async if needed)
   - SQLite insertion (main + FTS)
   - Link resolution if `link_to` provided
3. Implement `memory_search`:
   - Hybrid BM25 + vector query
   - RRF fusion
   - Filtering (project, tags, importance, source)
   - Temporal boost calculation
   - Proper result formatting
4. Implement `memory_forget`:
   - Safety checks (require at least one filter)
   - Soft delete by default (move to `deleted/` subdirectory)
   - Deletion log for audit
   - Dry-run mode
5. Comprehensive unit tests:
   - Mock embedding provider (return fixed vectors)
   - Test all three tools with in-memory DB
   - Test filters, RRF, temporal decay
   - Test safety restrictions (no filter → error)

### Phase 3: Integration

**Goals:** Connect memory tools to Wichy agent, enable context injection

**Tasks:**
1. Register tools in `src/wichy/tools/__init__.py`
2. Update `RootAgent` class:
   - Add `memory_enabled` attribute to `__init__`
   - Implement `_detect_project_name()` and `_load_memory_config()`
   - Implement `enrich_context_with_memories()` with MMR diversity
3. Modify main loop in `src/wichy/__main__.py`:
   - Call `enrich_context_with_memories(user_input)` before `append()`
4. Add config mechanism:
   - Load global config from `~/.wichy/config.yaml` if exists
   - Load project config from `.wichy/memory/config.yaml` if exists
   - Merge with defaults
   - Pass config to RootAgent
5. Add slash commands for memory management
6. End-to-end test: Run Wichy with memory enabled, have a conversation, search memories

### Phase 4: Auto-Remembering & Importance

**Goals:** Automatic memory writing based on events, importance scoring

**Tasks:**
1. Implement importance calculation algorithm:
   - Base importance from `auto_remember` event type
   - Boost from `retrieval_count * use_count_weight`
   - Boost from recency: `importance * (1 + recency_weight * exp(-days/30))`
   - Update `memory_usage` table on each retrieval
   - Periodic recalculation (on access or background job)
2. Hook into tool execution:
   - In `Tool.execute()` wrapper or `RootAgent` result processing
   - Call `_maybe_auto_remember()` based on config
3. Detect user preferences:
   - Pattern matching on user inputs (e.g., "I prefer...", "use X instead")
   - Store as memory with source=user_input
4. Implement compaction/summarization:
   - Background task to find old low-importance memories
   - Use LLM to summarize into new high-importance memory
   - Soft-delete originals
5. Testing: Verify auto-remember works for errors, successes, preferences

### Phase 5: Multi-Agent Support

**Goals:** Ensure TaskAgentTool sub-agents properly use memory

**Tasks:**
1. Modify `TaskAgentTool`:
   - Accept `memory_isolation` parameter in task description
   - Accept `shared_memory_group` parameter
   - Propagate parent agent's memory context to sub-agent unless isolated
2. In `TaskAgent` creation:
   - If `memory_isolation=true`, create fresh memory manager with empty DB
   - If `shared_memory_group` set, use shared memory DB (stored globally or in project)
   - Otherwise inherit parent's memory manager (share same indexes)
3. Test scenarios:
   - Sub-agent without isolation can read/write parent's memory
   - Sub-agent with isolation has separate memory
   - Multiple sub-agents in same group share memory
4. Consider memory cleanup:
   - When isolated sub-agent finishes, optionally prompt user to merge learnings
   - Or auto-merge high-importance memories to parent

### Phase 6: Polish, Security & Performance

**Goals:** Production readiness, security, optimization

**Tasks:**
1. **Security:**
   - Implement redaction of API keys, passwords, tokens before storage
   - Use regex patterns for common secret formats
   - Configuration option `redact_patterns` (default includes sensible defaults)
2. **Performance:**
   - Add embedding cache: hash content → embedding vector (avoid recompute)
   - Async indexing (watchdog observer on memory directories)
   - Batch insertions for bulk operations
3. **Robustness:**
   - Handle corrupted markdown files (backup and skip)
   - Schema migrations for future changes
   - Database connection pooling (or single connection with proper locking)
4. **User Experience:**
   - Progress indicators for large searches or compaction
   - Memory visualization command (`/memory graph`?) - show relationships
   - Export/import memories (JSON, markdown bundle)
   - Memory usage report (by project, by tag, by source)
5. **Documentation:**
   - README section on memory system
   - Configuration examples
   - Troubleshooting guide (embedding model setup, SQLite extension)
   - Migration guide from pre-memory Wichy

---

## 7. Advanced Considerations

### 7.1 Context Window Management

**Problem:** Retrieval may return many memories that overwhelm context.

**Solutions:**
1. **Dynamic Budgeting:** Count tokens of retrieved memories; if over budget, drop lowest importance first
2. **Adaptive k:** Increase `context_injection` as conversation grows longer (include summaries)
3. **Summarization Chain:** Periodic creation of "meta-memories" that summarize clusters of old memories (implemented in compaction)
4. **Lazy Loading:** Only inject memory summaries (first 200 chars) and let agent request full content via `memory_get` tool if needed

### 7.2 Importance Decay Algorithm

Long-term memories should gradually decay if unused:

```python
def calculate_dynamic_importance(base_importance: float, retrieval_count: int, last_retrieved_days_ago: float, created_days_ago: float) -> float:
    # Base + retrieval boost (diminishing returns)
    retrieval_boost = math.log(1 + retrieval_count) * config.use_count_weight
    
    # Recency boost: higher if retrieved recently
    recency_boost = config.recency_weight * math.exp(-last_retrieved_days_ago / 30)
    
    # Age decay: older memories lose importance slowly
    age_decay = math.exp(-created_days_ago / config.half_life_days)
    
    importance = base_importance * (0.5 + 0.5 * age_decay) + retrieval_boost + recency_boost
    
    return min(1.0, max(0.0, importance))
```

Run this update on each memory access (read or search) and write back to SQLite.

### 7.3 Memory Deduplication

Avoid storing duplicate or near-duplicate memories:

- On `memory_remember`, compute embedding and search for nearest neighbors
- If similarity > 0.95, suggest merging instead of creating new
- Or automatically merge if content is substring or very similar

### 7.4 Sub-Agent Memory Strategies

**Inherited Memory (default):**
- Sub-agent uses same memory DB as parent
- Pros: learns together, unified knowledge base
- Cons: may clutter with temporary task details

**Isolated Memory:**
- Sub-agent gets fresh empty memory
- Pros:隔离, no noise
- Cons: loses cross-task learning

**Shared Group Memory:**
- Multiple sub-agents (and parent) share a group DB
- Good for multi-step workflows where each agent contributes
- Implement as separate memory store under `~/.wichy/memory/groups/<group_id>/`

---

## 8. Configuration Examples

### Example 1: Enable Memory for a Project

Create `.wichy/memory/config.yaml`:

```yaml
memory:
  enabled: true
  use_global: true
  auto_remember:
    errors: true
    successful_tasks: true
```

### Example 2: Privacy-Focused Project (No Global)

```yaml
memory:
  enabled: true
  use_global: false      # Don't read from global
  write_to_global: false # Don't write to global
  tags:
    exclude_from_global: ["sensitive", "confidential"]
```

### Example 3: Development vs Production

Global config `~/.wichy/config.yaml`:

```yaml
memory:
  enabled: true
  embedding_source: sentence_transformers  # Faster, local only
  auto_remember:
    errors: true
    successful_tasks: false  # Don't clutter with routine successes
  search:
    context_injection: 5
```

In production with more data, switch to `embedding_source: ollama` for better embeddings.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Context window overflow from injected memories | Token counting before injection; dynamic k based on token budget; summarization |
| Storing sensitive data in global memory | `exclude_from_global` tags; auto-redaction of secrets; project config opt-out |
| Embedding computation slow | Async indexing with queue; embedding cache; batch processing |
| SQLite file corruption | Write-ahead logging (WAL); regular backups; integrity checks on startup |
| Memory bloat (too many entries) | Retention policies (min importance, max age); auto-compaction; manual `/memory clear` |
| Duplicate/contradictory memories | Deduplication on write; importance scoring favors consensus; `memory_forget` to clean up |
| Sub-agent memory leakage | Default to isolated memory for untrusted sub-agents; explicit opt-in for sharing |

---

## 10. Open Questions for User

1. **Auto-remembering defaults:** Should errors and successes be auto-remembered by default? (Recommended: errors yes, successes no by default - successes can clutter)
2. **Opt-in vs opt-out:** Should memory be enabled by default for all projects, or require explicit config file? (Recommended: opt-in via config to avoid surprising writes)
3. **Sub-agent memory sharing:** Should all TaskAgents share parent's memory, or be isolated by default? (Recommended: share by default, with `memory_isolation: true` option for isolation)
4. **Memory quotas:** Should we enforce per-project memory limits (e.g., 1000 entries or 50MB)? (Recommended: soft limits with warnings, configurable)
5. **Compaction frequency:** Should compaction be manual (`/memory compact`) or automatic (nightly)? (Recommended: automatic with configurable schedule)
6. **Embedding model:** Prefer local (`sentence-transformers`) or server (`ollama/nomic-embed-text`)? (Depends on user's GPU availability; local is simpler)

---

## 11. Comparison to OpenClaw

| Feature | OpenClaw | Wichy (Proposed) |
|---------|----------|------------------|
| Workspace | Single global | Hybrid (global + per-project) |
| Storage | Markdown + SQLite (global) | Markdown + SQLite (local + global) |
| Context Injection | Manual (via memory_search tool) | Automatic (configurable count) |
| Memory Creation | Explicit `write` tool call | Explicit `memory_remember` + auto-remembering |
| Multi-agent | Single agent | Hierarchical (root + sub-agents) with shared/isolated options |
| Temporal Decay | Yes (configurable half-life) | Yes (configurable) |
| Index Search | BM25 + vector + MMR | BM25 + vector + RRF |
| Privacy | Single workspace, all data in one place | Project isolation + selective global sharing |
| Configuration | `openclaw.json` | YAML configs (global + per-project) |
| Auto-compaction | Yes | Yes (configurable) |
| LLM Backend | Likely single | Ollama, Llama.cpp, OpenRouter |

---

## 12. Next Steps

If approved, begin implementation with **Phase 1**:

1. Set up `src/wichy/memory/` module structure
2. Implement `path_resolver.py` and database initialization
3. Add dependencies to `pyproject.toml`
4. Write unit tests for path resolution and DB schema
5. Create global and project config loaders

Then proceed sequentially through Phases 2-6, with testing at each phase.

---

**Document Version:** 1.0
**Date:** 2025-02-23
**Status:** Proposed Design
