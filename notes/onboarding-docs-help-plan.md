# Onboarding, Docs, Help & Safeguard Implementation Plan

Date: 2026-04-11

---

## 1. Refresh README.md and ABOUT.md

**Goal:** Bring both docs up to date with the current feature set.

### README.md — Changes

- **"What's Included" section**: Update the tool count (currently says "43+" — verify actual count). Add Session Map and result offload system to the feature list.
- **CLI flags section**: Add `--session-map`, `--max-backend-connections`, `--seq-exec` — these are missing.
- **Modes section**: Document pipeline mode (`--prompt`) properly.
- **Subcommands**: Clarify `wichy ls skills`, `wichy new skill`, `wichy install hooks`.
- **Quick start**: Mention the web UI auto-launching at `http://localhost:7891/` and what you'll find there.
- **First-run note**: Mention the first-run greeting feature (item 4a) once implemented.
- **`/help` command**: Mention the new `/help` REPL command (item 4b).

### ABOUT.md — Changes

- **Add Session Map section**: Document `--session-map` CLI flag, `read_session_map` tool, web UI at `/tools/session-map/`, LLM-based conversation structure extraction, SQLite persistence.
- **Add Result Offload section**: Document offload threshold (8000 chars default), `[RESULT_OFFLOADED]` reference, `query_result` tool, TTL, preview chars, and the new input cap (item 3).
- **Fix root agent default**: CLI default is `root-agent-basic`, not `root-agent-code-advanced`. Correct this.
- **Task agent types**: Ensure `data-analysis` agent is documented.
- **Document new features**: `wichy-help` skill (item 2), `/help` REPL command (item 4b), first-run greeting (item 4a).

### Files to change

- `/workspace/README.md`
- `/workspace/ABOUT.md`

---

## 2. Add "wichy-help" Default Skill

**Goal:** A self-knowledge skill the agent activates when users ask about wichy itself.

### Structure

```
src/wichy/skills/default/wichy-help/
└── skill.md
```

No scripts, references, or assets — purely knowledge.

### Frontmatter

```yaml
---
name: wichy-help
description: Self-knowledge about the wichy harness. Activate this skill when the user asks "what can you do?", "how do I use wichy?", "help me understand...", or any meta-question about the wichy system itself.
metadata:
  tags: [help, onboarding, meta, reference]
---
```

### Body sections

1. **When to Activate** — Trigger conditions: user asks about tools, skills, REPL commands, CLI flags, hooks, web UI, root agents, or anything "how does wichy..."
2. **Tool Categories** — Table of all ~35 tools grouped by category (File ops, Shell, Web, Task, DuckDB, Graph, Session, Skills, User interaction, Result offload) with tool name + one-line purpose
3. **REPL Commands** — All 8 `/` commands + `/help` with syntax and behavior
4. **Task Agent Types** — Bash, Explore, general-purpose, web-research, data-analysis — when to use each, the "lite research" flag, statelessness caveat
5. **Skills System** — How `activate_skill` works, `list_skills`, `search_skills`, `execute_skill_script`, `read_skill_file`, skill directory layout, `safe_scripts`
6. **Hooks System** — All 8 hook types, decorator syntax, `HookResult` actions, priority, `~/.wichy/hooks.py`
7. **Web UI** — 5 routes with URLs and what each does
8. **Root Agents** — The two built-ins, custom agents in `~/.wichy/root_agent_defs/`
9. **CLI Flags** — Grouped by function (model, tools, context, server, execution)
10. **Configuration** — `~/.wichy/` directory structure, `WICHY_` env var prefix, key settings with defaults
11. **Gotchas** — Common non-obvious behaviors (task agents are stateless, `replace_text` needs exact match, `write_file` overwrites, result offload thresholds, etc.)

### Writing style

Follow the `task-agents` skill's instructional style — explicit examples, tables where appropriate, imperative tone. Content should be written so the agent can relay it conversationally to the user, not just dump it. Each section: brief overview, enough detail to answer most questions, agent summarizes naturally rather than copy-pasting.

### Files to create

- `src/wichy/skills/default/wichy-help/skill.md`

### No other files need changes

The `SkillLoader.install_default_skills()` method in `loader.py` automatically discovers and copies any new directory under `src/wichy/skills/default/` to `~/.wichy/skills/` on next launch. No registration or `__init__.py` needed — just the directory with a `skill.md`.

---

## 3. Hard Cap on Offloaded Result Input to Summarizer

**Goal:** Prevent a massive stored result from blowing up the summarizer LLM's context when `query_result` is called.

### Problem

When `query_result` is called, `format_stored_results()` in `hijack.py` dumps the **entire** stored result content into the summarizer prompt with no size limit. If the stored result is huge (100K+ chars), it blows up the summarizer LLM's context window before it can even generate a response.

### Setting

Add to `src/wichy/config/settings.py`:

```python
# Max chars of stored result content fed to the summarizer LLM
# Prevents context overflow when querying very large offloaded results
query_result_max_input_chars: int = 100000
```

This follows the existing pattern — typed field with default, auto-picked up via `WICHY_` env prefix and `.env` file.

### Where to apply truncation

**Single point: `format_stored_results()` in `src/wichy/result_offload/hijack.py` (lines 163–182).**

Every path that formats results for the summarizer goes through this function. Truncating here covers both the summarizer and the validator (since the validator also calls `format_stored_results`).

### Implementation

Modify `format_stored_results()`:

```python
def format_stored_results(results: list) -> str:
    max_content_chars = settings.query_result_max_input_chars
    parts = []
    for i, r in enumerate(results, 1):
        content = r.content
        total = r.char_count  # already stored on the dataclass, no recomputation
        if total > max_content_chars:
            content = (
                content[:max_content_chars]
                + f"\n\n[result was truncated to {max_content_chars:,} chars from a total of {total:,} chars]"
            )
        parts.append(
            f"--- RESULT {i} ---\n"
            f"Reference ID: {r.ref_id}\n"
            f"Tool: {r.tool_name}\n"
            f"Size: {r.char_count:,} characters\n"
            f"Created: {r.created_at.isoformat()}\n\n"
            f"{content}"
        )
    return "\n\n".join(parts)
```

Key details:
- Uses `r.char_count` (already computed at save time, stored on `StoredResult`) — no `len()` recomputation needed.
- Truncation message format: `"result was truncated to n chars from a total of N chars"` — clear, unambiguous, no double meaning of "query".
- The truncation happens per-result, before the f-string interpolation into the full prompt.
- The same truncation automatically applies to the validator since it also calls `format_stored_results()`.

### Files to change

1. `src/wichy/config/settings.py` — add `query_result_max_input_chars: int = 100000`
2. `src/wichy/result_offload/hijack.py` — modify `format_stored_results()` to truncate `r.content`

---

## 4. First-Run Greeting + `/help` REPL Command

### 4a. First-Run Greeting

**Goal:** When wichy launches for the first time (no `~/.wichy/` directory exists), show a brief welcome before the REPL starts.

#### First-run detection

Check `settings.wichy_home.exists()`. **Timing is critical** — `install_default_skills()` (called earlier in `__main__.py`) creates `~/.wichy/skills/` and therefore `~/.wichy/`. So the check must happen **before** `initialize_skills()` is called.

#### Implementation

In `src/wichy/__main__.py`:

1. Before `initialize_skills()` (around line 104), save a boolean:
   ```python
   is_first_run = not settings.wichy_home.exists()
   ```

2. After the `SESSION_START` hook fires but before `repl.run()` (around line 322), conditionally print the greeting:
   ```python
   if is_first_run:
       user_console.print(
           "\n[bold green]Welcome to wichy![/bold green]\n"
           "It looks like this is your first run. Here's how to get started:\n\n"
           "  • Just type naturally — the agent understands plain English\n"
           "  • Type [cyan]/help[/cyan] to see what wichy can do\n"
           f"  • Web tools available at [cyan]http://{settings.server_host}:{settings.server_port}[/cyan]\n"
           "  • Skills, hooks, and root agents can be customized in [cyan]~/.wichy/[/cyan]\n"
       )
   ```

#### Files to change

- `src/wichy/__main__.py` — add `is_first_run` check and greeting print

### 4b. `/help` REPL Command

**Goal:** Typing `/help` passes a message to the root agent rather than printing a static menu. The agent can activate the `wichy-help` skill and respond naturally.

#### Current slash command system

`SlashCommandChecker.check_command(line)` (slash_commands.py:185) checks `line.startswith("/")`, looks up the command in `self._handlers` dict, and if found, calls the handler. The handler returns a string → REPL prints it → `continue`. The message never reaches the root agent.

8 existing commands: `/btw`, `/exit`, `/logging`, `/reset`, `/compact`, `/drop`, `/status`, `/hooks`.

Typing `/help` today produces: `"Unknown command: /help"`.

#### Design

We need `/help` to be recognized as a known command, but instead of returning a printable string, it should rewrite the message and let it fall through to `root_agent.process()`.

**Signal mechanism:** A new `PassToAgent` sentinel class:

```python
# In slash_commands.py
class PassToAgent:
    """Signal to the REPL that this command should be rewritten and sent to the agent."""
    def __init__(self, message: str):
        self.message = message
```

**`handle_help` function:**

```python
def handle_help(line: str) -> PassToAgent:
    # Extract anything after "/help" as a specific question
    parts = line.strip().split(maxsplit=1)
    specific = parts[1] if len(parts) > 1 else None

    if specific:
        return PassToAgent(
            f"[The user is asking for help: {specific}. "
            "Explain this aspect of wichy. Activate the wichy-help skill if available.]"
        )
    else:
        return PassToAgent(
            "[The user typed /help — they want to know what you can do and how to get started. "
            "Activate the wichy-help skill if available.]"
        )
```

This means:
- `/help` → "what can you do?" general help
- `/help how do skills work` → "explain skills specifically"
- The agent activates `wichy-help` if available, giving rich answers; without the skill, it can still give a reasonable answer from its system prompt.

**Register in `_handlers`:**

```python
"/help": handle_help,
```

**Update the REPL loop** in `src/wichy/repl.py` (around lines 66–69):

```python
possible_cmd = self.cmd_checker.check_command(line)
if possible_cmd is not None:
    if isinstance(possible_cmd, PassToAgent):
        line = possible_cmd.message  # rewrite and fall through to process()
    else:
        user_console.print(possible_cmd)
        continue
```

**Add to tab completer** so `/help` shows up in autocomplete (the `NestedCompleter` in `slash_commands.py`).

#### Files to change

1. `src/wichy/slash_commands.py` — add `PassToAgent` class, `handle_help` function, register `"/help": handle_help` in `_handlers`, update completer
2. `src/wichy/repl.py` — add `PassToAgent` import and check in the REPL loop (modify lines 66–69)

---

## Summary of All Files

| Item | File | Action |
|------|------|--------|
| 1. Docs refresh | `README.md` | Update |
| 1. Docs refresh | `ABOUT.md` | Update |
| 2. wichy-help skill | `src/wichy/skills/default/wichy-help/skill.md` | Create |
| 3. Input cap | `src/wichy/config/settings.py` | Add `query_result_max_input_chars` |
| 3. Input cap | `src/wichy/result_offload/hijack.py` | Modify `format_stored_results()` |
| 4a. First-run | `src/wichy/__main__.py` | Add first-run check + greeting |
| 4b. /help | `src/wichy/slash_commands.py` | Add `PassToAgent`, `handle_help`, register, update completer |
| 4b. /help | `src/wichy/repl.py` | Add `PassToAgent` check in loop |