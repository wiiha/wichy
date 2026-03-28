# Context Engineering: Architectural Patterns for LLM Applications

**Date**: 2026-03-28 (Researched)
**Focus**: Non-RAG architectural patterns for managing LLM context — memory architectures, multi-turn conversation management, context window management, multi-agent coordination, self-correction loops, and the Context Pushing vs. Pulling paradigm.

---

## Table of Contents

1. [Memory Architectures for Agents](#1-memory-architectures-for-agents)
2. [Multi-Turn Conversation Context Management](#2-multi-turn-conversation-context-management)
3. [Context Window Management Within Agents](#3-context-window-management-within-agents)
4. [Multi-Agent Context Coordination](#4-multi-agent-context-coordination)
5. [Self-Correction and Reflection Loops](#5-self-correction-and-reflection-loops)
6. [State Management Patterns](#6-state-management-patterns)
7. [Tool/Function Calling Context Patterns (ACI)](#7-toolfunction-calling-context-patterns-aci)
8. [Context Pushing vs. Context Pulling](#8-context-pushing-vs-context-pulling)
9. [Summary & Decision Framework](#9-summary--decision-framework)

---

## 1. Memory Architectures for Agents

Context engineering in agents draws heavily from cognitive science. The unified taxonomy from the **Agent Memory Survey (2025)** categorizes memory by **form** (what carries it), **function** (why it's needed), and **dynamics** (how it evolves).

### Four Memory Types

| Memory Type | Function | Persistence |
|-------------|----------|-------------|
| **Working Memory** | Active context requiring immediate attention | Session-scoped, in-memory |
| **Episodic Memory** | Past experiences, task trajectories | Long-term, retrievable |
| **Semantic Memory** | Factual knowledge, learned concepts | Long-term, structured |
| **Procedural Memory** | Task execution patterns, skills | Embedded in weights/code |

### CoALA-Inspired Organization

The Cognitive Architectures for Language Agents (CoALA) framework organizes memory as:
- **Factual Memory**: World knowledge
- **Experiential Memory**: Insights gained through interaction
- **Working Memory**: Current active context

### Pattern: Memory Graph

Structured entity-relation storage instead of flat conversation logs:

```
User → prefers → concise explanations
Project → depends on → LangGraph
Task → failed due to → missing parameter
```

Enables semantic traversal and reasoning over relationships. Requires more complex query mechanisms than simple key-value stores, but enables structured reasoning.

### Pattern: Hierarchical Memory Architecture

Three-layer approach (2025-2026 research):
- **Short-term**: Current task context, temporary variables
- **Long-term**: User preferences, past successes/failures
- **Episodic**: Complete task trajectories for reflection

---

## 2. Multi-Turn Conversation Context Management

### Sliding Window with Periodic Summarization

```
Always include: Last 2 exchanges (most recent)
Add older messages: From most recent, stop when budget runs out
Periodic summarization: Compress history when it exceeds threshold
```

### Four Strategies, Increasing Sophistication

| Strategy | How it works | Tradeoff |
|----------|-------------|----------|
| **Fixed-window** | Keep only N most recent messages | Simple, but loses older context |
| **Sliding window** | Keep messages within time/distance threshold | Loses distant but potentially relevant context |
| **Summarized history** | Compress old messages to key facts | Loses nuance; summarization quality varies |
| **Semantic compression** | LLM distills only important information | Better fidelity but more expensive |

### Lost-in-the-Middle Mitigation

LLMs underweight information in the middle of long contexts. Mitigations:
- **Position bias correction**: Repeat important info at start and end
- **Query-focused summarization**: Only retain context relevant to current query
- **Progressive disclosure**: Start with most relevant; provide additional context on demand

---

## 3. Context Window Management Within Agents

### Token Budgeting Framework

```
Total Context = System Prompt + Few-shot Examples + RAG Context + User Input + Reserved Output
```

Example 128K-token budget allocation:

| Component | Tokens | % |
|-----------|--------|---|
| System prompt | 2,000 | 1.6% |
| Few-shot examples | 3,000 | 2.3% |
| User input | 75,000 | 58.6% |
| Output reserve | 8,000 | 6.2% |

### Context Compression Techniques

| Technique | How it works |
|-----------|-------------|
| **Map-Reduce Summarization** | Summarize each chunk independently, then combine summaries |
| **Query-Focused Extraction** | LLM extracts only portions relevant to current query |
| **Recursive Compression** | Iteratively compress until under budget |
| **LLMLingua-style** | Train models to preserve key tokens while compressing |

### Adaptive Deliberation Pattern

Agents decide dynamically how much reasoning to apply:
- **Simple tasks**: Respond directly without deep reasoning
- **Complex tasks**: Activate deeper planning and reasoning loops
- Implementation via conditional edges routing based on task complexity signals

### The Finite Attention Budget

LLMs experience **context rot** — as context length increases, ability to recall information decreases. This stems from:
1. **n² attention problem** — n tokens create n² pairwise relationships
2. **Training distribution** — models see shorter sequences more often
3. **Position encoding degradation** — attention degrades at long ranges

---

## 4. Multi-Agent Context Coordination

### Five Foundational Orchestration Patterns (Anthropic, Dec 2024)

| Pattern | When to use | Tradeoff |
|---------|------------|----------|
| **Prompt Chaining** | Sequential dependencies (A→B→C) | Predictable, but higher latency |
| **Routing** | Distinct categories needing different handling | Requires accurate classification |
| **Parallelization** | Independent subtasks | Speed gains, but needs merge step |
| **Orchestrator-Workers** | Complex tasks with unpredictable subtasks | Flexibility, but coordination overhead |
| **Evaluator-Optimizer** | Tasks with clear improvement criteria | Quality gains, but iterative cost |

### Supervisor-Worker Hierarchical Pattern

- Central supervisor manages task decomposition
- Worker agents handle specialized subtasks
- Structured handoffs with typed payloads
- Shared state with checkpointing

### Handoff Protocol Design (Skywork AI, 2025)

Critical for context preservation across agent boundaries:

```json
{
  "schemaVersion": "1.2.0",
  "role": "Researcher",
  "summary": "Key findings",
  "tool_state": {...},
  "output_contract": {...}
}
```

Key principles:
- Versioned schemas with backward compatibility
- Strict validators (Pydantic/Guardrails)
- Fail-closed on validation errors
- Preserve provenance and tool state

### Multi-Agent Context Challenges

1. **Rate limits** — AI operates much faster than humans; managing API quotas is critical
2. **Parallel execution duplication** — multiple agents independently request the same context
3. **Overlap in work** — agents without shared awareness duplicate context gathering
4. **Noisy neighbor problem** — one user pushing many tasks consumes all quota for others

---

## 5. Self-Correction and Reflection Loops

### Reflexion Pattern

```
Actor → produces actions/outputs
Evaluator → checks if output meets criteria
Self-Reflection Model → generates verbal feedback on mistakes
Actor (improved) → produces better output
```

### Context Sufficiency Self-Evaluation

Agents evaluate before responding:
- Do I have enough context to answer?
- Is the context relevant to the query?
- Should I retrieve more information?
- Am I confident in my response?

### Evaluator-Optimizer Workflow (Anthropic)

1. Generate initial response
2. Evaluator provides feedback against criteria
3. Loop until acceptance or max iterations

Best for: Translation, complex research, writing refinement.

### Three Core Elements of Self-Reflective Reasoning

1. **Generation module**: LLM action planner
2. **Self-reflection module**: Evaluator/verifier
3. **Feedback pathway**: Correction mechanism

---

## 6. State Management Patterns

### LangGraph State Architecture

Everything revolves around explicit typed state:

```python
class AgentState(TypedDict):
    current_goal: str
    conversation_history: list[Message]
    working_memory: dict
    retrieved_memories: list[Memory]
    tool_outputs: list[ToolResult]
    self_evaluation: str
```

### State Persistence Strategies

| Strategy | Use case |
|----------|----------|
| **In-memory** | Fast but volatile |
| **Checkpointed** | Save state at key points for recovery |
| **Durable execution** | Auto-retry failed steps (Inngest, Temporal) |
| **Database-backed** | Persistent across sessions |

---

## 7. Tool/Function Calling Context Patterns (ACI)

The **Agent-Computer Interface (ACI)** concept from Anthropic (Dec 2024, Sep 2025) argues that **the quality of an agent's tools is as important as the quality of its prompts**.

> *"Think about how much effort goes into human-computer interfaces (HCI), and plan to invest just as much effort in creating good agent-computer interfaces (ACI)."*
> — Erik Schluntz & Barry Zhang, Anthropic

Anthropic's SWE-bench team reported spending **more time optimizing their tools than their overall prompts**. This was the key observation that drove the ACI framework.

### Why Tools Are Different from Software Functions

Traditional software functions produce deterministic output. Tools must be interpreted by agents that can:
- Call tools correctly or incorrectly
- Fail to understand how to use them
- Hallucinate about tool behavior
- Choose sub-optimal strategies

This creates a **new kind of software contract** — between deterministic systems and non-deterministic agents.

### The Address Book Analogy

Anthropic frames the design problem with a simple analogy:

| Traditional Software (Human) | Agent-Friendly Interface |
|-------------------------------|------------------------|
| Iterate through all contacts | Search by name directly |
| List all → filter → find | `search_contacts` tool |
| Brute-force iteration | Skip to relevant information |

LLMs have **limited context attention** compared to computers that have abundant memory. Tools should let the agent skip directly to what it needs rather than requiring it to process large lists or iterate through irrelevant results.

### Three Core Principles

1. **Simplicity** — keep agent design simple
2. **Transparency** — explicitly show the agent's planning steps
3. **Careful ACI design** — thorough tool documentation and testing

### Failure Modes: Why Tools Fail

Understanding *why* tools fail is as important as knowing best practices:

| Failure Mode | Description | Mitigation |
|-------------|-------------|------------|
| **Wrong tool selection** | Agent picks wrong tool due to ambiguous descriptions | Namespacing, clear descriptions, examples |
| **Malformed parameters** | Agent provides wrong parameter types or formats | Typed parameters, enums, concrete examples |
| **Output confusion** | Agent can't parse or misinterprets tool output | Semantic return types, concise modes |
| **Context overflow** | Tool returns too much data, diluting relevant info | Pagination, max_results, time-range filters |
| **Hallucinated behavior** | Agent assumes tool works differently than it does | Explicit boundaries, edge case documentation |
| **Sub-optimal chaining** | Agent uses 5 tool calls when 1 would suffice | Consolidate tools, document intended usage |

### The "Right Altitude" for Instructions

```
❌ Too Low (Brittle):
"If user asks X, call tool Y. If tool returns error Z, do A. If error B, do C..."

❌ Too High (Vague):
"Be helpful and do the right thing."

✅ Goldilocks:
"Specific enough to guide, flexible enough for heuristics"
```

### System Prompt Organization

Anthropic recommends organizing system prompts into clear sections:

```xml
<background>
  <!-- What the agent is, its role, domain knowledge -->
</background>

<instructions>
  <!-- What to do, general guidelines -->
</instructions>

<tool_guidance>
  <!-- When to use which tools, common patterns -->
</tool_guidance>

<output_description>
  <!-- How to format responses -->
</output_description>
```

Avoid brittle if-else logic in instructions. Let the agent's own reasoning handle branching logic rather than encoding it in prompt rules.

---

### Best Practices for Tool Design

#### 1. Avoid Diff-Based Editing

❌ **BAD** — model must know line count before writing:
```json
{
  "name": "edit_file",
  "parameters": {
    "old_string": {"type": "string"},
    "new_string": {"type": "string"},
    "expected_line_count": {"type": "integer"}
  }
}
```
The model can't calculate the diff header until it knows the new content — a chicken-and-egg problem.

✅ **GOOD** — line-range based editing:
```json
{
  "name": "str_replace_editor",
  "parameters": {
    "command": {"enum": ["view", "create", "str_replace", "insert"]},
    "path": {"description": "Absolute path"},
    "old_string": {"type": "string"},
    "new_string": {"type": "string"}
  }
}
```

#### 2. Return Semantic Information, Not UUIDs

❌ **BAD** — cryptic identifiers requiring lookups:
```json
{"uuid": "a1b2c3d4", "mime_type": "image/png"}
```

✅ **GOOD** — semantically meaningful language:
```json
{"name": "John Smith", "file_type": "profile picture"}
```
Resolving arbitrary identifiers to meaningful language significantly improves retrieval precision and reduces hallucinations.

#### 3. Include Examples in Tool Definitions

```json
{
  "name": "search_logs",
  "description": "Search application logs for debugging.",
  "parameters": {
    "query": {
      "examples": [
        "ERROR",
        "purchase_complete customer_id=9182",
        "timeout.*database"
      ]
    },
    "time_range": {
      "enum": ["last_hour", "last_day", "last_week"],
      "description": "Defaults to last_day"
    }
  }
}
```

#### 4. Consolidate Tools

❌ **Too many small tools** — cognitive overhead:
```
list_users, list_events, create_event, read_logs, get_customer_by_id...
```

✅ **Consolidated tools** — fewer, richer:
```
search_logs (replaces read_logs, filter_logs, get_recent_logs)
get_customer_context (replaces get_customer, list_transactions, list_notes)
```

The rule from cubic's Paul: *"Audit your tools ruthlessly. Every tool you add adds cognitive overhead. Prioritize tools the model already knows — terminal commands, standard APIs — over custom abstractions you invented."*

#### 5. Namespacing for MCP

When many tools exist (MCP enables hundreds), namespacing prevents confusion:

```json
"asana_search_projects"
"jira_search_tickets"
"search_github_repos"
```

Namespacing helps the agent:
- Select the right tool at the right time
- Reduce context overhead from tool descriptions
- Offload decision-making to tool design rather than prompt logic

#### 6. Response Format: Concise vs. Detailed

```python
enum ResponseFormat {
    CONCISE = "concise"   # Essential content only
    DETAILED = "detailed" # Includes IDs, metadata
}
```

**Slack example:**
- Detailed response: 206 tokens (includes `channel_id`, `user_id`, `thread_ts`)
- Concise response: 72 tokens (only thread content)

Using `CONCISE` saves ~⅓ of tokens. Anthropic found that LLMs perform better with formats matching their training data — natural text over complex structured formats.

#### 7. Pagination and Output Truncation

Large outputs confuse agents. Always implement pagination:

```json
{
  "name": "search_files",
  "parameters": {
    "query": {"type": "string"},
    "limit": {
      "type": "integer",
      "description": "Maximum results to return. Range 1-50. Defaults to 20.",
      "default": 20
    },
    "offset": {
      "type": "integer",
      "description": "Pagination offset for subsequent pages."
    }
  }
}
```

Return a `has_more` flag so the agent knows to paginate if needed:
```json
{
  "results": [...],
  "has_more": true,
  "total": 247,
  "next_offset": 20
}
```

#### 8. Security Considerations for Tool Design

When agents can execute code or access systems, security becomes critical:

**Guardrails at the tool layer, not just the prompt layer:**
```json
{
  "name": "execute_bash",
  "description": "Execute bash commands in the sandboxed environment.",
  "parameters": {
    "command": {
      "type": "string",
      "description": "The bash command to execute.",
      "examples": ["ls -la", "grep -r 'error' ./logs", "git status"]
    },
    "timeout_seconds": {
      "type": "integer",
      "description": "Maximum execution time. Defaults to 30. Max 120.",
      "default": 30,
      "maximum": 120
    }
  }
}
```

**Claude Code's approach**: Uses Claude Haiku (a smaller, faster model) to analyze bash commands before execution, catching obvious issues quickly. Sandboxing is mandatory — agents should never have direct access to production systems.

**Blocklist approach is brittle** — prefer allowlists where possible:
```json
{
  "name": "allowed_commands",
  "description": "Execute only safe, whitelisted shell commands.",
  "parameters": {
    "command": {
      "type": "string",
      "enum": ["ls", "cat", "grep", "git_status", "git_diff"],
      "description": "One of the allowed commands only."
    }
  }
}
```

---

### Claude Code's 14 Tools (Real ACI Implementation)

From reverse-engineering analysis by Jannes Klaas, Claude Code uses exactly 14 tools — a deliberately small set that proves the "fewer tools" principle:

| Category | Tools |
|----------|-------|
| Command line | `bash`, `glob`, `grep`, `ls` |
| File operations | `read`, `write`, `edit`, `multi_edit`, `notebook_read`, `notebook_edit` |
| Web | `web_search`, `web_fetch` |
| Control | `todo_write`, `task` |

**Design principles visible in Claude Code:**

1. **Simple while loop over complex state machines**: The agent loop is just `while(tool_use)` — it asks questions by outputting text without tool calls. No complex orchestration overhead.

2. **TODO lists for planning and state**: The `TodoWrite` tool is the agent's working memory:
```json
{
  "name": "TodoWrite",
  "input": {
    "todos": [
      {"id": "1", "content": "Analyze implementation", "status": "completed", "priority": "high"},
      {"id": "2", "content": "Implement feature", "status": "in_progress", "priority": "high"},
      {"id": "3", "content": "Test changes", "status": "pending", "priority": "medium"}
    ]
  }
}
```
The TODO list serves as:
- **Planning** — agent creates it at the start of a task
- **State tracking** — updates status as work progresses
- **Context preservation** — survives context window resets within a session

3. **Dynamic system reminders injected at loop boundaries**:
```xml
<system-reminder>
Your todo list has changed. DO NOT mention this explicitly. Contents:
[{...}]. Continue on with the tasks at hand.
</system-reminder>
```
The agent is reminded of its state without being told to output it explicitly.

4. **File discovery via glob/grep, not embedding-based retrieval**: Files are discovered progressively through exploration (`glob "**/*.ts"`, then `grep "function name"`), not pre-loaded via vector search. This is **context pulling** at the file level — the agent only loads what it needs.

5. **Two-tier security**: `Haiku` (fast, cheap) pre-screens bash commands before execution, trading some accuracy for speed.

---

### Model Context Protocol (MCP)

Anthropic's November 2024 open standard for connecting AI assistants to data sources:

- Standardizes tool definitions so agents can use tools from any MCP server
- Enables third-party integrations without per-tool implementation work
- Reduces the cognitive overhead of tool discovery and selection
- Allows tools to be namespaced and categorized automatically

**MCP + ACI synergy**: MCP provides the transport and discovery layer; ACI provides the design principles for *what makes a good tool*. MCP is the plumbing; ACI is the design philosophy.

Anthropic's code execution with MCP (Nov 2025) achieves up to **98.7% context overhead reduction** by using MCP tools instead of pre-loading code into context.

---

### The Feedback Loop: Evaluating and Improving Tools

Anthropic recommends treating tool development like a scientific process:

```
┌─────────────────────────────────────────┐
│  1. Build initial tool definitions       │
│         ↓                               │
│  2. Run agent evaluation on test tasks   │
│         ↓                               │
│  3. Analyze failure modes                │
│         ↓                               │
│  4. Let Claude analyze transcripts      │
│     ("What went wrong? Why?")            │
│         ↓                               │
│  5. Improve tool definitions            │
│         ↓                               │
│  6. Re-evaluate → repeat                │
└─────────────────────────────────────────┘
```

**Generating strong evaluation tasks** (these catch more failures):

❌ **Weak tasks** (too simple, single-step):
```
"Search logs for customer_id=9182"
"Schedule a meeting with jane@acme.corp next week."
```

✅ **Strong tasks** (multi-step, realistic, ambiguous):
```
"Customer ID 9182 was charged three times. Find all relevant log entries
and determine if other customers were affected. Escalate to billing
if the issue spans more than one customer."

"Schedule a meeting with Jane next week to discuss our Acme Corp project.
Check her calendar first, then send an invite. If she's unavailable,
find the next available slot and confirm with the backup time."
```

**Metrics to track per tool:**
- Tool call success rate (did it execute without errors?)
- Parameter correctness (were parameters well-formed?)
- Outcome correctness (did the tool call achieve the intended goal?)
- Token efficiency (how much context did the output consume?)
- Latency (does the tool introduce unacceptable delay?)

**Key finding from Anthropic**: Claude-optimized tools consistently outperformed "expert" human-written tools on held-out test sets. The agent is often better at improving its own tools than humans are at designing them.

---

### OpenHands ACI Implementation

The [OpenHands](https://github.com/All-Hands-AI/openhands-aci) project is an open-source ACI implementation for software development agents:

**Core tools:**
- `str_replace_editor` — multi-command file editing (view, create, str_replace, insert)
- `bash` — shell command execution with sandboxing
- `file_search` — glob-based file discovery

**Key design decisions:**
- Tree-sitter based linting integrated into the editor
- Diff generation handled server-side, not by the agent
- Shell commands are sandboxed with strict timeout and resource limits
- The agent never writes diffs — it provides intent; the tool implements the diff

This follows the ACI principle: **the tool should handle the mechanical complexity so the agent can focus on high-level reasoning**.

---

## 8. Context Pushing vs. Context Pulling

A fundamental architectural decision from Inngest's **"Context Engineering in Practice"** (Nov 2025) and the five critical lessons from **cubic's Paul Sanglé-Ferrière**.

### The Core Distinction

| | **Context Pushing** | **Context Pulling** |
|---|---|---|
| Who controls context | System pre-retrieves and delivers | LLM dynamically requests via tools |
| Metaphor | Pre-loading a travel guide before asking questions | Having a librarian on call to look things up |
| Typical pattern | RAG workflows | Agentic tool-use loops |
| Initiative | System | LLM |
| Data dependency | Predictable, bounded | Vast, unpredictable |

### When to Use Context Pushing

- Data domain is **well-defined and bounded** — questions about a known set of topics
- **Fast response time is critical** — no latency from tool-calling loops
- Context **fits within window limits** — the entire relevant dataset can be delivered
- **Reliability is paramount** — fewer failure points, simpler orchestration
- Task is question-answering or classification

### When to Use Context Pulling

- Data domain is **vast and unbounded** — thousands of files, open-ended exploration
- You **cannot predict what context will be needed** — the AI must discover
- **Context window limits are a hard constraint** — nothing fits pre-loaded
- **High autonomy is required** — the AI makes decisions based on what it discovers

### The Decision Framework

```
Is your data domain vast and unpredictable?
YES → Context Pulling
NO ↓
Can all relevant data fit in context window?
YES → Context Pushing
NO ↓
Is high autonomy required?
YES → Context Pulling
NO → Context Pushing with compression
```

### The Key Tradeoff (from cubic's Paul Sanglé-Ferrière)

> *"The tradeoff is reliability. Context pulling requires orchestrating multiple tool calls that may fail, which means you need infrastructure that handles retries and doesn't lose progress when something breaks. But the upside is better decisions. The AI gathers exactly the context it needs based on what it's actually seeing, not what you predicted it would need."*

---

### Five Critical Lessons for Context Engineering (from cubic)

These lessons emerged from cubic building a code review agent that handles hundreds of companies simultaneously, processing codebases of varying sizes and complexity.

#### Lesson 1: Context Pulling Wins for Unpredictable Domains

When cubic first started, they tried to manually guide the AI toward context they thought it needed for code reviews. This didn't work — the AI would discover new dependencies, patterns, and requirements that couldn't be predicted upfront.

> *"In the end, for those users and for us, it's usually best to let the model determine what context is necessary."*

The key insight: **you can't engineer context you don't know the model needs**.

#### Lesson 2: Fewer Tools Win

> *"Audit your tools ruthlessly. Every tool you add adds cognitive overhead. Prioritize tools the model already knows — terminal commands, standard APIs — over custom abstractions you invented."*

Tools the model already understands have lower friction:
- Terminal commands (`ls`, `grep`, `cat`) — model knows how to use them
- Standard APIs — model knows how to form queries
- Common file operations — model knows how to navigate

Custom abstractions require the model to learn a new interface, which adds cognitive load and error rate.

**The 10% rule**: Remove any tool used less than 10% of the time. Consolidate or remove tools that don't pull their weight.

#### Lesson 3: Plan for Deduplication at Execution Level, Not Output Level

A subtle but critical operational insight. In parallel agent systems, multiple agents often independently request the same context:

```
Agent A: needs auth.js → calls read_file("auth.js")
Agent B: needs auth.js → calls read_file("auth.js")  ← duplicate
Agent C: needs auth.js → calls read_file("auth.js")  ← duplicate
```

**Naive deduplication** (at output level):
- Agents all call read_file("auth.js")
- System deduplicates the results
- **Problem**: All API calls already happened; you wasted tokens on 2 redundant calls

**Smart deduplication** (at execution level):
- Before calling read_file("auth.js"), check if another agent already has it
- If yes, share the result
- If no, execute and share the result to all agents that need it
- **Benefit**: Eliminated the redundant API calls entirely

> *"You can't just deduplicate at the output level because by then you've already wasted the API calls and context gathering. You need intelligent batching at the execution level."*

#### Lesson 4: Rate Limits Are a First-Class Problem

This is often underestimated until you're running production agents at scale:

> *"By definition, when you're doing this sort of context pulling, the AI operates a lot faster than a human would... Scale that out at the level of multiple companies, one company pushing multiple PRs at the same time and over hundreds of different companies… managing rate limits has been a huge focus for us."*

**The noisy neighbor problem** (from the Inngest articles):
- One customer pushes 50 PRs simultaneously
- Their agents consume the entire API quota
- All other customers' agents slow down or fail
- **Solution**: Per-customer rate limiting, not just global rate limiting

**Burst handling**:
- Agents burst requests (fast: read file, read file, read file)
- APIs burst-limit on requests-per-second
- **Solution**: Implement request queuing and pacing, not just global throttling

**Cost projection**:
- Each PR review generates N tool calls
- N PRs simultaneously = N× tool calls
- **Solution**: Budget-aware parallelism — don't run 50 PR reviews in parallel if the cost would exceed the per-minute budget

#### Lesson 5: Observability Is Non-Negotiable

> *"Log every tool call with full inputs, outputs, and timing. Not just 'called search_codebase,' but the exact query, reasoning, results count, and duration. You need to reconstruct the decision tree: why did the agent read a particular file after that search? What triggered that choice?"*

**What to log per tool call:**
```
Tool: read_file
Input: {path: "src/auth.js", reason: "needed to understand JWT validation"}
Output: {lines: 47, truncated: false}
Duration: 23ms
Context_before: [search_codebase results for "JWT", grep results for "auth"]
Agent_reasoning: "I found JWT validation in the search results. Let me read the auth module to understand how it's implemented."
```

**Why this matters:**
- Without this, debugging a failing agent is guesswork
- You can see *why* the agent made a decision, not just what it decided
- You can identify patterns in tool misuse (agent always reads the wrong file type)
- You can measure token efficiency (is this tool returning too much?)

**The decision tree reconstruction** is the key capability this enables:
```
Agent read auth.js
  → Why? Because search_codebase returned it as top result for "JWT"
    → Why search for "JWT"? Because the PR touched authentication
      → How did agent know to search for "JWT"? Because... (traces back to initial analysis)
```

---

### Practical Code Pattern (Context Pulling with Inngest/AgentKit)

```tsx
import { createAgent, anthropic, createTool, createNetwork } from "@inngest/agent-kit"
import { z } from "zod"

const agent = createAgent({
  name: "Coding Agent",
  description: "An expert coding agent",
  system: `You are a coding agent help the user achieve the described task.
    Think step-by-step before you start. When you need to understand a file,
    use the readFiles tool. When you've completed a step, update your TODO list.`,
  model: anthropic({
    model: "claude-3-5-sonnet-latest",
    max_tokens: 4096,
  }),
  tools: [
    createTool({
      name: "readFiles",
      description: `Read files from the sandbox environment.
        Always use absolute paths starting from the root.
        Returns the full file content. Use for understanding existing code.`,
      parameters: z.object({
        path: z.string().describe("Absolute path to the file"),
      }),
      handler: async ({ path }, { network }) => {
        const sandbox = getSandbox(network);
        return await sandbox?.files.read(path);
      },
    }),
    createTool({
      name: "createOrUpdateFiles",
      description: `Create or update files in the sandbox.
        For new files, provide full content.
        For existing files, include the full new content (not just the diff).`,
      parameters: z.object({
        files: z.array(z.object({
          path: z.string(),
          content: z.string(),
        })),
      }),
      handler: async ({ files }, { network }) => {
        const sandbox = getSandbox(network);
        for (const { path, content } of files) {
          await sandbox?.files.write(path, content);
        }
        return `Updated ${files.length} files`;
      },
    }),
    createTool({
      name: "TodoWrite",
      description: `Manage your task tracking list.
        Use this to plan your work and track progress.
        Always update status when a task is completed.`,
      parameters: z.object({
        todos: z.array(z.object({
          id: z.string(),
          content: z.string(),
          status: z.enum(["pending", "in_progress", "completed"]),
          priority: z.enum(["high", "medium", "low"]).optional(),
        })),
      }),
      handler: async ({ todos }) => {
        // Store in agent state for persistence
        return `Updated ${todos.length} todo items`;
      },
    }),
  ],
});

const network = createNetwork({
  name: "coding-agent-network",
  agents: [agent],
  maxIter: 10,
  defaultRouter: ({ network }) => {
    if (network?.state.kv.has("task_summary")) {
      return; // Agent completed
    }
    return agent;
  },
});
```

**Key design choices visible here:**
- **Simple tools** — readFiles, createOrUpdateFiles, TodoWrite (model knows these patterns)
- **Semantic descriptions** — descriptions explain *when* to use each tool, not just what it does
- **Examples in descriptions** — agent knows common usage patterns from the description
- **Persistence via state** — TODO list survives context resets
- **Minimal tools** — only 3 tools, all high-value

---

### Multi-Model Context Compression Pattern

An important pattern from the Inngest AI Research Assistant: combining multiple specialized models to reduce context size while preserving information.

> *"To enable our AI Research Assistant to provide the most accurate and unbiased answer, our top 10 retrieved contexts are forwarded to 4 specialized models, again, in parallel:
> - The GPT-4 Analyst — extracts structured insights
> - The Claude Summarizer — creates coherent summaries
> - The Gemini Fact-Checker — verifies factual claims
> - The Mistral Classifier — categorizes and tags content

> This 'divide and conquer' approach is an efficient context-engineering pattern that 'compresses' the context into smaller ones that are easier to reason about."*

**The insight**: While LLM context windows keep increasing, LLM reasoning accuracy tends to diminish as context increases. Reference: arxiv.org/pdf/2502.05167. Build pipelines to refine and compress context along the way rather than dumping everything into a single prompt.

**Practical application**:
- Instead of: `llm.analyze(all_50_documents)`
- Use: `parallel(analyzer(all_10_docs), summarizer(all_10_docs), checker(all_10_docs), classifier(all_10_docs))` then `llm.synthesize(4_specialized_outputs)`

---

### Combining Context Pushing and Pulling: The Hybrid Approach

In practice, many systems use both patterns together:

```
┌──────────────────────────────────────────────────────────────┐
│                      HYBRID CONTEXT ARCHITECTURE              │
│                                                               │
│  User Query → [Context Pushing Layer]                         │
│                 │                                              │
│                 ├── Fast: Pre-retrieve known relevant sources │
│                 ├── Filter: Remove obviously irrelevant       │
│                 └── Compress: Summarize before passing        │
│                           │                                   │
│                           ↓                                   │
│              [Context Pulling Layer]                          │
│                 │                                              │
│                 ├── Agent receives pre-filtered context       │
│                 ├── Agent identifies gaps                      │
│                 ├── Agent pulls additional context via tools   │
│                 └── Agent synthesizes                          │
│                           │                                   │
│                           ↓                                   │
│                      Final Response                           │
└──────────────────────────────────────────────────────────────┘
```

**When to use this hybrid**:
- The domain has both known and unknown aspects
- You want the reliability of pre-retrieval with the flexibility of dynamic discovery
- Your agent has access to a large but structured knowledge base with an open-ended interface

---

## 9. Summary & Decision Framework

| Scenario | Recommended Pattern |
|----------|---------------------|
| Simple, linear task | Prompt chaining |
| Multiple distinct categories | Routing |
| Independent parallel subtasks | Parallelization |
| Complex, unpredictable tasks | Orchestrator-workers |
| Iterative refinement needed | Evaluator-optimizer |
| Open-ended autonomous tasks | Full agent loop with reflection |
| Multi-turn conversation | Sliding window + summarization |
| Long-horizon tasks | Hierarchical memory + episodic storage |
| Multiple specialized agents | Supervisor-worker with typed handoffs |
| Bounded data domain | Context Pushing |
| Vast/unpredictable data domain | Context Pulling |
| Well-known + discoverable domains | Hybrid (Pushing + Pulling) |

### The Unified Insight

Context engineering has emerged as a distinct discipline from prompt engineering:
- **Prompt engineering**: Focuses on *how* you communicate with the model — the words, structure, techniques
- **Context engineering**: Focuses on *what* information the model has access to — the data architecture and context curation

> *"Creating agentic systems is no longer about giving the LLM the right prompt but about providing it with a suitable set of tools, memory, and data to enable it to build its context — and managing how that context evolves over time."*
> — Inngest, "Context Engineering is Just Software Engineering for LLMs"

### Common Pitfalls

- **Context stuffing**: Passing too much irrelevant context hurts LLM recall more than it helps
- **Ignoring routing**: Every query through full retrieval is expensive and often unnecessary
- **Flat chunking**: Single-granularity chunks lose document-level context
- **No evaluation**: Without stage-specific metrics, debugging pipeline failures is guesswork
- **Over-engineering**: Complex multi-agent systems introduce complexity that simpler approaches can't justify
- **Tool proliferation**: Adding tools without auditing their usage creates cognitive overhead
- **Output-level deduplication**: Deduplicating after API calls already happened wastes tokens

The consistent advice across all practitioners is: **start simple, measure performance, and only add complexity when it demonstrably improves outcomes.**

---

## Sources

**Foundational:**
- [Anthropic: Building Effective Agents (Dec 2024)](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Writing Effective Tools for Agents (Sep 2025)](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

**Context Pushing vs. Pulling:**
- [Inngest: Context Engineering in Practice (Nov 2025)](https://www.inngest.com/blog/context-engineering-in-practice)
- [Inngest: Five Critical Lessons for Context Engineering](https://www.inngest.com/blog/five-lessons-for-context-engineering)
- [Inngest: Context Engineering is Just Software Engineering](https://www.inngest.com/blog/context-engineering-is-software-engineering-for-llms)
- [E2B: Replicating Cursor's Agent Mode with AgentKit](https://e2b.dev/blog/replicating-cursors-agent-mode-with-e2b-and-agentkit)
- [GitHub: Context Engineering Demo with Inngest](https://github.com/inngest/Context-Engineering-with-Inngest)

**Multi-Agent Patterns:**
- [Skywork AI: AI Agent Orchestration & Handoffs (Dec 2025)](https://skywork.ai/blog/ai-agent-orchestration-best-practices-handoffs)
- [LangGraph: Multi-agent patterns, state management](https://langchain-ai.github.io/langgraph/)

**Memory Architectures:**
- [Agent Memory Survey (Dec 2025)](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)
- [CoALA Framework: Cognitive Architectures for Language Agents](https://www.cognee.ai/blog/fundamentals/cognitive-architectures-for-language-agents-explained)
- [DeepLearning.AI: Agentic Design Patterns Part 2 — Reflection](https://www.deeplearning.ai/the-batch/agentic-design-patterns-part-2-reflection/)

**ACI Implementations:**
- [Claude Code Agent Design Analysis (Jannes Klaas)](https://jannesklaas.github.io/ai/2025/07/20/claude-code-agent-design.html)
- [OpenHands ACI (GitHub)](https://github.com/All-Hands-AI/openhands-aci)
