# Pipeline Feature — Implementation Plan

**Goal:** Allow wichy to be used in a bash pipeline: `echo "fix the bug" | wichy --prompt` — single invocation, non-interactive, exits when done.

---

## Phase 1: `--last-ctx` flag

**Status:** DONE

**What:** Find and load the most recent context file by mtime, as shorthand for `--load-ctx`.

**Changes:**

- `cli_parser.py` — add `--last-ctx` boolean flag to `CliParser`, mapped to `CliConfig.last_ctx`
- `context/handler.py` — add `latest_context_file()` function: `max(glob, key=mtime)` on `settings.contexts_dir`
- `__main__.py` — after `--load-ctx` handling, check `args.last_ctx`: if true, call `latest_context_file()` and load via `context_from_file()`. Error if no context files exist.

**Mutual exclusivity:** `--last-ctx` and `--load-ctx` are mutually exclusive — if both given, print error and exit non-zero.

---

## Phase 2: `--prompt` flag

**Status:** DONE

**What:** Add `--prompt` flag that bypasses the REPL entirely. Single invocation, runs the agent with that prompt, exits.

**Changes:**

- `cli_parser.py` — add `--prompt` string flag to `CliParser`, mapped to `CliConfig.prompt`
- `__main__.py` — after agent build, check `if args.prompt is not None`. If so:
  - Skip `Repl` instantiation and `repl.run()` entirely
  - Call `root_agent.process(args.prompt)` directly
  - Print final response to stdout
  - Exit cleanly

**Wake-up behavior:**

- `--first` controls wake-up as specced — no implicit suppression
- `--prompt --first "fix it"` = prompt as first user message
- `--prompt "fix it"` = wake-up message fires first, then prompt

---

## Phase 3: `--prompt` + context flags

**Status:** DONE (2026-03-24)

**What:** Make context-loading flags work in combination with `--prompt`.

**Changes:**

- If `--last-ctx` given: load most recent context file before `root_agent.process(prompt)`
- If `--load-ctx` given: load specified context file before `root_agent.process(prompt)`
- All combinations work; `--first` respects phase 2 behavior

**Resulting truth table:**

| Flags                        | Context      | First initiative          | Behavior                                            |
| ---------------------------- | ------------ | ------------------------- | --------------------------------------------------- |
| `--prompt` alone             | New          | Agent (wake-up)           | Wake-up fires, then prompt, then exits              |
| `--prompt --first`           | New          | User                      | Prompt as first message, exits                      |
| `--prompt --last-ctx`        | Latest saved | Agent (wake-up)           | Wake-up fires on loaded context, then prompt, exits |
| `--prompt --load-ctx <file>` | Specified    | Agent (wake-up)           | Same, loaded context                                |
| `--last-ctx` alone           | Latest saved | Depends on loaded context | Normal REPL (existing behavior)                     |

---

## Phase 3.5: Pipeline-safe human verification

**Status:** DONE

**What:** When pipeline mode is active and human verification is required, auto-deny instead of blocking on stdin.

**Changes:**

- `human_verification.py` — added `PIPELINE_MODE = False` module-level flag
- `__main__.py` — sets `PIPELINE_MODE = True` when `--prompt` is set, before agent runs
- `human_verification.py` — in `require_human_verification` decorator: when `PIPELINE_MODE` is `True`, raises `PermissionError` with message before reaching `prompt("Proceed? (y/n)")`
- `skills/tools.py` — `SkillScriptTool.execute()` has its own inline verification loop (not using the decorator); also checks `PIPELINE_MODE` and raises `PermissionError`
- `cli_parser.py` — added `--prompt` flag to `CliConfig` and `CliParser` (needed to gate on)

**Behavior in pipeline mode:**

- Tool requiring verification → reaches `PIPELINE_MODE` check → auto-deny → `PermissionError` → LLM sees error, can retry or explain
- No silent skips, no blocking on stdin
- `SKIP_HUMAN_VERIFICATION` is ignored in pipeline mode

---

## Phase 4: Output suppression

**Status:** DONE

**What:** Suppress all rich user-facing output in pipeline mode. One central place to toggle output off.

**Changes:**

- `src/wichy/console/user.py` — created: `user_console = Console(quiet=False)` + `set_user_output_quiet(bool)`
- `src/wichy/console/__init__.py` — re-exports `user_console` and `set_user_output_quiet`
- All 8 files using `from rich import print` replaced with `from wichy.console import user_console` and `user_console.print(...)`:
  - `__main__.py`
  - `agent_builder.py`
  - `repl.py`
  - `skills/tools.py`
  - `context/handler.py`
  - `root_agent/root_agent.py`
  - `tools/human_verification.py`
  - `memory/zettelkasten/memory.py`
- `llm_backend.py` and `root_agent/root_agent.py` — `Console().print(...)` replaced with `user_console.print(...)`
- `__main__.py` — call `set_user_output_quiet(True)` when `--prompt` is set

---

## Phase 5: Auto-save context after pipeline run

**Status:** DONE (2026-03-24) — verified working.

**Verification:** `context_from_file()` sets `ch._path = path`, so `ContextHandler.append()` → `_write_line()` writes back to the original file after every `process()` turn. No changes needed.

**What:** After `root_agent.process(prompt)` completes in pipeline mode, the resulting context is saved to the same file it was loaded from (or a new file for fresh context). Enables chaining:

```bash
wichy --prompt --last-ctx "continue the plan"
wichy --prompt --last-ctx "what's next"
```

**Implementation:** `ContextHandler` already auto-saves via `append()` → `_write_line()`. The loaded context's `_path` is already set by `context_from_file()`. Each `append()` writes to the original file. Verify this works end-to-end; if not, force a flush/sync after `process()` returns.

**Expected behavior:** Works out of the box if `ContextHandler.append()` continues to call `_write_line()` after the pipeline `process()` call. Confirm no early-exit path skips the write.

---

## Files to touch

| Phase | Files                                                                  |
| ----- | ---------------------------------------------------------------------- |
| 1     | `cli_parser.py`, `context/handler.py`, `__main__.py`                   |
| 2     | `cli_parser.py`, `__main__.py`                                         |
| 3     | `__main__.py`                                                          |
| 3.5   | `human_verification.py`, `__main__.py`                                 |
| 4     | `console/user.py`, `console/__init__.py`, all 8 files with print calls |
| 5     | `__main__.py` (verify), `context/handler.py` (if needed)               |

---

## Dependencies

```
Phase 4  ──────────────────────────────── Phase 3.5
Phase 1  ────────────────────────────────────────────────────────── Phase 5
Phase 2  ────────────────────────────────────────────────────────── Phase 5
Phase 3  ────────────────────────────────────────────────────────── Phase 5
```

Most phases are independent and can be tackled in any order.
