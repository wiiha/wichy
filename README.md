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
# Install
pip install -e .

# Run with default model
wichy

# Or specify a model
wichy --model-str ollama/llama3.2

# Resume a previous conversation
wichy --last-ctx
wichy --load-ctx 2025-03-15_1234567890.jsonl
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

## What's Included

- **40+ Tools**: File operations, web browsing, DuckDB queries, graph editing, notes, sub-agents, skills, and more
- **Skills System**: Markdown-based knowledge bundles with optional scripts, auto-discovered from `~/.wichy/skills/`
- **Napkin Runbook**: Per-repo curated runbook that persists guidance across sessions
- **Web Interface**: Notes editor, graph editor, and context editor at http://127.0.0.1:7891
- **Multiple LLM Backends**: Ollama, llama.cpp, OpenRouter, or any OpenAI-compatible endpoint

## References

- [Simon Willison on Agents](https://simonwillison.net/2025/Sep/18/agents/)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling)
- [IBM Granite Tool Calling](https://www.ibm.com/granite/docs/models/granite#tool-calling)
- [Qwen Thinking Mode](https://docs.unsloth.ai/models/qwen3-how-to-run-and-fine-tune#switching-between-thinking-and-non-thinking-mode)
