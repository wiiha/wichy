# Wichy Follow-up Ideas: Revised Plan

## 1. CORRECTIONS & CLARIFICATIONS

### First-turn message injection
- **Current state:** No automatic injection occurs. The "you just woke up" message was manually written by you during testing.
- **Needed:** Both the injection feature AND the `--first` flag to inhibit it.
- **Implementation location:** Likely in `__main__.py` before entering REPL loop, or in `Repl.run()` before first prompt.

### Web GUI line wrap issue
- **Problem:** Files with very long lines (e.g., minified JavaScript) cause horizontal overflow/ugliness.
- **Solution:** CSS `overflow-wrap: anywhere;` or `word-break: break-all;` on the message content container in context editor.
- **Not** about tool call JSON formatting.

### `/btw` command
- **Category:** Slash command (handled by `SlashCommandChecker` before reaching RootAgent)
- **Not** a TaskAgent tool. Should be implemented as a separate command handler.
- **Behavior:** Spawns a single-turn agent with current context (read-only copy), no tools, returns answer as "[BTW]" to user, does not modify main context.
- **Implementation:** New method in `Repl` or new slash command class that creates temporary `ContextHandler` from current context, calls LLM with no tools, returns result.

### Web GUI: User notepad
- **Architecture:** Sibling feature to graph and context editors – separate blueprint/template under `/tools/notes/` or similar.
- **Storage:** Plain markdown files in `.wichy/notes/`, one per note with UUID filenames.
- **Frontmatter:** Each file should have frontmatter with metadata including a user-chosen title/name (not just UUID).
- **Agent access:** New `SearchNotesTool`, `ReadNotesTool` (agent can query but not write initially).
- **UI:** Simple markdown editor (consider EasyMDE or similar) with preview.

### Skill output format (activate_skill & skill_search)
**activate_skill** currently returns JSON with markdown_content inside. Desired: return **full markdown directly** (the `skill.md` content including frontmatter and body) as the primary output, not wrapped in JSON.

**skill_search** currently returns JSON array of skill metadata. Should return **markdown** (likely a table or list) for better LLM consumption.

**Additional cleanup:**
- Remove the `path` field from output (filesystem path not needed by agent/user).
- Deduplicate tags: currently showing twice (once in `metadata` and once as `tags` field). Should only appear once, under `metadata` or as a separate line in markdown output.
- `markdown_content` field includes frontmatter which duplicates `metadata` – if returning markdown directly, this redundancy disappears.

**Proposed format for `activate_skill`:**
Simply return `skill.markdown_content` (the full content of `skill.md`). That's what "activation" means – loading the knowledge.

**Proposed format for `skill_search`:**
Return markdown like:
```markdown
## Found 3 skills

- **napkin** (memory, runbook, workflow)
  - Runbook maintenance tool
- **web-search** (web, search, ddg)
  - DuckDuckGo search integration
...
```
Or a table format. Keep it simple, readable.

---

## 2. GROUPED ROADMAP (No time estimates)

### A. Core Infrastructure & Reliability

1. **Token usage tracking**
   - Modify `llm_backend.py:call()` to return usage data (prompt_tokens, completion_tokens, total)
   - Track cumulative usage in `RootAgent` (for auto-compaction)
   - Expose via context logs and `/status` endpoint

2. **Model fallback on failure**
   - Catch backend errors in `llm_backend.py`
   - Config: `WICHY_FALLBACK_MODEL` (or comma-separated list)
   - Retry with fallback(s) on connection/API failures
   - Log fallback attempts

3. **Unparsed tool call handling**
   - Detect tool calls embedded in content (e.g., `<tool_call>` XML tags, code blocks with JSON)
   - Add fallback parser in `RootAgent.handle_tools()` after LLM response
   - Distinguish actual calls from discussion (heuristic: isolated code block vs narrative)
   - Retry extraction or prompt model to format properly
   - Research: Implement multi-format parser (Anthropic XML, OpenAI JSON, Gemini functionCall, Ollama narrative)

4. **First-turn injection**
   - Add default behavior: inject "You just woke up..." system message before first user turn
   - Add `--first` / `--no-first-message` flag to disable
   - Implementation: Flag in `CliParser`, condition in `Repl.run()` or before `RootAgent.process()`

5. **Sub-agent context lineage**
   - Tag sub-agent context files with parent context reference
   - Use `context.add_log()` with `{"type": "sub_agent_link", "parent_context": "...", "sub_agent_file": "..."}`
   - Or embed parent context filename in sub-agent's filename
   - Improve context viewer to show hierarchy

6. **ESC key interrupt**
   - Add key binding in prompt_toolkit `PromptSession` to ESC
   - Should behave like Ctrl+C (interrupt current agent processing)
   - Check existing bindings; likely just add to `key_bindings`

### B. Web UI Polish & Features

1. **Force line wrap in context editor**
   - Add CSS rule: `.message-content { overflow-wrap: anywhere; }` or `word-break: break-word;`
   - Fix long unbroken lines (minified JS, long URLs)

2. **Delete individual messages**
   - Add `ContextHandler.delete_message(index)` if not exists
   - Add UI button and API endpoint `DELETE /tools/context/api/messages/<index>`
   - Confirm dialog for safety

3. **Smart auto-scroll in context viewer**
   - JavaScript: if scroll near bottom before update, auto-scroll to bottom after
   - If user scrolled up, preserve position

4. **User notepad (sibling feature)**
   - Blueprint: `src/wichy/tools/notes/` with `/tools/notes/` route
   - Templates: `notes.html`, static: `notes.js`, `notes.css`
   - Storage: `.wichy/notes/` with markdown files (`uuid.md`) having frontmatter `title: ...`
   - CRUD API: list, create, read, update, delete
   - Tools: `SearchNotesTool`, `ReadNotesTool` (read-only for now)
   - Agent cannot write (future enhancement)

5. **Context editor full rewrite instead of truncate**
   - Extend `ContextHandler.replace_message(index, new_content)` already exists
   - Add UI: edit button that replaces truncate action with full text editor
   - Or add "delete" as alternative

### C. Advanced Agent Capabilities

1. **`/btw` slash command**
   - Handler in `SlashCommandChecker` or `Repl` for `/btw <question>`
   - Creates temporary context = copy of current messages (read-only)
   - Spawns agent with no tools (or minimal tools like `AskUserQuestion` if needed)
   - Single turn: user question → LLM response (not added to main context)
   - Display with `[BTW]` prefix or special formatting
   - Clean up temporary context

2. **Auto-compaction by token count**
   - Flag: `--auto-compact <tokens>` (threshold)
   - After each turn, check usage cumulative; if exceeded, trigger compaction before next LLM call
   - Compaction prompt: different from `/compact` – focus on preserving ability to continue current task/mission
   - System prompt never compacted (always kept at front)
   - Implementation: New compaction strategy in `ContextHandler` or `RootAgent`

3. **Agent self-modify context tool**
   - New tool: `SelfModifyContextTool` (high security, maybe require human verification)
   - Operations: delete_message, truncate_message, replace_message (on own context)
   - Operates on `self.context` (the agent's own Active context)
   - Guardrails: only recent N messages, or require explicit permission

4. **USER.md editable profile**
   - File: `~/.wichy/USER.md` (markdown with frontmatter)
   - Tool: `UpdateUserProfileTool` (or allow `WriteFileTool` to that location with restrictions)
   - Research-based design: append-only vs merge-update strategy
   - System prompt reminder: "Update USER.md when you learn new preferences"
   - Consider event-sourcing: append events to `USER.log` and derive profile
   - Git-backed for durability

5. **PDF support in ReadFileTool**
   - Add docling as optional dependency
   - Detect `.pdf` extension
   - Use docling to convert to markdown text
   - Fallback: error message suggesting docling installation
   - Alternative: try `pdf2image` + OCR if docling unavailable (but heavier)

### D. Skill System Improvements

1. **activate_skill output**
   - Change from JSON to plain markdown: return `skill.markdown_content` directly
   - Remove JSON wrapper
   - If metadata needed, return separate field via new tool or parameter

2. **skill_search output**
   - Change from JSON to markdown (table or list)
   - Format: skill name, description, tags, script count
   - Remove `path` and `matches_in_content` (or include as footnote)
   - Deduplicate tags (show only once)

3. **Markdown-defined task agents**
   - Implement loader for task agent definitions from markdown files
   - Create `~/.wichy/task_agent_defs/` and `.wichy/task_agent_defs/`
   - Reuse `read_markdown_with_frontmatter()` and `TaskAgentDefinitionBase` parsing
   - Combine with built-in definitions in `TASK_AGENT_DEFS`
   - Add template generator via `wichy new taskagent`

### E. Research & Experimentation

1. **Anthropic team/agent patterns review**
   - Already done: see findings on orchestrator-worker, parallelization, context engineering, tool design
   - Potential application: improve TaskAgent coordination, implement team lead pattern
   - No immediate code change – inform future design

2. **LLM file update reliability**
   - Already researched: three-tier memory, atomic operations, checkpoint-validate-recover, append-only vs rewrite
   - Apply patterns to `UpdateUserProfileTool` and other file-modifying tools
   - Consider git integration for durability

3. **PDF processing landscape**
   - Already researched: docling (recommended), PyMuPDF, pdfplumber, pdf2image+OCR
   - Implement with docling as primary, fallbacks optional

4. **Multi-format tool call parsing**
   - Already researched: Anthropic (tool_use blocks), Gemini (functionCall), OpenAI (tool_calls), Ollama (narrative)
   - Implement universal extractor in `RootAgent.handle_tools()` as fallback
   - Use heuristics: JSON code blocks, XML tags, validate against tool schemas

---

## 3. DEPENDENCIES & SEQUENCING

```
Token usage tracking → Auto-compaction, usage bubbling
First-turn injection → Independent
ESC key → Independent
Line wrap → Independent
Delete message → Independent
Smart scroll → Independent

Markdown task agents → Independent
activate_skill format → Independent
skill_search format → Independent

PDF support → Depends on docling integration decision
Self-modify context → Depends on token tracking maybe
USER.md → Depends on file update reliability patterns
/btw → Independent
Sub-agent lineage → Independent
Model fallback → Independent
Tool call parsing → Independent but could be phased: first OpenAI/Anthropic, then generic
User notepad → Independent

Web notepad blueprint → Sister feature, independent
```

Most items are independent and can be tackled in any order. Prioritize by personal workflow pain points.

---

## 4. NOTES ON IMPLEMENTATION APPROACH

- **Esc key:** Add to `prompt_toolkit` key bindings in `repl.py`. Look for `@self.prompt_session.key_bindings.add('c-c')` pattern.
- **First-turn:** In `__main__.py`, after building agent and before REPL start, check `args.first` flag; if false, `root_agent.context.add("system", FIRST_MESSAGE)`.
- **Token tracking:** `llm_backend.call()` already gets `response.usage` from OpenAI client. Need to return it alongside `Message`. Modify callers to accumulate.
- **Auto-compaction:** After `RootAgent.process()`, if `self.total_tokens > threshold`, call `context.compact()` with custom prompt before returning.
- **`/btw`:** Add to `SlashCommandChecker.COMMANDS` as `BtwCommand`. Implementation: copy context, single-turn LLM call with no tools, format result with `[BTW]` prefix.
- **Skill output:** Change `SkillInfoTool.execute()` to `return skill.markdown_content`. Change `SkillSearchTool.execute()` to build markdown string (table/list) and return that.
- **Markdown task agents:** Create `src/wichy/tools/task/loader.py`, add settings properties, integrate into `agents.py`. Follow root agent pattern.
- **User notepad:** Create new blueprint module like `src/wichy/tools/notes/__init__.py` with routes, templates, static. Use existing `list_files`, `read_file`, `write_file` patterns but isolated to `.wichy/notes/`.
- **Tool call parsing:** In `RootAgent.handle_tools()`, after receiving response, if `response.tool_calls` is empty, run `UniversalToolCallExtractor` on response.content. If extracts candidates, validate and execute, then possibly request proper format.

---

**End of Revised Plan**
