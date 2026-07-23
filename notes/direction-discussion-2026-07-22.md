# Wichy Direction Discussion — 2026-07-22

A brainstorming session covering six areas of the wichy project. This document
captures the discussion, conclusions, and open questions for future reference.

---

## 1. Core Tools vs. Extended Tools

### Problem
No clear distinction between tools that are fundamental to the agent loop and
tools that extend its reach. All tools are currently treated equally in the
loading mechanism. This makes it hard to reason about minimal configurations
(e.g. for small context models or read-only agents).

### Proposed Model: 3-Tier Tool Classification

**Tier 0 — Always-on core**
Tools the agent needs to function as an agent. Cannot be removed without
breaking the basic loop:
- `read_file`, `write_file`, `insert_lines`, `replace_text`
- `search_in_files`, `glob`, `list_files`, `tree`
- `bash`
- `todo`
- `ask_user_question`

**Tier 1 — Default-loaded extensions**
Tools with web GUI components or deeply integrated into the wichy workflow.
Loaded by default but explicitly removable:
- `duckdb_load`, `duckdb_query`, `duckdb_schema`, `duckdb_status`,
  `duckdb_persist`, `duckdb_reset`, `duckdb_manager` (web GUI data explorer)
- `graph_tools` (web GUI graph editor)
- `read_scratchpad`, `write_scratchpad` (shared scratchpad)
- `notes/*` (agent notebook web GUI)
- `task` (sub-agent delegation)
- Skill tools: `list_skills`, `search_skills`, `activate_skill`,
  `execute_skill_script`, `read_skill_file`

**Tier 2 — Opt-in extended tools**
Not loaded by default. Extend the agent's reach outside the local system:
- `fetch_webpage`, `search_ddg` (web research)
- `browser_*` (browser automation — 35KB of custom code, but an extension of
  reach, not core)
- MCP-served tools

### Key Points
- Bash is in Tier 0. Without it the agent is crippled for anything not covered
  by a dedicated tool (running tests, git, etc.).
- Read-only root agents should be possible (Tier 0 minus bash/write/insert/
  replace), similar to how Explore is a read-only sub-agent.
- Downstream tooling: MCP (already supported) + CLI-via-bash is a solid
  two-path strategy for users adding custom tools.

### Implementation Notes
- This is a deliberate refactor, not a quick change. Touches
  `tools/registry.py`, `tools/__init__.py`, `agent_builder.py`, and possibly
  the root agent desc format.
- Current mechanism: tools registered via `ToolMeta`, root agent desc `tools`
  property or CLI flags control what's loaded.
- A tiering system needs a way to classify each tool as Tier 0/1/2, and the
  loader needs to know which tiers to include by default.
- Worth planning carefully to avoid breaking changes.

### Status
Agreed on the model. Implementation deferred — needs careful planning.

---

## 2. Docker as a First-Class Citizen

### Problem
Users run wichy in Docker for isolation and autonomy, but Docker is a
"second-class citizen." The environment info block (`environment_info.py`)
only adds a single line: `"Running in Docker container: Yes"`. Everything
else (ephemerality, `/workspace`-only persistence, venv location) must be
communicated by the user every session. This is tedious and error-prone.

### Proposed Solution: Context-Aware `<env>` Block

When `settings.container` is true, inject enriched container-specific
guidance directly into the `<env>` block:

```
<env>
Working directory: /workspace
Is directory a git repo: Yes
Platform: linux
Running in Docker container: Yes
Today's date: 2026-07-22

You are running inside a Docker container. Key implications:
- Only /workspace is persisted across container restarts. Anything outside
  /workspace (including installed packages, temp files, home directory
  changes) will be lost on restart.
- The container provides isolation; you have elevated autonomy to take
  actions without human-in-the-loop, but changes outside /workspace are
  ephemeral.
- Use /workspace/venv for Python; do not install packages globally.
</env>
```

When NOT in a container, the block stays minimal (current behavior).

### Custom Container Message
Add a `container_context` section to `settings.yaml` so users with custom
setups can tailor the guidance. The default message covers the common case;
users override if their setup differs (e.g. additional persistent mounts).

### Design Principle
Make container context a **property of the environment**, not a user
directive. Agents already treat `<env>` blocks as authoritative context — we
just enrich what goes in there. Non-container users see no confusing
container-specific text.

### Status
Agreed. This is a **quick win** — low-risk, self-contained change to
`environment_info.py` + a new `settings.yaml` namespace. Ready to prototype.

---

## 3. Skills: System-Prompt Listing vs. Discovery

### Clarification
Two separate concepts that must not be conflated:
1. **Listing in system prompt** — whether the skill's name+description
   appears in the initial system prompt (via `skills_information()`).
2. **Activation** — the agent calling `activate_skill('napkin')` to load the
   full skill content. This is **always the agent's decision** for every
   skill.

The problem is solely about #1: which skills get listed in the system prompt
(token cost) vs. which are left for discovery (agent must call `list_skills`/
`search_skills` to know they exist).

### The Constraint
Skills whose descriptions contain "activate early" directives (napkin,
agent-notebook) **must** be in the system prompt. If they're not listed, the
agent won't know to activate them and won't discover them on its own —
there's no trigger for the agent to go looking for a "runbook curation" skill
before it's done any work.

### Proposed Solution: `core: true` Frontmatter

A `core: true` (or similar) frontmatter property marks skills that must be
listed in the system prompt. Everything else becomes discovery-only.

- `core: true` means: "this skill must be listed in the system prompt" —
  nothing about auto-activation. Activation is still fully the agent's call,
  driven by the description text.
- Reduces token bloat for skill-heavy users (only core skills in the prompt,
  rest discovered on-demand).
- The system prompt should contain a short directive making `list_skills` a
  default first action: *"Skills extend your capabilities. At the start of a
  session or when tackling an unfamiliar task, call `list_skills` to see what's
  available."*

### Tool Count
5 skill tools (`list_skills`, `search_skills`, `activate_skill`,
`execute_skill_script`, `read_skill_file`) feels reasonable — a coherent
toolkit. The token cost of tool *definitions* is a separate problem from
skill *descriptions* in the system prompt.

### Status
Agreed on the `core: true` concept. Implementation deferred.

---

## 4. System Prompt Token Bloat

### Problem
Two sub-problems:
- **Problem A**: System prompt text is long. The `root-agent-code-advanced`
  prompt is heavily inspired by Claude Code and is very verbose. The
  `<conditional>` blocks help (tool-specific guidance only appears if the
  tool is loaded) but the base text is still large.
- **Problem B**: Tool definitions are many. ~35 tools consume 15-20K tokens
  in specs. On a 32K context window, that's 50% gone before the user says
  anything. Small models get confused.

### Proposed Approaches

**For Problem A (prompt text):**
1. **Tiered system prompts per root agent.** Ensure small-model users know to
   use `root-agent-basic` (which has a tiny prompt). Maybe add a
   `root-agent-code-compact` variant.
2. **Lazy conditional expansion.** The `<conditional>` mechanism could go
   further: only include section X if a keyword appears in the user's first
   message. Harder to implement but high payoff.
3. **Progressive disclosure.** Start with minimal system prompt, inject
   guidelines after first user turn. Risky — agents might not internalize
   late-arriving guidance.

**For Problem B (tool definitions):**
1. **Tiered tool loading** (see section 1). Only load Tier 0 + Tier 1 core
   skills = ~15-20 tools instead of 35.
2. **Lean flag / minimal tool set.** A `--lean` flag or `root-agent-basic`
   variant that loads only Tier 0 and skips skill description injection.
   Gives small-model users a usable agent that fits in 32K.
3. **Predictive tool loading** (see section 6). Ship only predicted-needed
   tools based on the user's first message.

### Status
Multiple approaches identified. No single solution — likely a combination of
tiered loading + lean flag + predictive loading. Deferred.

---

## 5. Large Files

### Top Offenders

| File | Size | Assessment |
|---|---|---|
| `context/handler.py` | 35KB | Context management — many moving parts, core component. High risk to refactor. |
| `helpers/browser.py` | 35KB | Browser automation. Large but cohesive — a single SDK wrapper. Probably fine as-is. |
| `tools/data/api.py` | 26KB | DuckDB data API. Could split by tool. |
| `tools/task/base.py` | 24KB | Task/sub-agent system. |
| `cli/handlers.py` | 24KB | CLI command handlers. Natural split along command boundaries. |
| `root_agent/root_agent.py` | 21KB | Core agent loop. |
| `llm_backend.py` | 20KB | LLM backend abstraction. On the edge. |

### Approach
- **`context/handler.py`**: Owner is "afraid" to refactor — it's a core
  component with many moving parts. Safe approach: first map responsibilities
  via explorer agents to identify natural seams, then split incrementally
  with tests as safety net. Do NOT do a blind split.
- **`cli/handlers.py`**: Natural split point — one module per command group.
  Lower risk.
- **`browser.py`**: Large but cohesive. Splitting might not add value.
- Principle: split when there's clear organizational benefit (testability,
  readability, parallel development), not just to reduce line count.

### Status
Acknowledged. No action taken. `context/handler.py` is the high-value-but-
high-risk candidate.

---

## 6. Semantic Analysis R&D

### Direction
Add "back to basics" statistics and/or lightweight ML to enhance the user
experience. Must feel natural, not shoe-horned. Must be opt-in and local
(no external API calls).

### Phase 1: Context/Session Statistics (zero ML, immediate value)
- **Tool usage frequency per session** — which tools are actually used? This
  can drive the core/extended tool tiering *empirically* rather than by gut
  feel.
- **Session length distribution** — detect when the agent is spinning (long
  sessions with no resolution).
- **Token consumption breakdown** — system prompt vs. tool calls vs. user
  messages. Directly validates the "system prompt is too heavy" concern with
  data.

### Phase 2: Lightweight ML

**Topic modeling over conversation turns** (already started in a separate
project):
- Could power a "session digest" that tells the user what was accomplished
  at a glance, without a raw transcript.

**Predictive tool usage (Bayesian/Naive Bayes)** — the preferred approach:
- Given user message features (tokens), compute P(tool_x is needed).
- Naive Bayes: P(tools | message tokens). No neural networks, no embeddings.
- **Why Bayesian**: works with small training data (session history),
  interpretable, degrades gracefully (falls back to default tools with no
  data), naturally discrete data.
- **Pipeline**: collect (user_message_tokens, tools_used) pairs from past
  sessions → train Naive Bayes → at session start, compute P(tool_needed)
  for each tool → include tools above threshold → agent can still
  dynamically request tools if prediction was wrong.
- Directly addresses token bloat: on a 32K model, ship 8 predicted tools
  instead of 35.
- Same session data feeds both topic modeling and tool prediction.

### Status
R&D direction. No implementation yet. Bayesian tool prediction is the most
promising near-term ML feature.

---

## 7. Inter-Instance Agent Communication

### Concept
Allow multiple wichy instances on the same machine to discover and
communicate with each other. Use cases: cross-workspace collaboration,
delegation to instances with different access, multi-agent
simulation/brainstorming.

### Architecture: Shared Directory Discovery + File-Based Messaging

**Discovery**: Each wichy instance writes a descriptor file to a shared
directory on the host: `~/.wichy/instances/`. This directory is mounted into
containers. Instances scan the directory to discover peers.

**Discovery file contents** (corrected — no ports or API tokens):
- Instance ID (stable, derived from workspace path or UUID)
- Workspace path (human-readable context — what is this instance working on)
- Heartbeat timestamp (so peers can tell if alive or stale)
- Inbox path (where to drop messages for this instance)

**Communication**: File-based messaging. Instance A writes a JSON message
into instance B's inbox directory. Instance B polls its inbox and processes
incoming messages. Message file contains sender ID, message content, and
reply-to inbox path.

### Key Decisions
- **No HTTP API calls between instances.** Instances never expose their API
  to peers. All communication is file-based through the shared directory.
- **No separate MCP message bus server.** Would require too much new code.
  The filesystem-based approach reuses existing infrastructure.
- Works container→host→container as long as `~/.wichy/instances/` is mounted
  into containers.
- Async by nature — messages wait in inbox until recipient polls. Latency
  (polling interval) is acceptable for agent collaboration.
- Discovery file does NOT contain ports or API tokens (corrected from
  initial suggestion).

### Open Questions
- Authentication/trust between peers (shared token? per-instance keys?)
- Concurrency: what if a peer is busy?
- Message format standardization
- How the agent sees incoming messages in its conversation context

### Status
Conceptual. Resonates with the direction. Not yet prototyped. Simplest
first version: file-based messaging through shared `~/.wichy/instances/`
directory, no HTTP, no MCP server.

---

## Priority Assessment

| Area | Effort | Risk | Value | Ready? |
|---|---|---|---|---|
| Docker env enrichment | Low | Low | High | **Yes — quick win** |
| Skills core vs discovery | Medium | Low-Med | Medium | Design agreed, impl deferred |
| System prompt reduction | Medium-High | Medium | High (small models) | Multiple approaches, needs combination strategy |
| 3-tier tool classification | High | High (breaking changes) | High | Needs careful planning |
| Large file splitting | Medium-High | Medium (context/handler.py) | Medium | context/handler.py needs explorer survey first |
| Semantic analysis R&D | Medium | Low (opt-in) | High (long-term) | R&D, start with statistics |
| Inter-instance comm | High | Medium | High (long-term) | Conceptual, needs scoping |

---

*Session 28. Notebook entries saved. Discussion only — no code changes made.*