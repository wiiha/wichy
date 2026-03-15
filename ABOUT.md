# Wichy - Agentic LLM Framework

Wichy is a **local-first agentic LLM framework** implementing Simon Willison's definition: _"An LLM agent runs tools in a loop to achieve a goal."_ It provides a REPL interface with a comprehensive tool ecosystem, skills system, and web-based utilities.

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
wichy --load-ctx 2025-03-15_1234567890.jsonl

# List available tools
wichy ls tools

# List available skills
wichy ls skills

# Create a new skill
wichy new skill --name my-skill
```

---

## Architecture

### Entry Point

`wichy` or `python -m wichy` → `__main__.py:main()` → `AgentBuilder.build()` → `Repl.run()`

### Core Components

```
┌─────────────────────────────────────────────────────┐
│                    REPL (repl.py)                   │
│              PromptSession with history             │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│                  RootAgent                          │
│   process() → call LLM → handle_tools() → loop      │
└─────────────────────┬───────────────────────────────┘
                      │
      ┌───────────────┼───────────────┬───────────────┐
      │               │               │               │
┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
│   Tools   │   │  Skills   │   │  Context  │   │Web Server │
│  (28+)    │   │ (markdown)│   │ (JSONL)   │   │  (Flask)  │
└───────────┘   └───────────┘   └───────────┘   └───────────┘
```

---

## Tool System

Wichy includes **28+ tools** for various tasks:

### Basic Tools

- **BashTool** - Shell command execution with human verification for destructive operations
- **TodoTool** - Task management and planning
- **AskUserQuestion** - Interactive user prompts

### File System Tools

- **ReadFileTool** - Read file contents (supports images via base64)
- **WriteFileTool** - Write or create files
- **ListFilesTool** - Directory listing
- **GlobTool** - Pattern-based file matching
- **SearchInFilesTool** - Ripgrep-powered content search
- **ReplaceTextTool** - Targeted text replacement
- **InsertLinesTool** - Insert content at specific lines
- **KnowledgeStoreTool** - Search personal knowledge store

### Data Analysis Tools

- **DuckDBLoadTool** - Load CSV/Parquet/JSON into tables
- **DuckDBQueryTool** - Execute SQL queries
- **DuckDBSchemaTool** - Inspect table structure
- **DuckDBStatusTool** - Check session state
- **DuckDBPersistTool** - Save database to disk
- **DuckDBLoadDBTool** - Load saved database
- **DuckDBResetTool** - Clear session

### Web & Browser Tools

- **WebSearchTool** - DuckDuckGo search
- **FetchWebPageTool** - Fetch webpage content as markdown
- **NavigateTool** - Browser navigation (Playwright)
- **BrowserStatusTool** - Current browser state
- **ScreenshotTool** - Capture browser screenshots
- **BrowserRawTool** - Execute raw Playwright commands

### Graph Tools

- **CreateGraphTool** - Create graphs from text
- **ReadGraphTool** - Read graph data
- **ListGraphsTool** - List saved graphs

### Sub-Agent Tools

- **TaskAgentTool** - Launch specialized agents for complex tasks

### Skill Tools

- **SkillDiscoveryTool** - List available skills
- **SkillSearchTool** - Search skill content
- **SkillInfoTool** - Get skill details
- **SkillScriptTool** - Execute skill scripts
- **SkillFileTool** - Read skill references/assets

### Networking Tools (not enabled by default)

- **PingTool** - Network connectivity testing
- **ReverseDnsTool** - DNS resolution
- **TreeTool** - Directory tree visualization

---

## Skills System

Skills are markdown-based knowledge bundles stored in `~/.wichy/skills/<name>/`:

```
~/.wichy/skills/my-skill/
├── skill.md          # Required: knowledge/documentation
├── references/       # Optional: reference documents
├── assets/           # Optional: templates, examples
└── scripts/          # Optional: executable scripts
```

**skill.md format:**

```markdown
---
name: my-skill
description: What this skill does
tags: [tag1, tag2]
safe_scripts: [safe_script.sh] # Scripts that don't require approval
---

# Skill Knowledge

Your knowledge content here...
```

---

## LLM Backends

Wichy supports multiple backends via the `--model-str` flag:

| Backend    | Format                             | Example                           |
| ---------- | ---------------------------------- | --------------------------------- |
| Ollama     | `ollama/<model>`                   | `ollama/llama3.2`                 |
| llama.cpp  | `llama_cpp/<model>`                | `llama_cpp/model`                 |
| OpenRouter | `open_router/<model>`              | `open_router/anthropic/claude-3`  |
| Generic    | `generic/<host>[:<port>]##<model>` | `generic/localhost:8080##llama-3` |

Configuration via environment variables:

- `WICHY_OLLAMA_BASE_URL` (default: `http://localhost:11434/v1`)
- `WICHY_LLAMA_CPP_BASE_URL` (default: `http://localhost:8080`)
- `OPEN_ROUTER_API_KEY` or `WICHY_OPENROUTER_API_KEY`

---

## Context Management

Conversations are persisted as JSONL files in `.wichy/contexts/`:

```python
# Context structure
{
  "type": "message",  # or "log" for metadata
  "role": "user" | "assistant" | "tool",
  "content": "...",
  "timestamp": "2025-03-15T10:30:00"
}
```

### Slash Commands

| Command           | Description                   |
| ----------------- | ----------------------------- |
| `/reset`          | Clear context completely      |
| `/compact`        | Summarize context, then reset |
| `/drop`           | Remove last message           |
| `/logging on/off` | Toggle verbose logging        |
| `/exit`           | Exit the REPL                 |

### Context Editor

Web-based context editor at `http://127.0.0.1:7891/tools/context/`:

- Live editing of conversation messages
- Syncs with REPL via file watching
- Atomic writes prevent corruption

---

## Root Agents

Root agents define the agent's behavior via markdown descriptions:

**Built-in agents:**

- `root-agent-basic` - Simple configuration
- `root-agent-code-advanced` - Enhanced for software engineering (default)

**Custom agents:** Create in `~/.wichy/root_agent_defs/` or `.wichy/root_agent_defs/`

```bash
# Get template
wichy ra --template > my-agent.md

# Place in: ~/.wichy/root_agent_defs/my-agent.md
```

---

## Web Interface

Wichy starts a Flask server on port 7891 (disable with `--no-server`):

| Path              | Description                 |
| ----------------- | --------------------------- |
| `/`               | Landing page with tool GUIs |
| `/tools/graph/`   | Graph editor                |
| `/tools/context/` | Context editor              |

Logs: `.wichy/logs/server.log`

---

## Configuration

Settings are managed via pydantic-settings with `WICHY_` prefix:

| Setting                  | Default           | Description           |
| ------------------------ | ----------------- | --------------------- |
| `WICHY_CONTEXTS_DIR`     | `.wichy/contexts` | Context storage       |
| `WICHY_BROWSER_HEADLESS` | `true`            | Browser headless mode |
| `WICHY_SERVER_PORT`      | `7891`            | Web server port       |

Also loads from `.env` file.

---

## CLI Reference

```bash
# Global flags
wichy --model-str <backend/model>
wichy --root-agent-description <name>
wichy --tools <tool1,tool2>      # Whitelist tools
wichy --not-tools <tool1,tool2>  # Blacklist tools
wichy --load-ctx <file>          # Resume conversation
wichy --no-server                # Disable web server
wichy --show-log                 # Verbose logging
wichy --show-log --log-tools     # Include tool results

# Subcommands
wichy ls tools    # List available tools
wichy ls ra       # List root agent descriptions
wichy ls ctx      # List saved contexts
wichy ls skills   # List installed skills
wichy new skill --name <name>    # Create new skill
wichy ra --template              # Print agent template
```

---

## Development

```bash
# Run tests
pytest tests/

# Install editable
pip install -e .

# Build
make build
```

---

## Extending Wichy

### Adding a Tool

```python
# src/wichy/tools/my_tool.py
from wichy.tools.base import BaseTool, ParametersModel

class MyParams(ParametersModel):
    query: str

class MyTool(BaseTool):
    name = "my_tool"
    description = "Short description"
    parameters_model = MyParams

    def execute(self, query: str) -> str:
        return f"Result for: {query}"

# Register in src/wichy/tools/__init__.py
from wichy.tools.my_tool import MyTool
```

### Adding a Skill

```bash
wichy new skill --name my-skill
# Edit ~/.wichy/skills/my-skill/skill.md
```

---

## Philosophy

1. **Local-First**: All processing happens locally for privacy
2. **Modular Design**: Tools, skills, and agents are composable
3. **Persistent Context**: Conversations survive restarts
4. **Safe Execution**: Human verification for destructive operations
5. **Extensible**: Add tools, skills, and agents without core changes
