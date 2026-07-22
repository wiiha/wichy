# Wichy — Agentic LLM Framework

Wichy is a **local-first agentic LLM framework** implementing Simon Willison's definition: _"An LLM agent runs tools in a loop to achieve a goal."_ It provides a REPL, a web chat interface, a server API, a comprehensive tool ecosystem, a skills system, lifecycle hooks, and support for multiple LLM backends.

---

## Quick Start

```bash
# Install
pip install -e .

# Start with the default root agent
wichy

# Specify a model
wichy --model-str ollama/llama3.2

# Resume a previous conversation
wichy --last-ctx
wichy --load-ctx .wichy/contexts/2026-07-04_1234567890.jsonl

# List available resources
wichy ls tools
wichy ls ra
wichy ls skills
wichy ls sa

# Create a new project-local skill
wichy new skill --name my-skill

# Run in pipeline (headless) mode
wichy --prompt "Summarize the README"

# Run in server-only mode
wichy server --port 7891
```

---

## Architecture

### Agent Hierarchy

```
AgentCore  (abstract base — shared tool execution logic)
  ├── RootAgent  (main REPL/session agent)
  │     process() → call LLM → handle_tools() → loop until done
  │
  └── TaskAgent  (sub-agent for delegated multi-step work)
          Spawned via the `task` tool. Fresh isolated context and history.
```

`AgentCore` (`src/wichy/agent/core.py`) provides the shared `_tool_call()`, `_handle_tools_base()`, `_fix_multimodal_context()`, and `_get_tool_definitions()` methods. Subclasses (`RootAgent`, `TaskAgent`) override `_log()` and `_log_dict()` for their respective console instances.

### Execution Flow

The agent loop follows this cycle:

```
User Input  →  RootAgent.process()  →  Append user message
                                     ↓
                               LLM API call
                                     ↓
                    ┌────────────────────────────────────┐
                    │  Response handling                 │
                    │  • finish_reason == "tool_calls"?  │
                    │    → execute tools, loop back      │
                    │  • otherwise: stream/display reply │
                    └────────────────────────────────────┘
```

**Step-by-step:**

1. **User Input** → REPL receives input from `prompt_toolkit`
2. **RootAgent.process()** → Runs `PRE_USER_MESSAGE` hooks, appends the user message
3. **LLM Call** → Sends context and tool definitions to the model
4. **Response Handling**:
   - If `finish_reason == "tool_calls"`: execute tools, append results, loop back to LLM
   - Otherwise: append final assistant entry, run `PRE_RESPONSE_TO_USER` hooks, display response

### TaskAgent Spawning & History

When the `task` tool is invoked, a `TaskAgent` is spawned:

```python
task_agent = TaskAgent(
    agent_definition=definition,
    prompt="...",
    model="...",
    all_tools_not_instantiated=[...],
    max_turns=...,
)
result = task_agent.run()
```

- **Isolated Context**: each task agent gets its own `ContextHandler` under `.wichy/contexts/task_agents/`
- **Limited Tools**: only tools specified by the agent definition are available
- **Recursive Prevention**: the `task` tool excludes itself from sub-agent tool lists
- **History Registry**: stopped task agents are stored as lightweight `TaskAgentHistoryEntry` metadata, accessible via the server API
- **Result Return**: the final response is returned to the calling RootAgent

Built-in task agent types include `bash`, `explore`, `general-purpose`, `web-research`, and `data-analysis`. Custom definitions can be added under `.wichy/sub_agents/` or `~/.wichy/sub_agents/`.

### Context Persistence Flow

All context mutations are persisted atomically as newline-delimited JSON (JSONL) in `.wichy/contexts/`:

```
Context.append(message)
      │
      ▼
JsonlContext._write()
      │
      ├── Write to temp file (.tmp)
      ├── fsync() for durability
      └── Atomic rename to .jsonl
```

Each line is either a `"message"` entry (sent to the LLM) or a `"log"` entry (session metadata, never sent to the LLM). Every entry carries an auto-incremented `_tick` field for stale detection.

**JSONL Format:**

```jsonl
{"_tick": 1, "role": "system", "content": "...", "type": "message"}
{"_tick": 2, "role": "user", "content": "...", "type": "message"}
{"_tick": 3, "role": "assistant", "tool_calls": [...], "type": "message"}
{"_tick": 4, "role": "tool", "content": "...", "type": "message"}
```

### Core Components

```
┌──────────────────────────────────────────────────────────────┐
│                     REPL (repl.py)                           │
│           PromptSession with history, slash commands         │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                      RootAgent                               │
│   process(line) → append user msg → call LLM → handle_tools  │
│                   → loop until finish_reason != "tool_calls" │
└──────────────────────────┬───────────────────────────────────┘
                           │
      ┌────────────────────┼────────────────────┬──────────────┐
      │                    │                    │              │
┌─────▼─────┐   ┌──────────▼───────────┐  ┌─────▼─────┐  ┌─────▼────┐
│  Tools    │   │   Skills System      │  │ Context   │  │Web Server│
│ (37)      │   │  .wichy/skills/      │  │  (JSONL)  │  │  :7891   │
│ auto-reg  │   │  ~/.wichy/skills/    │  │           │  │          │
└───────────┘   └──────────────────────┘  └───────────┘  └──────────┘
```

---

## Tool System

### Tool Registry

Tools self-register via the `ToolMeta` metaclass — any class inheriting from `BaseTool` with a `name` is automatically added to the registry on import. No manual registration is needed.

```python
# src/wichy/tools/my_tool.py
from wichy.tools.base import BaseTool, ParametersModel

class MyParams(ParametersModel):
    query: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "Short description"
    description_long: str | None = None  # passed to LLM if present
    parameters_model = MyParams

    def execute(self, query: str) -> str:
        return f"Result for: {query}"

# src/wichy/tools/__init__.py
from wichy.tools.my_tool import MyTool  # auto-registers on import
```

### Tool Categories

#### File Operations

| Tool              | Class               | Description                                                                                                     |
| ----------------- | ------------------- | --------------------------------------------------------------------------------------------------------------- |
| `read_file`       | `ReadFileTool`      | Read file contents; supports images via base64 multimodal conversion, offset/limit, non-printable chars         |
| `write_file`      | `WriteFileTool`     | Write or create files; auto-creates parent dirs                                                                 |
| `replace_text`    | `ReplaceTextTool`   | Targeted before/after string replacement; occurrence control                                                    |
| `insert_lines`    | `InsertLinesTool`   | Insert content at 1-indexed line offset; appends at EOF if offset exceeds file length                           |
| `list_files`      | `ListFilesTool`     | Directory listing                                                                                               |
| `glob`            | `GlobTool`          | Pattern-based file matching; sorted newest-first; venv-excluded                                                 |
| `search_in_files` | `SearchInFilesTool` | Ripgrep-powered regex search; `content`/`files_with_matches`/`count` modes; oversize guard; per-line truncation |

#### Shell

| Tool   | Class      | Description                                                                                             |
| ------ | ---------- | ------------------------------------------------------------------------------------------------------- |
| `bash` | `BashTool` | Execute shell commands; destructive commands (write, delete, network) trigger human-verification prompt |

#### Task Delegation

| Tool   | Class           | Description                                                                                                       |
| ------ | --------------- | ----------------------------------------------------------------------------------------------------------------- |
| `task` | `TaskAgentTool` | Launch a sub-agent for multi-step work; prevents infinite recursion by excluding itself from sub-agent tool lists |

Predefined sub-agent types:

| Type              | Available Tools                                                                                                                                                             | Purpose                                          |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `Bash`            | `bash`                                                                                                                                                                      | Git ops, terminal tasks                          |
| `Explore`         | `read_file`, `glob`, `search_in_files`, `list_files`, `tree`                                                                                                                | Fast codebase exploration                        |
| `web-research`    | `web_fetch`, `web_search`                                                                                                                                                   | Web research; "lite research" for quick overview |
| `data-analysis`   | `duckdb_load`, `duckdb_query`, `duckdb_schema`, `duckdb_status`, `duckdb_persist`, `duckdb_load_db`, `duckdb_reset`, `glob`, `list_files`, `read_file`, `ask_user_question` | SQL-based data analysis with DuckDB              |
| `general-purpose` | `ask_user_question`, `bash`, `read_file`, `glob`, `search_in_files`, `insert_lines`, `list_files`, `replace_text`, `todo`, `web_fetch`, `web_search`, `write_file`          | Complex multi-step research and coding tasks     |

#### Data / DuckDB

| Tool             | Class               | Description                                                             |
| ---------------- | ------------------- | ----------------------------------------------------------------------- |
| `duckdb_load`    | `DuckDBLoadTool`    | Load CSV/Parquet/JSON/JSONL into session tables; auto-detects format    |
| `duckdb_query`   | `DuckDBQueryTool`   | Execute SQL; configurable row limit; random-sample mode                 |
| `duckdb_schema`  | `DuckDBSchemaTool`  | Inspect table schemas (columns, types, row counts)                      |
| `duckdb_status`  | `DuckDBStatusTool`  | Show session state (tables, sources, in-memory vs. persisted)           |
| `duckdb_persist` | `DuckDBPersistTool` | Persist in-memory database to disk via ATTACH + CREATE OR REPLACE TABLE |
| `duckdb_load_db` | `DuckDBLoadDBTool`  | Reload a persisted `.duckdb` file                                       |
| `duckdb_reset`   | `DuckDBResetTool`   | Clear all loaded tables                                                 |

#### Web & Browser (Playwright-backed)

| Tool                 | Class                 | Description                                                                                                   |
| -------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------- |
| `web_search`         | `WebSearchTool`       | DuckDuckGo search; responses must include "Sources:" citation block                                           |
| `web_fetch`          | `FetchWebPageTool`    | Fetch webpage as markdown; Playwright-powered; `wait_until` options (`load`/`domcontentloaded`/`networkidle`) |
| `browser_navigate`   | `NavigateTool`        | Navigate to URL; return page title                                                                            |
| `browser_status`     | `BrowserStatusTool`   | Get current URL and page title                                                                                |
| `browser_page_info`  | `BrowserPageInfoTool` | Get structured page info: URL, title, meta description, links count, headings outline                         |
| `browser_screenshot` | `ScreenshotTool`      | Viewport or full-page PNG screenshot; save to file or base64                                                  |
| `browser_raw`        | `BrowserRawTool`      | Execute raw Playwright Page API expressions; AST-evaluated with auto-await                                    |
| `browser_act`        | `BrowserActTool`      | Declarative browser actions: click by text, fill forms by name/placeholder/id, wait for CSS selectors         |

#### Graph Tools

| Tool           | Class             | Description                                                                                                      |
| -------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------- |
| `create_graph` | `CreateGraphTool` | Create node-edge graphs from simple `## Nodes:` / `## Edges:` text format; hex colors; saves to `.wichy/graphs/` |
| `read_graph`   | `ReadGraphTool`   | Read saved graph JSON; edge-list output; fallback to `latest.json`                                               |
| `list_graphs`  | `ListGraphsTool`  | List saved graphs with file size and date                                                                        |

#### User Interaction

| Tool                | Class                 | Description                                                                                               |
| ------------------- | --------------------- | --------------------------------------------------------------------------------------------------------- |
| `ask_user_question` | `AskUserQuestionTool` | TUI dialog prompts (radio or checkbox); auto-appends "Other" option; `needs_user_attention` bell          |
| `todo`              | `TodoTool`            | Session-scoped task list; states: `PENDING` → `IN_PROGRESS` → `COMPLETED`; single in-progress enforcement |

#### Notes & Scratchpad

| Tool               | Class                 | Description                                          |
| ------------------ | --------------------- | ---------------------------------------------------- |
| `read_scratchpad`  | `ReadScratchpadTool`  | Read the pinned scratchpad note                      |
| `write_scratchpad` | `WriteScratchpadTool` | Save a markdown scratchpad note; auto-pins as active |

#### Skills Tools

| Tool                   | Class                    | Description                                                                               |
| ---------------------- | ------------------------ | ----------------------------------------------------------------------------------------- |
| `list_skills`          | `ListSkillsTool`         | List all skills with tags and script counts                                               |
| `search_skills`        | `SearchSkillsTool`       | Search skills by keyword (name, description, tags, content)                               |
| `activate_skill`       | `ActivateSkillTool`      | Get full skill details: markdown content, metadata, scripts list                          |
| `execute_skill_script` | `ExecuteSkillScriptTool` | Run a skill script; human verification unless in `safe_scripts`; blocked in pipeline mode |
| `read_skill_file`      | `ReadSkillFileTool`      | Read skill reference or asset files                                                       |

#### Result Offload

| Tool           | Class             | Description                                           |
| -------------- | ----------------- | ----------------------------------------------------- |
| `query_result` | `QueryResultTool` | Query an offloaded tool result using natural language |

### Human Verification & API Verification

`BashTool` classifies many shell commands as safe or destructive. Destructive commands (`rm`, `dd`, `mkfs`, etc.), dangerous flags, and redirect/pipe operators trigger a y/n prompt before execution. Verification is skipped in pipeline mode or when `WICHY_SKIP_HUMAN_VERIFICATION` is set.

For the **server API**, every tool inherits `needs_verification_in_api = True` by default. Read-only and safe tools explicitly opt out (`needs_verification_in_api = False`). State-mutating tools such as `write_file`, `replace_text`, `insert_lines`, `bash`, `browser_act`, `browser_raw`, `task`, `todo`, `ask_user_question`, and `execute_skill_script` retain the default `True` and require a cooperative `verified=true` flag when executed through the API.

**Note:** The source tree also contains `TreeTool` and `PingTool`, but they are not imported by default and therefore not available to agents unless explicitly imported.

---

## Context Management

### Persistence

Conversations are stored as newline-delimited JSON (JSONL) in `.wichy/contexts/`:

```
{YYYY-MM-DD}_{unix_timestamp}[_{custom_suffix}].jsonl
```

**Atomic writes**: all mutations use a `temp → rename` pattern to avoid corruption on crash.

### Context Operations

| Method                               | Effect                                                    |
| ------------------------------------ | --------------------------------------------------------- |
| `append(msg)` / `add(role, content)` | Append a message                                          |
| `drop(n)`                            | Remove last _n_ messages                                  |
| `replace_all(messages)`              | Atomic bulk replacement                                   |
| `update_message(i, msg)`             | Edit message by index                                     |
| `delete_message(i)`                  | Delete message by index                                   |
| `truncate_message(i, max_chars)`     | Truncate + store original in `_truncated_from`            |
| `expand_message(i)`                  | Restore truncated message                                 |
| `start_watching()`                   | Background polling for external changes (web editor sync) |

### Slash Commands

| Command            | Description                                           |
| ------------------ | ----------------------------------------------------- |
| `/btw <question>`  | One-shot sandboxed question via a temporary RootAgent |
| `/reset`           | Wipe context completely                               |
| `/compact`         | Summarize context (LLM-generated summary + compact)   |
| `/drop`            | Remove last message                                   |
| `/status`          | Show token count and auto-compact threshold           |
| `/logging on\|off` | Toggle verbose logging                                |
| `/exit`            | Exit the REPL                                         |

### Auto-Compaction

When `--auto-compact <n>` is set, `RootAgent` tracks `current_prompt_tokens` each turn. When the threshold is reached, auto-summarization triggers: a new context is created with an LLM-generated summary of the old one.

---

## Skills System

Skills are user-defined knowledge packages. The loader merges two directories:

| Location           | Purpose                                                          |
| ------------------ | ---------------------------------------------------------------- |
| `.wichy/skills/`   | Project-local skills (new skills created with `wichy new skill`) |
| `~/.wichy/skills/` | Shared/default skills (`wichy install skills`)                   |

Project-local skills take precedence when names collide.

```
.wichy/skills/my-skill/
├── skill.md        # Required: YAML frontmatter + markdown content
├── references/     # Optional: reference documents
├── assets/         # Optional: templates, config files
└── scripts/        # Optional: executable scripts
```

**skill.md format:**

```markdown
---
name: my-skill
description: What this skill does
metadata:
  tags: [tag1, tag2]
safe_scripts: [safe_script.sh]
---

# Skill Knowledge

Your knowledge content here...
```

Skills are auto-discovered by `SkillLoader` and their summaries are injected into the system prompt at startup. The agent can also query skills at runtime via the skills tools. `SkillReloader` uses `watchdog` with a 1-second debounce to reload skills automatically when files change.

- **Safe scripts**: declared in `skill.md` frontmatter (`safe_scripts:`) or as a fallback in `skill.json`; listed scripts bypass human verification.
- **Inactive skills**: setting `metadata.inactive: true` in `skill.md` hides the skill from discovery and use.

---

## Hooks System

Hooks provide a mechanism to intercept and modify tool execution and lifecycle events.

### Hook Decorators

```python
from wichy.hooks import pre_tool, post_tool, HookResult, HookContext

@pre_tool("bash")
def check_bash_command(ctx: HookContext) -> HookResult:
    if "rm -rf" in ctx.input_args.get("command", ""):
        return HookResult.deny("Destructive command not allowed")
    return HookResult.approve()

@post_tool("read_file")
def redact_secrets(ctx: HookContext) -> HookResult:
    output = ctx.output.replace("secret", "[REDACTED]")
    return HookResult.modify_output(output)
```

### Hook Actions

| Action        | Method                       | Description                         |
| ------------- | ---------------------------- | ----------------------------------- |
| Approve       | `HookResult.approve()`       | Allow execution to proceed          |
| Deny          | `HookResult.deny("reason")`  | Block execution with error message  |
| Modify Input  | `HookResult.modify_input()`  | Transform tool arguments (pre-tool) |
| Modify Output | `HookResult.modify_output()` | Transform tool output (post-tool)   |
| Log           | `HookResult.log()`           | Informational only                  |

### Priority-Based Execution

Hooks execute in priority order (lower = earlier). Default priority is 50.

### Lifecycle Hook Types

- `SESSION_START`, `SESSION_END`
- `CONTEXT_RESET_PRE`, `CONTEXT_RESET_POST`
- `CONTEXT_COMPACT_PRE`, `CONTEXT_COMPACT_POST`
- `PRE_USER_MESSAGE`, `PRE_RESPONSE_TO_USER`

Lifecycle hooks are mostly informational; `PRE_RESPONSE_TO_USER` hooks may modify the final assistant message.

### Hook File Locations

Hooks are loaded from two locations in order:

1. **User-global**: `~/.wichy/hooks.py`
2. **Project-local**: `.wichy/hooks.py`

When both files exist, project-local hooks can add to or override user-global hooks.

---

## LLM Backends

Configured via `--model-str`:

| Backend    | Format                             | Example                           |
| ---------- | ---------------------------------- | --------------------------------- |
| Ollama     | `ollama/<model>`                   | `ollama/llama3.2`                 |
| llama.cpp  | `llama_cpp/<model>`                | `llama_cpp/model`                 |
| OpenRouter | `open_router/<model>`              | `open_router/anthropic/claude-3`  |
| Generic    | `generic/<host>[:<port>]##<model>` | `generic/localhost:8080##llama-3` |
| Config     | `config/<alias-or-path>`           | `config/my-local-llm`             |

**Config backends** allow defining arbitrary OpenAI-compatible endpoints in `settings.yaml` without adding hard-coded backend types. Define backends under a `backends` namespace:

```yaml
# ~/.wichy/settings.yaml or ./.wichy/settings.yaml
backends:
  my-local-llm:
    base_url: "http://localhost:8080/v1"
    model: "llama-3-70b"
    api_key: "${MY_API_KEY}" # env var interpolation supported
    extra_body: # optional, forwarded to the API
      provider:
        allow_fallbacks: true
```

Then use `wichy -m config/my-local-llm`. If no `api_key` is set, falls back to `OPENAI_API_KEY`, then `sk-generic`. Add backends via `wichy new backend --name <alias> --base-url <url> --model <model>`.

You can also reference a standalone JSON/YAML config file directly: `wichy -m config//path/to/config.json`.

Environment variables:

| Variable                   | Default                     |
| -------------------------- | --------------------------- |
| `WICHY_OLLAMA_BASE_URL`    | `http://localhost:11434/v1` |
| `WICHY_LLAMA_CPP_BASE_URL` | `http://localhost:8080`     |
| `OPEN_ROUTER_API_KEY`      | —                           |
| `OPENAI_API_KEY`           | —                           |

### Reasoning / Thinking Extraction

Reasoning content is extracted from the OpenAI SDK `model_extra` dict, checking `reasoning` then `reasoning_content`. If `finish_reason == "stop"` and `content` is empty but reasoning exists, Wichy synthesizes display content from the reasoning. Reasoning is preserved in context for both tool-call and final assistant responses.

---

## Web Interface

Wichy starts a Flask server on port 7891 (auto-incremented if busy) in a background daemon thread. Disable with `--no-server`.

### Landing Page — `/`

Links to all available tool GUIs.

### Chat — `/chat/`

A web chat UI with history, verification/question handling, and send/steer endpoints. The chat web module proxies requests to the main server API. A status line above the message list polls the server event stream (`/server/api/events`) to show what the agent is doing in real time — thinking, calling tools, running task agents, awaiting approval, etc.

### Notes Editor — `/tools/notes/`

EasyMDE-based markdown editor for the user's notes.

| Route                              | Description                     |
| ---------------------------------- | ------------------------------- |
| `GET /`                            | Notes UI                        |
| `GET /api/notes`                   | List all notes                  |
| `POST /api/notes`                  | Create note                     |
| `GET/PUT/DELETE /api/notes/<slug>` | Read/update/delete note         |
| `POST /api/notes/set-scratchpad`   | Set or clear active scratchpad  |
| `GET /api/scratchpad-status`       | Current scratchpad slug + title |

### Context Editor — `/tools/context/`

Live editing of the conversation context; syncs with REPL via file watching.

| Route                                 | Description                           |
| ------------------------------------- | ------------------------------------- |
| `GET /`                               | Context editor UI                     |
| `GET /api/status`                     | Token count, message count, threshold |
| `GET /api/messages`                   | Get all messages                      |
| `PUT /api/messages`                   | Atomic bulk replace                   |
| `PUT /api/messages/<index>`           | Edit single message                   |
| `DELETE /api/messages/<index>`        | Delete single message                 |
| `POST /api/drop`                      | Drop last _n_ messages                |
| `POST /api/messages/<index>/truncate` | Truncate message                      |
| `POST /api/messages/<index>/expand`   | Restore truncated message             |

### Graph Editor — `/tools/graph/`

Vis.js-based graph editor for node-edge visualization.

| Route                      | Description       |
| -------------------------- | ----------------- |
| `GET /`                    | Graph editor UI   |
| `GET /api/list`            | List saved graphs |
| `GET /api/load/<filename>` | Load a graph      |
| `POST /api/save`           | Save graph        |

### Data Explorer — `/tools/data/`

DuckDB-backed data explorer UI.

Server logs: `.wichy/logs/server.log`

---

## Server API

In addition to the web UIs, Wichy exposes a programmatic HTTP API (prefix `/server/api`).

| Method | Route                                      | Description                                                       |
| ------ | ------------------------------------------ | ----------------------------------------------------------------- |
| `GET`  | `/server/api/messages`                     | Captured console messages                                         |
| `POST` | `/server/api/messages`                     | Inject a line into the input queue                                |
| `POST` | `/server/api/steer`                        | Steer the root agent                                              |
| `GET`  | `/server/api/verifications`                | List pending human verifications                                  |
| `POST` | `/server/api/verifications/<vid>`          | Respond to a verification                                         |
| `GET`  | `/server/api/questions`                    | List pending interaction questions                                |
| `POST` | `/server/api/questions/<qid>`              | Answer a question                                                 |
| `GET`  | `/server/api/root/context`                 | Root agent context entries                                        |
| `GET`  | `/server/api/root/status`                  | Root agent model, tokens, threshold                               |
| `GET`  | `/server/api/slashcommands`                | Available slash commands                                          |
| `GET`  | `/server/api/sub-agents`                   | Running task agents; `?include_history=1` includes stopped        |
| `GET`  | `/server/api/sub-agents/<id>`              | Single task agent status                                          |
| `POST` | `/server/api/sub-agents/<id>/steer`        | Steer a task agent                                                |
| `POST` | `/server/api/sub-agents/<id>/stop`         | Request task agent stop                                           |
| `GET`  | `/server/api/sub-agents/<id>/context`      | Task agent context; falls back to history if stopped              |
| `GET`  | `/server/api/tools`                        | List root-agent tools with schemas                                |
| `POST` | `/server/api/tools/execute`                | Execute a tool; `verified=true` required for state-mutating tools |
| `POST` | `/server/api/tools/inject`                 | Inject a stored manual tool result into root context              |
| `GET`  | `/server/api/tools/results`                | List stored manual tool results                                   |
| `GET`  | `/server/api/events`                       | Session event stream; `?since_id=N&limit=M` for polling           |
| `POST` | `/server/api/events/clear`                 | Clear the root session event log                                  |
| `GET`  | `/server/api/sub-agents/<id>/events`       | Per-agent event stream                                            |
| `POST` | `/server/api/sub-agents/<id>/events/clear` | Clear that agent's event log                                      |

**Authentication:** Wichy currently has no built-in API authentication. The server binds to `127.0.0.1` by default and relies on local-host isolation.

**Health check:** `GET /health` → `{"status": "ok"}`

---

## MCP Host

Wichy can act as an **MCP host**, connecting to MCP servers and exposing their tools as native Wichy tools.

- Configure servers in `~/.wichy/mcp_servers.json` or `.wichy/mcp_servers.json`
- `MCPManager` discovers servers and proxies tools via `MCPToolProxy`
- An asyncio event loop runs in a daemon thread (`MCPAsyncBridge`) so synchronous Wichy code can call async MCP clients
- Proxied tools inherit `needs_verification_in_api = True` by default and must opt out explicitly
- Use `--no-mcp` to skip MCP discovery

---

## Root Agents

Root agents define the agent's personality and system prompt via markdown frontmatter:

```markdown
---
name: my-agent
description: A helpful coding assistant
model: ollama/llama3.2
tools: [read_file, write_file, bash, search_in_files, glob]
include_env_info: false
include_skills: true
---

[System prompt content here]
```

Built-in agents: `root-agent-basic`, `root-agent-code-advanced` (default).

Custom agents live in `~/.wichy/root_agent_defs/` or `.wichy/root_agent_defs/`. The CLI `--root-agent-description`/`-r` selects the active agent.

Tool availability is gated by: (1) CLI `--tools`/`--not-tools` flags, (2) the root agent's `tools` frontmatter field, (3) the `--not-tools` exclusion list.

---

## Modes

### REPL Mode (default)

Interactive loop via `prompt_toolkit` with persistent history, auto-suggest, slash command completion, and a background web server.

### Pipeline Mode (`--prompt "..."`)

Single-shot, non-interactive execution. Bypasses the REPL; sends the user's prompt as a single user message. Injects a `[System note: Running in pipeline mode…]` preamble. Final response printed to stdout, then exits with code 0. Human-verification-decorated tools and non-safe skill scripts raise `PermissionError` immediately rather than prompting.

### Server Mode (`wichy server [--port N] [--no-chat]`)

Runs the Flask server in the foreground without the REPL. Can run with or without the chat web UI. Uses `ServerInteractionProvider` and `ServerVerificationProvider` for remote API interactions.

### Context Loading

```bash
wichy --load-ctx path/to/file.jsonl   # specific file
wichy --last-ctx                       # most recent file
```

---

## Configuration

Settings use `pydantic-settings` with `WICHY_` env prefix. Values are layered:

1. Environment variables (`WICHY_*`)
2. `.env` file
3. `~/.wichy/settings.yaml`
4. `./.wichy/settings.yaml` (highest priority)

Most project-local paths live under `.wichy/` (and home paths under `~/.wichy/`). Directory suffixes are configured via their `*_DIR_NAME` env vars; `contexts_dir` and `wichy_home` accept full paths.

| Setting                             | Default                        | Description                                            |
| ----------------------------------- | ------------------------------ | ------------------------------------------------------ |
| `WICHY_WICHY_HOME`                  | `~/.wichy`                     | Base home directory                                    |
| `WICHY_CONTEXTS_DIR`                | `.wichy/contexts/`             | Conversation storage (full Path)                       |
| `WICHY_SKILLS_DIR_NAME`             | `skills`                       | Suffix for skills dirs under `.wichy/` and `~/.wichy/` |
| `WICHY_GRAPHS_DIR_NAME`             | `graphs`                       | Suffix for graphs dir under `.wichy/`                  |
| `WICHY_NOTES_DIR_NAME`              | `notes`                        | Suffix for notes dir under `.wichy/`                   |
| `WICHY_LOGS_DIR_NAME`               | `logs`                         | Suffix for logs dir under `.wichy/`                    |
| `WICHY_ROOT_AGENT_DEFS_DIR`         | `root_agent_defs`              | Suffix for local root agents under `.wichy/`           |
| `WICHY_SUB_AGENT_DEFS_DIR`          | `sub_agents`                   | Suffix for local sub-agent defs under `.wichy/`        |
| `WICHY_BROWSER_HEADLESS`            | `true`                         | Browser headless mode                                  |
| `WICHY_SERVER_HOST`                 | `127.0.0.1`                    | Web/API server bind                                    |
| `WICHY_SERVER_PORT`                 | `7891`                         | Web/API server port                                    |
| `WICHY_OLLAMA_BASE_URL`             | `http://localhost:11434/v1`    | Ollama endpoint                                        |
| `WICHY_LLAMA_CPP_BASE_URL`          | `http://localhost:8080`        | llama.cpp endpoint                                     |
| `WICHY_OPENROUTER_BASE_URL`         | `https://openrouter.ai/api/v1` | OpenRouter endpoint                                    |
| `WICHY_TASK_TOOL_MODEL_STR`         | —                              | Override task-tool model                               |
| `WICHY_QUERY_RESULT_TOOL_MODEL_STR` | —                              | Override query-result model                            |
| `WICHY_SKIP_HUMAN_VERIFICATION`     | `false`                        | Bypass verification prompts                            |
| `WICHY_PARALLEL_EXEC`               | `true`                         | Parallel tool execution                                |
| `WICHY_MAX_BACKEND_CONNECTIONS`     | —                              | Limit concurrent LLM calls                             |

---

## CLI Reference

```bash
# Global flags
wichy --model-str <backend/model>        # LLM backend
wichy -m <backend/model>                 # short alias
wichy -r, --root-agent-description <name>  # root agent personality
wichy --tools <t1,t2>                    # tool allowlist (also globs)
wichy --not-tools <t1,t2>                # tool blocklist (also globs)
wichy --load-ctx <file>                    # resume conversation
wichy --last-ctx                           # resume most recent
wichy --no-server                          # disable web server
wichy --no-mcp                             # disable MCP discovery
wichy --first                              # user speaks first
wichy --prompt "..."                       # pipeline mode
wichy --auto-compact <n>                   # auto-compact at n tokens
wichy --show-log                           # verbose logging
wichy --show-log --log-tools               # include tool results
wichy --show-log --log-agents              # include sub-agent results
wichy --seq-exec                           # disable parallel execution
wichy --max-backend-connections <n>        # limit concurrent LLM calls
wichy --name <display-name>                # root agent display name

# Subcommands
wichy ls tools          # list available tools
wichy ls ra             # list root agent descriptions
wichy ls ctx            # list saved contexts
wichy ls skills         # list installed skills
wichy ls sa             # list available sub agents
wichy new skill -n <name> [--with-script]  # scaffold skill in .wichy/skills/
wichy new backend -n <alias> --base-url <url> --model <model> [--api-key <key>] [--scope home|project] [--force]  # add config backend
wichy install skills                    # install default bundled skills
wichy install hooks                     # install default hooks file
wichy install mcp                       # install example MCP config
wichy install sub-agents                # install default sub-agent template
wichy ra --template                     # print root agent template
wichy server [--port N] [--no-chat]     # REST API server mode
```

---

## Development

```bash
# Install
pip install -e .

# Run tests
pytest tests/

# Build
make build
```

---

## Extending Wichy

### Adding a Tool

Subclass `BaseTool`, implement `execute()`, and import it in `src/wichy/tools/__init__.py` — the metaclass auto-registers it. Return plain strings or error strings (`format_error()` / `format_error_with_context()`). Never raise exceptions from `execute()`.

For API safety, set `needs_verification_in_api = False` only for read-only or otherwise safe tools.

### Adding a Skill

```bash
wichy new skill --name my-skill
# Edit .wichy/skills/my-skill/skill.md
```

### Adding a Root Agent

```bash
wichy ra --template > .wichy/root_agent_defs/my-agent.md
# Edit the file, then:
wichy -r my-agent
```

### Adding a Hook

Create `.wichy/hooks.py`:

```python
from wichy.hooks import pre_tool, HookResult

@pre_tool("bash")
def no_rm_rf(ctx):
    if "rm -rf" in ctx.input_args.get("command", ""):
        return HookResult.deny("Not today")
    return HookResult.approve()
```

---

## Philosophy

1. **Local-First**: All processing happens locally for privacy
2. **Modular Design**: Tools, skills, and agents are composable
3. **Persistent Context**: Conversations survive restarts via JSONL
4. **Safe Execution**: Human verification for destructive operations
5. **Extensible**: Add tools, skills, agents, hooks, and MCP servers without core changes
