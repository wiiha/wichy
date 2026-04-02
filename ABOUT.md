# Wichy - Agentic LLM Framework

Wichy is a **local-first agentic LLM framework** implementing Simon Willison's definition: _"An LLM agent runs tools in a loop to achieve a goal."_ It provides a REPL interface, a comprehensive tool ecosystem, a skills system, and a suite of web-based utilities.

---

## Quick Start

```bash
# Install
pip install -e .

# Start with default root agent
wichy

# Specify a model
wichy --model-str ollama/llama3.2

# Resume previous conversation
wichy --last-ctx
wichy --load-ctx 2025-03-15_1234567890.jsonl

# List available tools, agents, skills
wichy ls tools
wichy ls ra
wichy ls skills

# Create a new skill
wichy new skill --name my-skill
```

---

## Architecture

### Entry Point

`wichy` → `__main__.py:main()` → `AgentBuilder.build()` → `Repl.run()`

### Agent Hierarchy

```
AgentCore  (abstract base — shared tool execution logic)
  ├── RootAgent  (main REPL session agent)
  │     process() → call LLM → handle_tools() → loop until done
  │
  └── TaskAgent  (sub-agent for delegated multi-step work)
        Spawned via the `task` tool. Fresh isolated context.
```

`AgentCore` (`agent/core.py`) provides the shared `_tool_call()`, `_handle_tools_base()`, `_fix_multimodal_context()`, and `_get_tool_definitions()` methods. Subclasses (`RootAgent`, `TaskAgent`) override `_log()` and `_log_dict()` for their respective console instances.

### Execution Flow

The agent loop follows this cycle:

```
┌─────────────────────────────────────────────────────────────────────┐
│                         REPL Loop                                   │
│  ┌─────────┐    ┌──────────────┐    ┌──────────┐    ┌───────────┐   │
│  │ User    │───▶│ RootAgent    │───▶│ LLM API  │───▶│ Tool      │   │
│  │ Input   │    │ .process()   │    │ Call     │    │ Handler   │   │
│  └─────────┘    └──────────────┘    └──────────┘    └─────┬─────┘   │
│       ▲                                    │              │         │
│       │                               ┌────▼────┐    ┌────▼──────┐  │
│       │                               │ Response│    │ Execute   │  │
│       └───────────────────────────────│ Loop    │◀───│ Tool      │  │
│                                       └─────────┘    └───────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**Step-by-step:**

1. **User Input** → REPL receives input from `prompt_toolkit`
2. **RootAgent.process()** → Appends user message to context
3. **LLM Call** → Sends context to the model with tool definitions
4. **Response Handling**:
   - If `finish_reason == "tool_calls"`: Execute tools, append results, loop back to LLM
   - Otherwise: Stream response to user, wait for next input

### TaskAgent Spawning

When the `task` tool is invoked, a `TaskAgent` is spawned:

```python
# From task_tool.py
task_agent = TaskAgent(
    definition=agent_definition,
    context=Context(),  # Fresh isolated context
    agent_type=agent_type,
)
result = task_agent.process(prompt)
```

- **Isolated Context**: TaskAgent starts with a fresh, empty context
- **Limited Tools**: Only tools specified in the agent definition are available
- **Recursive Prevention**: The `task` tool excludes itself from sub-agent tool lists
- **Result Return**: Final response is returned to the calling RootAgent

### Context Persistence Flow

All context mutations are persisted atomically:

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

**JSONL Format:**

```jsonl
{"_tick": 1, "role": "system", "content": "...", "type": "message"}
{"_tick": 2, "role": "user", "content": "...", "type": "message"}
{"_tick": 3, "event": "tool_call", "name": "read_file", "type": "log"}
{"_tick": 4, "role": "tool", "content": "...", "type": "message"}
```

- `_tick`: Auto-incremented sequence number for stale detection
- `type`: Either `"message"` (sent to LLM) or `"log"` (session metadata)

### Auto-Compact Flow

When auto-compaction is enabled and the token threshold is reached:

```
current_prompt_tokens >= auto_compact_threshold
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Generate summary prompt with recent messages                     │
│ 2. Call LLM for summary                                             │
│ 3. Create new Context with summary as system message               │
│ 4. Replace old context atomically                                   │
│ 5. New context starts at _tick=1                                    │
└─────────────────────────────────────────────────────────────────────┘
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
│ (40+)     │   │  ~/.wichy/skills/    │  │  (JSONL)  │  │  (Flask) │
│ auto-reg  │   │  markdown bundles    │  │           │  │  :7891   │
└───────────┘   └──────────────────────┘  └───────────┘  └──────────┘
```

---

## Tool System

### Tool Registry

Tools self-register via the `ToolMeta` metaclass — any class inheriting from `BaseTool` is automatically added to the registry on import. No manual registration needed.

```python
# src/wichy/tools/my_tool.py
from wichy.tools.base import BaseTool, ParametersModel

class MyParams(ParametersModel):
    query: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "Short description"
    description_long: str | None = None  # passed to LLM
    parameters_model = MyParams

    def execute(self, query: str) -> str:
        return f"Result for: {query}"

# src/wichy/tools/__init__.py
from wichy.tools.my_tool import MyTool  # auto-registers on import
```

### Tool Categories

#### File Operations

| Tool              | Class               | Description                                                                                                                        |
| ----------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `read_file`       | `ReadFileTool`      | Read file contents; supports images via base64 multimodal conversion, offset/limit, non-printable chars                            |
| `write_file`      | `WriteFileTool`     | Write or create files; auto-creates parent dirs                                                                                    |
| `replace_text`    | `ReplaceTextTool`   | Targeted before/after string replacement; occurrence control                                                                       |
| `insert_lines`    | `InsertLinesTool`   | Insert content at 1-indexed line offset; appends at EOF if offset exceeds file length                                              |
| `list_files`      | `ListFilesTool`     | Directory listing                                                                                                                  |
| `glob`            | `GlobTool`          | Pattern-based file matching; sorted newest-first; venv-excluded                                                                    |
| `tree`            | `TreeTool`          | Directory tree visualization respecting `.gitignore`                                                                               |
| `search_in_files` | `SearchInFilesTool` | Ripgrep-powered regex search; `content`/`files_with_matches`/`count` modes; 500-match oversize guard; per-line 300-char truncation |

#### Notes & Scratchpad

| Tool               | Class                 | Description                                          |
| ------------------ | --------------------- | ---------------------------------------------------- |
| `read_scratchpad`  | `ReadScratchpadTool`  | Read the pinned scratchpad note                      |
| `write_scratchpad` | `WriteScratchpadTool` | Save a markdown scratchpad note; auto-pins as active |

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

#### Skills Tools

| Tool                   | Class                    | Description                                                                               |
| ---------------------- | ------------------------ | ----------------------------------------------------------------------------------------- |
| `list_skills`          | `ListSkillsTool`         | List all skills with tags and script counts                                               |
| `search_skills`        | `SearchSkillsTool`       | Search skills by keyword (name, description, tags, content)                               |
| `activate_skill`       | `ActivateSkillTool`      | Get full skill details: markdown content, metadata, scripts list                          |
| `execute_skill_script` | `ExecuteSkillScriptTool` | Run a skill script; human verification unless in `safe_scripts`; blocked in pipeline mode |
| `read_skill_file`      | `ReadSkillFileTool`      | Read skill reference or asset files                                                       |

#### Networking (not enabled by default)

| Tool   | Class      | Description              |
| ------ | ---------- | ------------------------ |
| `ping` | `PingTool` | ICMP ping; max 5 packets |

### Human Verification

`BashTool` classifies ~90+ shell commands as safe/destructive. Destructive commands (`rm`, `dd`, `mkfs`, etc.), dangerous flags (`-i`, `-f`, `--hard`), and redirect/pipe operators all trigger a y/n prompt before execution. Verification is skipped in pipeline mode or when `SKIP_HUMAN_VERIFICATION` is set.

---

## Context Management

### Persistence

Conversations are stored as **newline-delimited JSON (JSONL)** in `.wichy/contexts/`:

```
{YYYY-MM-DD}_{unix_timestamp}[_{custom_suffix}].json
```

Each line is either a `"message"` entry (sent to the LLM) or a `"log"` entry (session metadata, never sent to the LLM). Every entry carries an auto-incremented `_tick` field for stale detection.

**Atomic writes**: All mutations use a `temp → rename` pattern to avoid corruption on crash.

### Context Operations

| Method                               | Effect                                                    |
| ------------------------------------ | --------------------------------------------------------- |
| `append(msg)` / `add(role, content)` | Append a message                                          |
| `drop(n)`                            | Remove last _n_ messages                                  |
| `replace_all(messages)`              | Atomic bulk replacement                                   |
| `update_message(i, msg)`             | Edit message by index                                     |
| `delete_message(i)`                  | Delete message by index                                   |
| `truncate_message(i, max_chars)`     | Truncate + store original in `_truncated_from`            |
| `expand_message(i)`                  | Restore from `_truncated_from`                            |
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

Skills are **user-defined knowledge packages** stored in `~/.wichy/skills/<name>/`:

```
~/.wichy/skills/my-skill/
├── skill.md        # Required: YAML frontmatter + markdown content
├── references/      # Optional: reference documents
├── assets/         # Optional: templates, config files
└── scripts/         # Optional: executable scripts
```

**skill.md format:**

```markdown
---
name: my-skill
description: What this skill does
tags: [tag1, tag2]
safe_scripts: [safe_script.sh]
---

# Skill Knowledge

Your knowledge content here...
```

Skills are auto-discovered by `SkillLoader` and their summaries are injected into the system prompt at startup. The agent can also query skills at runtime via the skills tools.

---

## Hooks System

Hooks provide a mechanism to intercept and modify tool execution. They enable custom logic to run before or after any tool, with actions to approve, deny, or modify outputs.

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

| Action        | Method                       | Description                            |
| ------------- | ---------------------------- | -------------------------------------- |
| Approve       | `HookResult.approve()`       | Allow tool execution to proceed        |
| Deny          | `HookResult.deny("reason")`  | Block execution with error message     |
| Modify Output | `HookResult.modify_output()` | Transform tool output (post-tool only) |

### Priority-Based Execution

Hooks execute in priority order (lower = earlier). Default priority is 50.

```python
@pre_tool("bash", priority=10)  # Runs first
def high_priority_check(ctx: HookContext) -> HookResult:
    return HookResult.approve()

@pre_tool("bash", priority=100)  # Runs later
def low_priority_log(ctx: HookContext) -> HookResult:
    print(f"Executing: {ctx.tool_name}")
    return HookResult.approve()
```

### Hook File Locations

Hooks are loaded from two locations in order:

1. **User-global**: `~/.wichy/hooks.py` (loaded first)
2. **Project-local**: `.wichy/hooks.py` (loaded second, can override)

When both files exist, project-local hooks can add to or override user-global hooks.

### Lifecycle

1. Hooks are registered via decorators at module load time
2. `initialize_hooks()` is called from `__main__.py` to load hook files
3. Before each tool call: pre-tool hooks execute in priority order
4. After each tool call: post-tool hooks execute in priority order
5. Any hook can short-circuit by returning `deny()` or `modify_output()`

---

## LLM Backends

Configured via `--model-str`:

| Backend    | Format                             | Example                           |
| ---------- | ---------------------------------- | --------------------------------- |
| Ollama     | `ollama/<model>`                   | `ollama/llama3.2`                 |
| llama.cpp  | `llama_cpp/<model>`                | `llama_cpp/model`                 |
| OpenRouter | `open_router/<model>`              | `open_router/anthropic/claude-3`  |
| Generic    | `generic/<host>[:<port>]##<model>` | `generic/localhost:8080##llama-3` |

Environment variables:

| Variable                   | Default                     |
| -------------------------- | --------------------------- |
| `WICHY_OLLAMA_BASE_URL`    | `http://localhost:11434/v1` |
| `WICHY_LLAMA_CPP_BASE_URL` | `http://localhost:8080`     |
| `OPEN_ROUTER_API_KEY`      | —                           |

---

## Web Interface

Wichy starts a Flask server on port 7891 (auto-incremented if busy) in a background daemon thread. Disable with `--no-server`.

### Landing Page — `/`

Links to all available tool GUIs.

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

Server logs: `.wichy/logs/server.log`

---

## Root Agents

Root agents define the agent's personality and system prompt via markdown frontmatter:

```markdown
---
name: my-agent
description: A helpful coding assistant
tools: [read_file, write_file, bash, search_in_files, glob]
agent_has_first_initiative: false
---

[System prompt content here]
```

Built-in agents: `root-agent-basic`, `root-agent-code-advanced` (default).

Custom agents live in `~/.wichy/root_agent_defs/` or `.wichy/root_agent_defs/`.

```bash
wichy ra --template > my-agent.md  # get template
wichy -r my-agent                   # use custom agent
```

Tool availability is gated by: (1) CLI `--tools`/`--not-tools` flags, (2) the root agent's `tools:` frontmatter field, (3) the `--not-tools` exclusion list.

---

## Modes

### REPL Mode (default)

Interactive loop via `prompt_toolkit` with persistent history, auto-suggest, and slash command completion.

### Pipeline Mode (`--prompt "..."`)

Single-shot, non-interactive execution. Bypasses the REPL; sends the user's prompt as a single user message. Injects a `[System note: Running in pipeline mode…]` preamble. Final response printed to stdout, then exits with code 0. Human-verification-decorated tools (e.g. `bash` for destructive commands) and non-safe skill scripts (`execute_skill_script`) raise `PermissionError` immediately rather than prompting.

### Context Loading

```bash
wichy --load-ctx path/to/file.json   # specific file
wichy --last-ctx                       # most recent file
```

---

## Configuration

Settings via `pydantic-settings` with `WICHY_` env prefix. Also loads from `.env`.

| Setting                     | Default                     | Description           |
| --------------------------- | --------------------------- | --------------------- |
| `WICHY_CONTEXTS_DIR`        | `.wichy/contexts/`          | Conversation storage  |
| `WICHY_NOTES_DIR`           | `.wichy/notes/`             | Notes and scratchpads |
| `WICHY_GRAPHS_DIR`          | `.wichy/graphs/`            | Saved graphs          |
| `WICHY_LOGS_DIR`            | `.wichy/logs/`              | Server logs           |
| `WICHY_SKILLS_DIR`          | `~/.wichy/skills/`          | Skills storage        |
| `WICHY_ROOT_AGENT_DEFS_DIR` | `~/.wichy/root_agent_defs/` | Custom root agents    |
| `WICHY_BROWSER_HEADLESS`    | `true`                      | Browser headless mode |
| `WICHY_SERVER_PORT`         | `7891`                      | Web server port       |
| `WICHY_OLLAMA_BASE_URL`     | `http://localhost:11434/v1` | Ollama endpoint       |
| `WICHY_LLAMA_CPP_BASE_URL`  | `http://localhost:8080`     | llama.cpp endpoint    |

---

## CLI Reference

```bash
# Global flags
wichy --model-str <backend/model>      # LLM backend
wichy -r, --root-agent-description <name>  # root agent personality
wichy --tools <t1,t2>                  # tool allowlist
wichy --not-tools <t1,t2>              # tool blocklist
wichy --load-ctx <file>                # resume conversation
wichy --last-ctx                       # resume most recent
wichy --no-server                      # disable web server
wichy --first                          # user speaks first (no wake-up message)
wichy --prompt "..."                   # pipeline mode
wichy --auto-compact <n>               # auto-compact at n tokens
wichy --show-log                       # verbose logging
wichy --show-log --log-tools           # include tool results
wichy --show-log --log-agents          # include sub-agent results

# Subcommands
wichy ls tools    # list available tools
wichy ls ra       # list root agent descriptions
wichy ls ctx      # list saved contexts
wichy ls skills   # list installed skills
wichy new skill --name <name>    # scaffold new skill
wichy ra --template              # print agent template
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

Subclass `BaseTool`, override `execute()`, and import it in `src/wichy/tools/__init__.py` — the metaclass auto-registers it. Return plain strings or error strings (`format_error()` / `format_error_with_context()`). Never raise exceptions from `execute()`.

### Adding a Skill

```bash
wichy new skill --name my-skill
# Edit ~/.wichy/skills/my-skill/skill.md
```

---

## Philosophy

1. **Local-First**: All processing happens locally for privacy
2. **Modular Design**: Tools, skills, and agents are composable
3. **Persistent Context**: Conversations survive restarts via JSONL
4. **Safe Execution**: Human verification for destructive operations
5. **Extensible**: Add tools, skills, and agents without core changes
