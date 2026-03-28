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
