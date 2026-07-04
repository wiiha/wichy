# WICHY

A local-first agentic LLM framework built on Simon Willison's definition:
[An LLM agent runs tools in a loop to achieve a goal](https://simonwillison.net/2025/Sep/18/agents/).

**Goals**

- Run entirely locally (privacy-first)
- Serve as a foundation for larger projects
- Provide a rich tool ecosystem for real-world tasks

**For full documentation, see [ABOUT.md](ABOUT.md)**

## Quick Start

```bash
# Install in a virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -e .

# Start the REPL with the default root agent
wichy

# Use a specific model
wichy --model-str ollama/llama3.2
wichy --model-str open_router/anthropic/claude-3.5-sonnet

# Resume a previous conversation
wichy --last-ctx
wichy --load-ctx .wichy/contexts/2026-07-04_1234567890.jsonl
```

## What Wichy Includes

- **37 Tools**: file operations, shell commands, web search, browser automation, DuckDB queries, graphs, notes, sub-agents, skills, MCP tool proxies, and more
- **Root & Task Agents**: `RootAgent` runs the REPL; `TaskAgent` handles delegated multi-step work
- **Skills System**: Markdown-based knowledge bundles with optional scripts; project-local `.wichy/skills/` and shared `~/.wichy/skills/`
- **Hooks System**: Intercept and modify tool execution and lifecycle events
- **Napkin Runbook**: Per-repo curated runbook at `.wichy/napkin.md`
- **Agent Notebook**: SQLite memory at `.wichy/notebook.db` for cross-session learning
- **Web Interface**: Chat, notes editor, context editor, graph editor, and data explorer at `http://127.0.0.1:7891`
- **Server API**: HTTP API for messages, verifications, sub-agents, root context, and tool execution
- **Multiple LLM Backends**: Ollama, llama.cpp, OpenRouter, or any OpenAI-compatible endpoint via `generic/`
- **Context Persistence**: JSONL conversation storage in `.wichy/contexts/`

## Modes

| Mode | Command | Purpose |
|------|---------|---------|
| REPL (default) | `wichy` | Interactive loop with slash commands and web UI |
| Pipeline | `wichy --prompt "..."` | Single-shot, non-interactive execution to stdout |
| Server | `wichy server [--port N]` | HTTP server mode without REPL |

## CLI Cheatsheet

```bash
wichy ls tools        # list available tools
wichy ls ra           # list root agent descriptions
wichy ls skills       # list installed skills
wichy ls sa           # list available sub-agent types
wichy ls ctx          # list saved contexts

wichy new skill --name my-skill       # scaffold a project-local skill
wichy install skills                  # install default bundled skills
wichy ra --template                   # print root agent template

wichy --tools read_file,write_file,bash   # allow-list tools
wichy --not-tools bash                     # block specific tools
wichy --no-server                          # disable the web UI/API
wichy --first                              # user speaks first
wichy --auto-compact 8000                  # summarize context at 8k tokens
wichy --show-log --log-tools --log-agents  # verbose output
```

## Docker

Run wichy in a container for isolation:

```bash
# Build
docker build -t wichy .

# Interactive REPL (mount your project)
docker run -it --rm \
  -v /path/to/your/project:/workspace \
  -p 7891:7891 \
  --add-host=host.docker.internal:host-gateway \
  wichy

# With custom host port (access web GUI on localhost:7895)
docker run -it --rm \
  -v /path/to/your/project:/workspace \
  -p 7895:7891 \
  --add-host=host.docker.internal:host-gateway \
  wichy

# With ~/.wichy mount (persists skills, root agent defs)
docker run -it --rm \
  -v /path/to/your/project:/workspace \
  -v ~/.wichy:/home/wichy/.wichy \
  -p 7891:7891 \
  --add-host=host.docker.internal:host-gateway \
  wichy --model-str ollama/llama3.2

# Pipeline mode (headless)
docker run --rm \
  -v /path/to/your/project:/workspace \
  --add-host=host.docker.internal:host-gateway \
  wichy --prompt "Review the codebase"

# Override Ollama endpoint (e.g., remote server)
docker run -it --rm \
  -v /path/to/your/project:/workspace \
  -e WICHY_OLLAMA_BASE_URL="http://192.168.1.50:11434/v1" \
  -p 7891:7891 \
  wichy --model-str ollama/llama3.2
```

**Key points:**
- LLM backends (Ollama, llama.cpp) run on the **host** — container connects via `host.docker.internal`
- Project folder mounts at `/workspace` — the `.wichy/` folder for contexts/logs goes there
- `~/.wichy` mount is optional — stores skills and user root agent definitions
- Web GUI accessible at mapped port (default `http://localhost:7891`)
- Container runs as non-root user `wichy`

**Environment variables (set in Dockerfile, can be overridden with `-e`):**
| Variable | Default | Purpose |
|----------|---------|---------|
| `WICHY_OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | Ollama API endpoint |
| `WICHY_SERVER_HOST` | `0.0.0.0` | Web server bind address (Docker needs `0.0.0.0`) |

**Note:** `--add-host=host.docker.internal:host-gateway` is required on Linux. On macOS/Windows Docker Desktop, it's optional but recommended for consistency.

## References

- [Simon Willison on Agents](https://simonwillison.net/2025/Sep/18/agents/)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling)
- [IBM Granite Tool Calling](https://www.ibm.com/granite/docs/models/granite#tool-calling)
- [Qwen Thinking Mode](https://docs.unsloth.ai/models/qwen3-how-to-run-and-fine-tune#switching-between-thinking-and-non-thinking-mode)
