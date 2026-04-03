# Introspection Mechanisms in LLM Agentic Harnesses
## Research Report: OpenClaw & Claude Code

*Research date: 2026-04-03*
*Repos examined: openclaw-main, claude-code-main*

---

## Executive Summary

Both OpenClaw and Claude Code are LLM coding agents with sophisticated, multi-layered introspection and memory systems. While neither calls it a "journal" in the harness sense, both implement rich recording, compaction, and memory mechanisms that serve similar purposes: maintaining context across sessions, enabling the agent to recall prior work, and compressing long conversations when context limits are approached.

This report covers:
1. Session transcript and logging mechanisms
2. Memory and journaling systems (prompt-driven)
3. Compaction and summarization
4. Tool call and execution observation
5. External research on the broader field

---

## Part I: OpenClaw

### 1. Session Transcript (Primary Journaling Mechanism)

**Files:**
- `src/agents/session-tool-result-guard.ts` — Core wrapper, monkey-patches `SessionManager.appendMessage`
- `src/agents/transcript-rewrite.ts` — Rewrites past message entries
- `src/agents/transcript-policy.ts` — Per-provider transcript policies (sanitization, truncation)
- `src/sessions/transcript-events.ts` — Event bus emitting `SessionTranscriptUpdate`

**What it records:** Every message in the agent's conversation — user messages, assistant responses, tool calls, tool results, compaction summaries, thinking level changes, model changes, custom entries — written as a persistent append-only JSONL log.

**Key constraints:**
- `HARD_MAX_TOOL_RESULT_CHARS = 400_000` — hard cap on a single tool result
- `beforeMessageWriteHook` — optional plugin hook that can block or transform a message before persistence

**Output:** `$SESSION_DIR/session.jsonl` (JSONL per session)

**Entry point:** Every tool execution result goes through `guardedAppend()` → `originalAppend()`. `emitSessionTranscriptUpdate()` fires on every write, notifying all listeners (daemon, UI, memory index).

---

### 2. Cache Trace — Structured LLM Call Tracer

**File:** `src/agents/cache-trace.ts`

**What it records:** A structured JSONL log of every LLM call's inputs and outputs at key pipeline stages.

**Stages captured:**
- `session:loaded` → `session:sanitized` → `session:limited` → `prompt:before` → `prompt:images` → `stream:context` → `session:after`

**Output:** `$OPENCLAW_STATE_DIR/logs/cache-trace.jsonl` (enabled via `OPENCLAW_CACHE_TRACE=1`)

**Key feature:** Uses `stableStringify()` with SHA-256 digest for deterministic serialization — useful for cache-digest debugging.

---

### 3. Tool Loop Detection — Self-Observation of Agent Behavior

**Files:**
- `src/agents/tool-loop-detection.ts` — Generic repeat, poll-no-progress, ping-pong, global circuit breaker
- `src/agents/pi-tools.before-tool-call.ts` — Hook that runs loop detection before each tool invocation
- `src/logging/diagnostic-session-state.ts` — Per-session tool call history

**Thresholds:**
- `WARNING_THRESHOLD = 10` — logs warning, continues
- `CRITICAL_THRESHOLD = 20` — blocks tool execution
- `GLOBAL_CIRCUIT_BREAKER_THRESHOLD = 30` — global kill switch

**What it records:** Tool call frequency, repetition patterns, and loop conditions per `sessionKey`.

---

### 4. Active Run Snapshots

**File:** `src/agents/pi-embedded-runner/runs.ts`

**What it records:** Live in-memory snapshots of in-progress agent runs — `transcriptLeafId`, in-flight messages, current prompt — accessible to the daemon and UI.

**Maps:**
- `ACTIVE_EMBEDDED_RUNS`: `Map<sessionId, EmbeddedPiQueueHandle>` — controls live runs
- `ACTIVE_EMBEDDED_RUN_SNAPSHOTS`: `Map<sessionId, ActiveEmbeddedRunSnapshot>` — per-run state
- `ACTIVE_EMBEDDED_RUN_WAITERS`: Waiters for run completion
- `EMBEDDED_RUN_MODEL_SWITCH_REQUESTS`: Deferred model switches

---

### 5. Compaction / Context Summarization

**Files:**
- `src/agents/pi-embedded-runner/compact.ts` — Core compaction logic
- `src/agents/pi-embedded-runner/compaction-hooks.ts` — Before/after hooks, memory sync

**Trigger:** `contextOverflowError` detected in the retry loop, or session timeout.

**What it does:** Captures a snapshot of the transcript, replaces old history with a compact summary to free context tokens. Pre-compaction snapshot is taken before rewriting.

**Types:** `"budget" | "overflow" | "manual" | "timeout_recovery"`

---

### 6. Agent Event System

**File:** `src/infra/agent-events.ts`

**What it records:** Typed, sequenced events emitted during agent execution — streaming blocks, tool execution, reasoning, errors — broadcast to WebSocket clients and internal consumers.

**Event streams:** `"lifecycle" | "tool" | "assistant" | "error" | string`

---

### 7. Plugin Hook System

**Files:**
- `src/plugins/hook-runner-global.ts` — Global hook runner singleton
- `src/plugins/types.ts` — Hook event types

**Hook types:** `before_tool_call`, `after_tool_call`, `before_compaction`, `after_compaction`, `before_agent_start`, `agent_end`, `llm_output`

---

### 8. Subagent Registry

**File:** `src/agents/subagent-registry.ts`

**What it records:** All subagent runs, their status, lineage, and lifecycle — persisted to `stateDir/subagents/runs.json`.

---

### 9. Memory System (Prompt-Driven Journaling)

**File:** `extensions/memory-core/src/prompt-section.ts` (L3–38)

#### `## Memory Recall` System Prompt

Injected into every DM session's system prompt. Adapts based on available tools:

```typescript
// If both memory_search + memory_get are available:
"Before answering anything about prior work, decisions, dates, people,
 preferences, or todos: run memory_search on MEMORY.md + memory/*.md;
 then use memory_get to pull only the needed lines."
```

**Skipped for subagents** (`isMinimal=true`).

#### Memory Flush (Pre-Compaction Journaling)

**File:** `extensions/memory-core/src/flush-plan.ts`

Triggered when session approaches context exhaustion. A silent turn runs:

```
Pre-compaction memory flush.
Store durable memories only in memory/YYYY-MM-DD.md (create memory/ if needed).
If memory/YYYY-MM-DD.md already exists, APPEND new content only.
Treat MEMORY.md, SOUL.md, TOOLS.md, AGENTS.md as read-only.
Do NOT create timestamped variant files (e.g., YYYY-MM-DD-HHMM.md).
```

**Execution:** `src/auto-reply/reply/agent-runner-memory.ts` — deduplicates via SHA-256 of last 3 messages.

#### Session Memory Hook (On `/new` or `/reset`)

**File:** `src/hooks/bundled/session-memory/handler.ts`

On session reset, captures last ~15 messages, generates a slug via LLM, writes:

```markdown
# Session: 2026-04-03 14:30:00 UTC
- **Session Key**: agent:main:main
- **Session ID**: abc123def456
- **Source**: telegram

## Conversation Summary
[last ~15 messages summarized]
```

#### Memory File Structure

| File | Purpose |
|---|---|
| `MEMORY.md` | Long-term durable facts, preferences, decisions — loaded at session start |
| `memory/YYYY-MM-DD.md` | Daily running notes, appended by flush (append-only) |
| `memory/YYYY-MM-DD-slug.md` | Per-session summaries from `/new` or `/reset` |

#### Search Backend

**File:** `extensions/memory-core/src/memory/manager.ts`

SQLite per-agent database (`~/.openclaw/memory/<agentId>.sqlite`):
- FTS5 full-text search (BM25 keyword scoring)
- Optional vector embeddings (OpenAI, Gemini, Voyage, Mistral, Ollama, GGUF)
- File watching with 1.5s debounce for live reindexing

---

### 10. Execution Loop

**Top-level:** `src/agents/pi-embedded-runner/run.ts` → `runEmbeddedPiAgent()`
**Single-turn:** `src/agents/pi-embedded-runner/run/attempt.ts` → `runEmbeddedAttempt()`
**Streaming:** `src/agents/pi-embedded-subscribe.ts` → `subscribeEmbeddedPiSession()`

Tool calls are intercepted via `pi-tool-definition-adapter.ts` wrapping `.execute()` with `runBeforeToolCallHook()`.

---

## Part II: Claude Code

### 1. Session Transcript

**Files:**
- `src/utils/sessionStorage.ts` (5105 lines) — Primary transcript writer
- `src/types/logs.ts` (330 lines) — Entry type definitions

**What it records:** Every user message, assistant message, attachment, system message, and metadata entry to `~/.claude/projects/<project>/<sessionId>.jsonl`.

**Entry types (20+):** `TranscriptMessage`, `SummaryMessage`, `CustomTitleMessage`, `AiTitleMessage`, `TaskSummaryMessage`, `TagMessage`, `ContentReplacementEntry`, `ContextCollapseSnapshotEntry`, `FileHistorySnapshotMessage`, `AttributionSnapshotMessage`, `QueueOperationMessage`, `SpeculationAcceptMessage`, `ModeEntry`, `PRLinkMessage`, and more.

**Key features:**
- `recordTranscript()` deduplicates already-written messages
- Messages buffered in memory, flushed every 100ms
- `recordSidechainTranscript()` — subagent transcripts to separate `subagents/agent-<agentId>.jsonl`
- `recordContentReplacement()` — file-content replacements for cache stability on resume
- `recordFileHistorySnapshot()` / `recordAttributionSnapshot()` — periodic state snapshots

---

### 2. Asciicast Terminal Recording

**File:** `src/utils/asciicast.ts`

Records all terminal stdout as an asciinema v2 `.cast` file for replay. Each output line is timestamped as `[elapsed_seconds, 'o', text]`. Terminal resize events recorded as `[elapsed, 'r', 'colsxrows']`.

**Output:** `~/.claude/projects/<cwd>/<sessionId>-<timestamp>.cast`
**Enabled by:** `USER_TYPE=ant` + `CLAUDE_CODE_TERMINAL_RECORDING=1`

---

### 3. Perfetto Tracing

**File:** `src/utils/telemetry/perfettoTracing.ts` (1120 lines)

Full Chrome Trace Event format tracing. Writes to `~/.claude/traces/trace-<sessionId>.json`. Viewable at ui.perfetto.dev.

**Traced events:**
- Agent hierarchy (parent-child in swarm)
- API calls with TTFT, TTLT, prompt/cache tokens, message ID
- Request Setup sub-spans
- First Token / Sampling sub-phases
- Tool executions with name, duration, token usage, success/error
- User input waiting spans
- Interaction spans

**Max events:** 100,000 — oldest half evicted when cap hit.

---

### 4. Query Profiler

**File:** `src/utils/queryProfiler.ts`

Enabled via `CLAUDE_CODE_PROFILE_QUERY=1`. Records 30+ per-turn pipeline checkpoints with ASCII bar chart output:
`query_user_input_received`, `query_context_loading_start/end`, `query_api_loop_start`, `query_tool_schema_build_start/end`, `query_message_normalization_start/end`, `query_client_creation_start/end`, `query_api_request_sent`, `query_first_chunk_received`, `query_api_streaming_end`, `query_tool_execution_start/end`, and more.

---

### 5. Headless Profiler

**File:** `src/utils/headlessProfiler.ts`

Profiles per-turn latency in headless/`-p` mode. Sampled: 100% of ant users, 5% of external users. Logs `tengu_headless_latency` to Statsig.

---

### 6. Memory System (Prompt-Driven Journaling)

Claude Code has the most sophisticated memory system of the two repos.

#### Four-Type Memory Taxonomy

**File:** `src/memdir/memoryTypes.ts`

| Type | Scope | Description |
|---|---|---|
| `user` | always private | Role, goals, preferences, knowledge |
| `feedback` | default private | What to avoid or repeat — corrections AND confirmations |
| `project` | private or team | Ongoing work, goals, bugs, incidents, decisions |
| `reference` | usually team | Pointers to external systems (Linear, Slack, Grafana...) |

Each type defines `<when_to_save>`, `<how_to_use>`, `<body_structure>`, and `<examples>`.

#### `MEMORY.md` — The Index

**File:** `src/memdir/memdir.ts`

`MEMORY.md` is the **index** (up to 200 lines, max 25KB). Each entry:
```
- [Title](file.md) — one-line hook
```

Topic files (e.g., `user_role.md`, `feedback_testing.md`) have YAML frontmatter with `name`, `description`, `type`.

#### Core Memory Prompt

**File:** `src/memdir/memdir.ts` (`buildMemoryLines()`, L199–265)

```
# auto memory

You have a persistent, file-based memory system at
`~/.claude/projects/<path>/memory/`. This directory already exists —
write to it directly...

You should build up this memory system over time so that future
conversations can have a complete picture of who the user is,
how they'd like to collaborate with you, what behaviors to
avoid or repeat, and the context behind the work...
```

**Two-step save process:** Write each memory to its own `.md` file with YAML frontmatter → add a pointer to `MEMORY.md`.

**What NOT to save:**
```
- Code patterns, conventions, architecture, file paths — derivable by reading the project
- Git history — git log / git blame are authoritative
- Debugging solutions or fix recipes — the fix is in the code; the commit message has context
- Ephemeral task details: in-progress work, temporary state, current conversation context
```
> "If they ask you to save a PR list or activity summary, ask what was *surprising* or *non-obvious* about it — that is the part worth keeping."

**Trusting recall:**
```
"The memory says X exists" is not the same as "X exists now."
Before recommending from memory: check the file exists, grep for the function or flag.
```

#### Background Memory Extraction Agent

**File:** `src/services/extractMemories/prompts.ts`

After **every complete query turn** (when the model stops producing tool calls), a forked subagent analyzes recent messages and updates memory files.

**Constraints:**
- Only Read/Edit/Write/Grep/Glob on memory directory — no `rm`, no Bash write
- Must read all files in parallel in one turn, then write in the next — no interleaving
- Only use content from recent messages — no investigating or grepping source files

This is like OpenClaw's memory flush, but running **continuously** rather than only at compaction time.

#### Per-Turn Memory Relevance Selection

**File:** `src/memdir/findRelevantMemories.ts`

Before each API call, a **Sonnet model** selects up to 5 relevant memory files based on the user's query. These are injected as `relevant_memories` attachments with staleness caveats for files > 1 day old.

```
This memory is N days old. Memories are point-in-time observations...
Verify against current code before asserting as fact.
```

#### Session Memory — Within-Session Self-Awareness

**File:** `src/services/SessionMemory/prompts.ts`

A **within-session notes file** (`~/.claude/sessions/<id>/session-memory/notes.md`) maintained by a background forked agent with this fixed template:

```markdown
# Session Title        (5-10 word distinctive title)
# Current State         (what is being worked on now, pending tasks, next steps)
# Task specification    (what the user asked to build, design decisions)
# Files and Functions   (important files and why they're relevant)
# Workflow              (bash commands, their order, how to interpret output)
# Errors & Corrections (errors, fixes, user corrections, failed approaches)
# Codebase and System Documentation  (important components)
# Learnings             (what worked, what didn't, what to avoid)
# Key results           (exact output the user requested)
# Worklog               (step-by-step terse summary)
```

**Update rules:**
- Never modify section headers or italic descriptions
- Always update "Current State" — "this is critical for continuity after compaction"
- Include exact commands, error messages, file paths — not summaries

**Triggered:** After every ~5K tokens of growth or ~3 tool calls.

#### KAIROS Daily-Log Mode

**File:** `src/memdir/memdir.ts` (`buildAssistantDailyLogPrompt()`)

For long-lived assistant sessions, append-only daily logs at `{memoryDir}/logs/YYYY/MM/YYYY-MM-DD.md`:

```
This session is long-lived. As you work, record anything worth remembering
by appending to today's daily log file...
Write each entry as a short timestamped bullet. Create the file on first
write. Do not rewrite or reorganize the log — it is append-only. A separate
nightly process distills these logs into MEMORY.md and topic files.
```

---

### 7. Compaction / Summarization

**File:** `src/services/compact/prompt.ts` (374 lines)

Three compact variants:
- `BASE_COMPACT_PROMPT` — Full conversation summary
- `PARTIAL_COMPACT_PROMPT` — Recent-only summary
- `PARTIAL_COMPACT_UP_TO_PROMPT` — Prefix-preserving (for cache invalidation)

**Structured summary output:**
```
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections (with full snippets)
4. Errors and fixes
5. Problem Solving
6. All user messages (non-tool-result)
7. Pending Tasks
8. Current Work (most recent, with file names and code snippets)
9. Optional Next Step (with verbatim quotes)
```

**Hard constraint:** `CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.`

**Session Memory Compact:** If session memory has been actively maintained, the already-extracted notes are used as the summary instead of calling the LLM API — no API call needed.

**Microcompact:** Lightweight pre-summarization token reduction pass to reduce API call token count.

---

### 8. Execution Loop

**File:** `src/query.ts` (1729 lines)

An `async function* query()` generator — a `while(true)` turn loop:
1. Builds `toolUseContext` and `messagesForQuery`
2. Streams API request, yields events (`message_start`, `content_block_delta`, `tool_use`, etc.)
3. Accumulates tool use blocks, calls `runTools()`
4. Appends tool results, continues loop
5. Exits on `end_turn`, `maxTurns`, stop hook, or `AbortController`

**Tool orchestration:** `src/services/tools/toolOrchestration.ts` — partitions tools into read-only (concurrent, up to 10) and non-read-only (serial) batches.

---

## Part III: External Research — The Broader Field

### Journaling / Trace / Playback in Popular Frameworks

#### LangGraph (LangChain)
Most mature checkpointing system. Saves entire graph state at each super-step boundary to a **thread**:
- Enables **replay** from any checkpoint
- Enables **fork** — branch from a past checkpoint with modified state
- Enables **interrupt** — human-in-the-loop pause points
- Checkpointers: `InMemorySaver`, PostgreSQL, SQLite, Redis, Aerospike, and more
- **LangSmith** provides cloud trace replay

#### CrewAI
- Built-in tracing via CrewAI AMP — records agent decisions, task timelines, tool usage, LLM calls, cost
- **Replay Tasks** from latest crew kickoff
- 15+ observability integrations: Arize Phoenix, Braintrust, Datadog, Galileo, LangDB, Langfuse, Langtrace, MLflow, OpenLIT, Opik, Patronus AI, Portkey, Weave, TrueFoundry

#### AutoGen / AG2 (Microsoft)
- First-class **OpenTelemetry** instrumentation via `autogen.opentelemetry`
- Span taxonomy: `conversation`, `invoke_agent`, `chat`, `execute_tool`, `execute_code`, `await_human_input`
- Traces to any OTel-compatible backend (Jaeger, Grafana Tempo, Datadog, etc.)

#### LlamaIndex
- Central `CallbackManager` — hooks into query engines, LLM calls, tool execution, retrieval
- Native LangSmith, Arize Phoenix, Langfuse, OpenTelemetry support

#### Google ADK
- Built-in integrations: Opik/Comet, LangDB, Agenta, MLflow, Langfuse, OpenLayer

### Academic Work

#### Reflexion (NeurIPS 2023)
The seminal paper on agent-internal journaling:
1. Agent executes a task
2. Receives feedback signal (pass/fail, unit test, critique)
3. **Verbally reflects** on failure in a persistent episodic memory buffer
4. Uses those notes to guide better decisions on subsequent trials

This is explicitly an **agent-driven** journaling mechanism — the LLM journals *for itself*, distinct from harness-level observability.

### Emerging Standards

#### OpenTelemetry GenAI Semantic Conventions
- Standardized span kinds: `llm`, `agent`, `chain`, `tool`, `retriever`, `reranker`, `embedding`, `guardrail`, `evaluator`, `prompt`
- GenAI-specific metric schemas (token throughput, latency, cost)
- Status: **In Development** — actively iterating

#### OpenInference (Arize AI)
- Richer AI-specific layer on OTel
- Span kinds: `AGENT`, `LLM`, `TOOL`, `RETRIEVER`, `CHAIN`, `EMBEDDING`, `GUARDRAIL`, `EVALUATOR`, `PROMPT`
- Natively supported by Arize Phoenix; exportable to any OTel backend

#### Model Context Protocol (MCP)
- Active proposal for OTel trace support in MCP servers
- MCP Logging Utility already provides structured, correlated logging between MCP clients and servers

---

## Part IV: Comparative Analysis

### Session Transcript (Machine-Written Journal)

| Aspect | OpenClaw | Claude Code |
|---|---|---|
| **Output format** | JSONL (`session.jsonl`) | JSONL (`<sessionId>.jsonl`) |
| **Message types** | AgentMessage (generic) | 20+ explicit types (logs.ts) |
| **Sidechain transcripts** | Subagent registry (metadata only) | Subagent JSONL files |
| **Periodic snapshots** | No | File history, attribution snapshots |
| **Terminal replay** | No | Yes — asciicast `.cast` files |
| **Structured trace** | Cache trace (LLM calls only) | Perfetto (full execution) |

### Memory System (Agent-Written Journal)

| Aspect | OpenClaw | Claude Code |
|---|---|---|
| **Memory file** | `MEMORY.md` + `memory/YYYY-MM-DD.md` | `MEMORY.md` + topic files |
| **Memory types** | Not explicitly typed | 4-type taxonomy (user/feedback/project/reference) |
| **Flush trigger (durable)** | Token threshold near compaction | After every complete turn (background agent) |
| **Flush trigger (session)** | `/new` or `/reset` | Every ~5K tokens or ~3 tool calls |
| **Flush output** | Daily `memory/YYYY-MM-DD.md` | Per-topic `.md` files with YAML frontmatter |
| **Memory search** | SQLite FTS5 + vectors (hybrid) | Sonnet model selects relevant files per-turn |
| **Flush agent constraint** | Silent turn, no explicit tool restriction | Forked agent: read memory dir only, no source investigation |
| **Daily log mode** | No | Yes (KAIROS mode — append-only daily logs) |

### Compaction

| Aspect | OpenClaw | Claude Code |
|---|---|---|
| **Trigger** | `contextOverflowError` or timeout | Token threshold |
| **Summary type** | Agent writes memories before compact | Structured summary prompt (no tools) |
| **Pre-compact capture** | Pre-compaction snapshot | Microcompact (token reduction pass) |
| **Post-compact restoration** | No | Top 5 most-recently-read files reinjected |
| **Session memory compact** | No | Yes — use already-extracted notes instead of API call |

### Self-Awareness

| Aspect | OpenClaw | Claude Code |
|---|---|---|
| **Within-session notes** | No | Yes — session memory template with 10 sections |
| **Self-tracking sections** | No | Current State, Task spec, Workflow, Errors, Learnings, Worklog |
| **Loop detection** | Yes — tool call history + circuit breaker | No (relies on model-level reflection) |
| **Plugin hook system** | Extensive — before/after every lifecycle point | Not present |

---

## Key Design Insights

### 1. Two Types of Journaling

**Machine-written journals** (session transcripts, traces) record exactly what happened — every message, every tool call, every LLM call. These are for debugging, auditing, and replay.

**Agent-written journals** (memory files, session notes) record what the agent deemed worth preserving — synthesized, semantic summaries. These are for continuity and recall across sessions.

OpenClaw leans toward the machine-written model for transcripts but delegates journaling to the agent for memory. Claude Code does both — machine-written transcripts AND a continuous background agent extracting durable memories.

### 2. Flush Trigger Strategy

OpenClaw flushes memory only at compaction time — efficient but means recent memories may not be saved if the session doesn't trigger compaction.

Claude Code flushes after every complete turn — higher overhead but ensures memories are captured continuously. The session memory then supplements this with within-session state tracking.

### 3. The Memory Is the Interface

Both systems treat memory as a **file-based, human-readable** structure — not a database. This is deliberate:
- Agents and humans can both read and write
- Easy to audit, edit, delete
- Survives tool failures and crashes
- Version-controllable

### 4. Staircased Trust

Claude Code explicitly implements a **"trust but verify"** model for memory:
- Memory files are injected as relevant context
- But the agent is told: "The memory says X exists is not the same as X exists now"
- Before recommending from memory: check the file exists, grep for the function

This is a meaningful guard against memory drift — a known failure mode in persistent memory systems.

### 5. Subagent Isolation

OpenClaw skips memory prompts for subagents (`isMinimal=true`). Claude Code runs extraction as a forked subagent sharing the parent's prompt cache. Both approaches are valid — isolation vs. coherence.

---

## Sources

### OpenClaw Files
- `src/agents/session-tool-result-guard.ts`
- `src/agents/cache-trace.ts`
- `src/agents/tool-loop-detection.ts`
- `src/agents/pi-embedded-runner/runs.ts`
- `src/agents/pi-embedded-runner/compact.ts`
- `src/agents/pi-embedded-runner/compaction-hooks.ts`
- `src/infra/agent-events.ts`
- `src/plugins/hook-runner-global.ts`
- `src/plugins/types.ts`
- `src/agents/subagent-registry.ts`
- `extensions/memory-core/src/prompt-section.ts`
- `extensions/memory-core/src/flush-plan.ts`
- `src/hooks/bundled/session-memory/handler.ts`
- `extensions/memory-core/src/memory/manager.ts`

### Claude Code Files
- `src/utils/sessionStorage.ts`
- `src/types/logs.ts`
- `src/utils/asciicast.ts`
- `src/utils/telemetry/perfettoTracing.ts`
- `src/utils/queryProfiler.ts`
- `src/utils/headlessProfiler.ts`
- `src/memdir/memdir.ts`
- `src/memdir/memoryTypes.ts`
- `src/memdir/findRelevantMemories.ts`
- `src/memdir/memoryScan.ts`
- `src/memdir/memoryAge.ts`
- `src/memdir/paths.ts`
- `src/services/extractMemories/prompts.ts`
- `src/services/extractMemories/extractMemories.ts`
- `src/services/SessionMemory/prompts.ts`
- `src/services/SessionMemory/sessionMemory.ts`
- `src/services/compact/prompt.ts`
- `src/services/compact/compact.ts`
- `src/services/compact/sessionMemoryCompact.ts`
- `src/services/compact/microCompact.ts`
- `src/query.ts`
- `src/services/tools/toolOrchestration.ts`
- `src/constants/prompts.ts`

### External References
- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366)
- [LangGraph Persistence — Core Concepts](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Time-Travel: Replay, Fork, Interrupts](https://docs.langchain.com/oss/python/langgraph/use-time-travel)
- [CrewAI Tracing Documentation](https://docs.crewai.com/en/observability/tracing)
- [AG2 OpenTelemetry Tracing](https://docs.ag2.ai/latest/docs/user-guide/tracing/opentelemetry/)
- [OpenInference Specification](https://arize-ai.github.io/openinference/spec/)
- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Model Context Protocol — Logging](https://modelcontextprotocol.io/specification/2025-03-26/server/utilities/logging)
