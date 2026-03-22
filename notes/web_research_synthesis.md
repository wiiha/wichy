# Web Research Synthesis: Key Insights for Wichy

---

## 1. Anthropic's Multi-Agent "Team" Approach

**Core Pattern:** Orchestrator-worker architecture
- A lead agent coordinates specialized subagents working in parallel
- Subagents have separate contexts → prevents path dependency
- Performance gains: up to 90% faster on research tasks vs single agent

**Key Principles:**
- Scale agent count to task complexity
- Tools must be extremely clear (bad descriptions cause failures)
- Let agents improve their own prompts (40% time reduction)
- Start wide (parallel exploration), then narrow down

**Context Engineering:**
- More important than prompt engineering
- Techniques: compaction, structured note-taking (external memory), just-in-time retrieval
- Challenge: multi-agent uses ~15× more tokens; tasks must justify cost

**Implication for Wichy:** Current TaskAgent is already a step toward this. Could enhance with better lineage tracking, shared memory, and orchestrator improvements.

---

## 2. Getting LLMs to Update Files Reliably

**Core Challenge:** LLMs are inherently unreliable for direct file operations—they forget, overwrite, and format inconsistently.

**Best Practice Architecture:**
```
Tier 1: Session State (in-memory)
Tier 2: File-based Memory (markdown files, human-readable, git-backed)
Tier 3: Event Sourcing (append-only logs for audit & resume)
```

**Reliable Update Patterns:**
- **Append-only** for learning logs (never rewrite)
- **Read-Modify-Write** for configuration (always read first)
- **Merge updates** (don't replace whole file)
- **Checkpoint-Validate-Recover**: backup before write, validate after, rollback on failure
- **Git integration**: commit after significant changes for durability

**Prompt Engineering:**
- Use atomic operation instructions: "Read → Modify → Validate → Write"
- Provide clear schema and examples
- Include format templates
- Add validation step in prompt

**Implementation Tip:** Wrap all file operations in a utility that handles reading, merging, validation, backup, and logging. Never trust raw LLM output to write directly.

---

## 3. PDF Reading for LLMs

**The Landscape:**

| Tool | Best For | Speed | Layout | Scanned PDFs |
|------|----------|-------|--------|--------------|
| **Docling** | AI-ready RAG, tables, formulas | Medium | ✅ Excellent | ✅ OCR support |
| **PyMuPDF** | Fast text extraction | ⚡ Fast | Basic | ❌ |
| **pdfplumber** | Complex layouts, tables | Medium | ✅ Very good | ❌ |
| **pdf2image + Tesseract** | Scanned/image PDFs | Slow | ❌ (images only) | ✅ OCR |

**Key Insight:** NVIDIA research shows specialized OCR pipelines outperform VLMs by 7.2% in retrieval accuracy and are 32× faster. Vision models (GPT-4V, Claude 3.5, Llama 3.2 Vision) can "read" PDFs as images but are expensive and slow.

**Recommendation for Wichy:**
- **Primary:** Use **Docling** (AI-optimized, preserves structure, outputs markdown)
- **Fallback:** PyMuPDF for simple text extraction
- **Scanned docs:** pdf2image + Tesseract if Docling OCR fails
- **Avoid:** Relying solely on VLMs for production PDF processing

---

## 4. Tool Call Formats & Parsing

**Provider Differences:**

| Feature | OpenAI | Anthropic | Gemini |
|---------|--------|-----------|--------|
| Container | `tool_calls` array | `content` blocks | `parts` array |
| Type | `function` | `tool_use` | `functionCall` |
| Args | JSON string | JSON object | JSON object |
| ID format | `call_*` | `toolu_*` | `call_*` |

**Common Failure Modes:**
1. **False positives**: LLM discusses tools without calling them
2. **Malformed JSON**: Invalid argument syntax
3. **Narrative embedding**: Tool calls mixed with prose
4. **Missing IDs**: Can't match results to calls

**Robust Parsing Strategy:**
- Use official SDKs when available (they handle format differences)
- For raw responses, implement multi-stage extraction:
  1. Look for structured blocks (JSON in ```json, XML tags)
  2. Validate against tool schemas
  3. Heuristic: require isolated code blocks, not narrative text
- Require strict delimiters to avoid false positives
- For Ollama/local models, enable "JSON mode" or "tool call prompting" if supported

**Anthopic Example:**
```json
{
  "content": [{
    "type": "tool_use",
    "name": "search",
    "input": {"query": "..."},
    "id": "toolu_abc123"
  }]
}
```

**Takeaway:** Build a `UniversalToolCallExtractor` that tries multiple patterns (code blocks, XML tags, provider-specific formats) and validates against schemas. Log failures to improve heuristics.

---

## TL;DR

- **Anthropic** shows parallel agents work well but are token-expensive; Wichy's TaskAgent is on the right track.
- **File updates** need guardrails: append-only logs, read-modify-write, git backups, validation layers.
- **PDFs**: Use Docling (AI-optimized), not vision models, for best accuracy/cost.
- **Tool calls**: Each provider has a different format; implement flexible parser with schema validation to handle edge cases and avoid executing false positives.

All of these findings directly inform several items in the Wichy backlog, especially auto-compaction (from context engineering), USER.md updates (file reliability), PDF support (Docling integration), and tool call parsing robustness.

---

**Sources:**

- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Introducing Claude Opus 4.6](https://www.anthropic.com/news/claude-opus-4-6)
- [Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Agent Memory Patterns: Checkpoint, Resume, and State Persistence](https://understandingdata.com/posts/agent-memory-patterns/)
- [Building Persistent AI Agent Memory: A 4-Layer File-Based Architecture](https://dev.to/oblivionlabz/building-persistent-ai-agent-memory-a-4-layer-file-based-architecture-5996)
- [User Profile with Large Language Models: Construction, Updating, and Benchmarking](https://arxiv.org/abs/2502.10660)
- [Docling Documentation](https://docling-project.github.io/docling/)
- [NVIDIA Blog: PDF Extraction Approaches](https://developer.nvidia.com/blog/approaches-to-pdf-data-extraction-for-information-retrieval/)
- [Anthropic Tool Use Documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling)

---

**End of Synthesis**
