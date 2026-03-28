# Wichy Harness Improvement Suggestions

> Based on: (1) codebase analysis of wichy, (2) online research into agentic harness engineering literature, industry blog posts, and GitHub issues from Claude Code, Cline, SWE-agent, OpenCode, Aider, and others.

**Date:** 2026-03-28

---

## 1. Edit Tool Fallback Cascade — Highest Priority

Wichy has `replace_text`, `insert_lines`, and `write_file` but **no fallback logic** when a string match fails. The agent receives an error and must manually re-read the file and re-call the tool. This is the single biggest reliability gap relative to every other harness in the ecosystem.

- **Cline** runs edits through a cascade: exact match → whitespace-normalized match → anchor-based match (first/last line match, fuzzy middle) → diff/patch → full overwrite.
- **OpenCode** goes further with 9 layers of fallback.
- **Sweep AI** documented that ~13% of str_replace failures are state-drift-related (format-on-save, concurrent edits).

**Recommendation:** When `replace_text` fails, try a whitespace-stripped version of `old_content` before giving up. Then try matching just the first and last line, finding that span, and replacing within it. Only fall back to `write_file` when everything else fails — and warn the agent that content may be at risk. Consider Cline's open-source diff-edit evaluation framework as a reference implementation (linked from their blog post on improving diff edits by 10%).

---

## 2. Output Normalization for State Drift Prevention

Wichy's `replace_text` is vulnerable to the trailing-whitespace state-drift problem Sweep AI documented in detail. Claude (and most LLMs) generate trailing whitespace between newlines (`"\n \n"` instead of `"\n\n"`). If the IDE or a linter strips those trailing spaces, a subsequent edit referencing the old whitespace will silently fail.

**Recommendation:** Normalize LLM-generated code in the tool execution layer before results enter the context — strip trailing whitespace from newlines in tool results. This prevents the entire class of failures without adding formatting constraints to prompts (which degrades output quality, as documented by Aider). This is a one-time transform that benefits all edit operations.

---

## 3. Tool Description Consistency — Low Effort, High Impact

The codebase analysis found `list_files` described as `"List files in a directory"` while `bash` gets 40 lines of detailed guidance including quoting rules and examples. Anthropic noted on SWE-bench they "spent more time optimizing tools than the overall prompt itself" — tool descriptions directly determine agent behavior.

**Recommendation:** Apply the `TaskAgentTool` description (125 lines, examples, patterns, guidance) as the minimum standard for every tool. At minimum, each tool description should include:

- Purpose (one sentence)
- When to use it
- When _not_ to use it (vs. a similar tool)
- Common failure modes
- One concrete example

---

## 4. Write-File Guardrails

Wichy's `write_file` description says to read first, but this is **unenforced**. The agent can overwrite any file without ever having read it. GitHub issue #27137 on the Claude Code repo documents exactly this failure: an agent rewrote a file from memory and silently dropped an 11-item table of contents section. The agent's own self-audit failed to detect the content loss because it checked for deleted _files_ but not missing content _within modified files_.

**Recommendations:**

- Track a small "recently read files" set per session. If `write_file` is called on an existing path, require a recent `read_file` call on that path in the current context.
- Pipe writes through a `diff` before committing. Return the diff to the LLM context and require it to acknowledge before the write finalizes. This is how Grok CLI handles it.

---

## 5. Context Compaction Error Recovery

The codebase analysis identified a real bug: if `compact_context()` fails partway through (rate limit, context length error), the old context is deleted before the new one is validated. Session history is silently lost.

```python
# Current (buggy):
response = call(context=ctx(), model_str=self.model_str)
ctx.delete()  # ← old context gone even if call() raised

# Fixed:
old_ctx = ctx
response = call(context=old_ctx, ...)
# validate response ...
old_ctx.delete()
```

**Recommendation:** Hold a reference to the old context, validate the compaction succeeds, then delete the old one. Alternatively, write the new context to a temp file first, validate, then atomically rename.

---

## 6. Permission Granularity — Avoid Approval Fatigue

Wichy's human verification is binary (y/n). The literature warns that **approval fatigue collapses safety silently** — users reflexively approve out of frustration, and the guard becomes theater. The system has `SKIP_HUMAN_VERIFICATION` and `PIPELINE_MODE` but no middle ground like "approve this class of operation for the rest of this session."

**Recommendation:** Add session-level permission persistence for command patterns. If the user approves `rm` three times, offer to remember that approval for the session. Ensure approvals are revocable at any time. The Anthropic harness literature calls this "approval persistence" and notes it as essential to preventing approval fatigue.

---

## 7. `_tick` as Staleness Tracking — Currently Dead Code

Every message in the context gets a `_tick` counter incremented per LLM round-trip. This is a genuinely good idea, but the agent has no awareness of it and no mechanism acts on it.

**Recommendation:** Inject a small hint into the system prompt enabling the agent to reason about staleness — e.g., "Messages with a higher tick count may be from earlier in the session and less relevant to the current task." Combine with a staleness-based eviction policy that summarizes or drops messages older than N ticks. This would meaningfully improve long-session coherence without significant engineering.

---

## 8. Tool Call Replay Protection

If the LLM sends the same tool call twice (common in retry loops or when the model generates duplicate tool calls), Wichy executes it twice with no idempotency check. For `replace_text` in particular, a second execution after a successful first could silently corrupt state.

**Recommendation:** Track a hash of recent tool calls per session. If a tool call hash repeats within the same LLM response batch, skip execution and return a note to the LLM: "This tool call was already executed in this batch. If you need to retry, please re-read the file first."

---

## 9. Consider AST/Semantic Edit Tools — Long-Term Direction

The industry is moving toward AST-aware editing with 98% accuracy (Morph) versus 60–85% for string matching. Wichy currently relies on exact string matching, which is inherently fragile.

**Recommendation (long-term):** Consider adding a `semantic_replace` tool — one that understands code structure via tree-sitter or the native AST, finds matching nodes, and replaces semantically. Even a lightweight version would dramatically improve reliability for code modifications. The ast-grep project provides a good reference for structural search and replace across many languages.

---

## 10. Catch Unhandled LLM Exceptions in REPL

The REPL exception handling catches specific known exceptions (`ContextResetException`, `LLMBackendContextLimitReached`, `LLMBackendRateLimitExceeded`, `KeyboardInterrupt`, `EOFError`) but any other exception from the LLM call (JSON decode errors, API errors, unexpected response formats) will crash the REPL entirely.

**Recommendation:** Wrap the main REPL loop in a broad `except Exception as e` that logs the error, notifies the user, and continues rather than crashes. At minimum, log the full traceback for debugging.

---

## 11. Insert Lines Offset Semantics Are Inconsistent

The `insert_lines` offset behavior is confusing and error-prone:

- `offset=0` → insert at BEGINNING
- `offset >= len(lines)` → append at END
- `offset > 0 and < len(lines)` → insert BEFORE the indexed line

Since Wichy uses 1-indexed line numbers throughout, `offset=1` should mean "after line 1" (i.e., before line 2 in 0-indexed terms), but the code actually inserts before line 1. This mismatch between 1-indexed documentation and 0-indexed behavior is a silent foot-gun.

**Recommendation:** Audit and fix the offset semantics to be clearly documented and consistent. The safest approach: make `insert_lines` take both `before_line` and `after_line` parameters, removing ambiguity about direction.

---

## Summary of Priority

| #   | Suggestion                   | Effort | Impact |
| --- | ---------------------------- | ------ | ------ |
| 1   | Edit fallback cascade        | Medium | High   |
| 2   | Output normalization         | Low    | High   |
| 3   | Tool description consistency | Low    | Medium |
| 4   | Write-file guardrails        | Medium | High   |
| 5   | Compaction error recovery    | Low    | High   |
| 6   | Permission granularity       | Medium | Medium |
| 7   | `_tick` staleness hints      | Low    | Medium |
| 8   | Tool call replay protection  | Low    | Medium |
| 9   | AST/semantic edit tools      | High   | High   |
| 10  | REPL unhandled exceptions    | Low    | Low    |
| 11  | Insert lines offset fix      | Low    | Low    |

---

## References

- [Anthropic: Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Sweep AI: Decreasing agentic code editing failures with output normalization](https://blog.sweep.dev/posts/jetbrains-coding-agent)
- [Cline: Improving Diff Edits by 10%](https://cline.bot/blog/improving-diff-edits-by-10)
- [Wichy File Editing Playbook](https://wuu73.org/aiguide/infoblogs/coding_file_edits/)
- [Martin Fowler: Harness Engineering](https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html)
- [Morph: AI Code Edit Formats Guide](https://www.morphllm.com/edit-formats)
- [GitHub: Claude Code issue #27137 — Write vs Edit content loss](https://github.com/anthropics/claude-code/issues/27137)
- [ddhigh: Harness Engineering — The Core Engineering Discipline of the AI Agent Era](https://www.ddhigh.com/en/2026/03/27/ai-agent-harness-engineering/)
- [WenHao Yu: Agent Harness — What Actually Determines Whether AI Delivers or Disappoints](https://yu-wenhao.com/en/blog/ai-harness/)
- [Eric Mann: The Agentic Harness Problem](https://eric.mann.blog/the-agentic-harness-problem-why-ai-agents-need-better-guardrails-than-code-reviews/)
- [OpenCode vs Codex CLI comparison (Morph)](https://www.morphllm.com/comparisons/opencode-vs-codex)
- [ast-grep: Structural search/rewrite tool](https://ast-grep.github.io/)
