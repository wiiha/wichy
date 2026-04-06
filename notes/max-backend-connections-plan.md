# Implementation Plan: `--max-backend-connections` / `--mbc`

**Feature:** Limit concurrent invocations of `llm_backend.call()` using a global `threading.Semaphore`.
**Status:** Planned — do not implement yet.
**Date:** 2026-04-05

---

## Overview

A new CLI flag `--max-backend-connections` (shorthand `-mbc`) limits how many threads can simultaneously be inside `llm_backend.call()`. Calls beyond the limit block until a slot is free. The default is no limit (`None`), preserving existing behavior unless the flag is passed.

**No call sites change.** All 18 existing callers invoke `call()` as before. The semaphore is internal.

---

## Files to Change (5 files)

| File | Change type | Approx. net lines |
|------|-------------|-------------------|
| `src/wichy/config/settings.py` | +1 field | +1 |
| `src/wichy/cli_parser.py` | field, type fn, arg, mapping | ~+20 |
| `src/wichy/__main__.py` | +1 assignment | +1 |
| `src/wichy/llm_backend.py` | imports, vars, helpers, wrapper+impl split | ~+35 |
| `tests/test_llm_backend.py` | new test class | ~+90 |

---

## File 1 of 5 — `src/wichy/config/settings.py`

**Change:** +1 line

**Target:** inside the `Settings` class, in the "Tool execution" section, after `skip_human_verification`.

```python
# Tool execution
parallel_exec: bool = True
skip_human_verification: bool = False
max_backend_connections: Optional[int] = None  # ← NEW: None = no limit
```

`Optional[int]` matches the `CliConfig` field type. `None` default means the semaphore is never created unless the user passes the flag.

---

## File 2 of 5 — `src/wichy/cli_parser.py`

**Changes:** +1 dataclass field, +1 type-checker helper, +1 `add_argument` call, +1 parse mapping (~20 lines total).

### 2a — `CliConfig` dataclass

**Target:** after `seq_exec: bool = False` (~line 25).

```python
seq_exec: bool = False
max_backend_connections: Optional[int] = None  # ← NEW
```

### 2b — `_positive_int` type-checker helper

**Target:** after imports (line ~6), before the `CliConfig` class definition (line ~8).

```python
def _positive_int(value: str) -> int:
    """Argparse type: must be a positive integer (>= 1)."""
    ival = int(value)
    if ival < 1:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a positive integer; "
            f"--max-backend-connections must be >= 1 (or omitted for no limit)"
        )
    return ival
```

This is the first custom type callable in the file. `argparse.ArgumentTypeError` is available via the existing `import argparse` on line 3. The function rejects non-integer input (argparse raises automatically) and values < 1 (explicit check).

### 2c — `add_argument` call

**Target:** after `--session-map` block (ends ~line 153), before `_add_global_arguments()` closes.

```python
self.parser.add_argument(
    "--max-backend-connections",
    "-mbc",
    type=_positive_int,
    dest="max_backend_connections",
    default=None,
    metavar="N",
    help="Limit concurrent llm_backend.call() invocations to N. "
         "Requests beyond N block until a slot is free. "
         "Default: unlimited. Sensible value to match existing parallelism: 8.",
)
```

**Design decisions:**
- `--max-backend-connections` and `-mbc` are declared in the same `add_argument` call (multiple option strings before kwargs)
- `type=_positive_int` validates N >= 1; argparse auto-rejects non-integer input
- `default=None` is redundant (optional arg without a value defaults to `None`) but makes intent explicit
- `metavar="N"` makes `--help` output `--max-backend-connections N` instead of `--max-backend-connections MAX_BACKEND_CONNECTIONS`
- No shorthand `-m` (already taken by `--model-str`)

### 2d — `parse()` mapping

**Target:** after `seq_exec=parsed.seq_exec` (~line 251).

```python
max_backend_connections=getattr(parsed, "max_backend_connections", None),
```

Follows the exact `getattr` pattern already used for `auto_compact_threshold` and `session_map_model`.

---

## File 3 of 5 — `src/wichy/__main__.py`

**Change:** +1 line

**Target:** after `settings.parallel_exec = not args.seq_exec` (~line 191).

```python
settings.parallel_exec = not args.seq_exec
settings.max_backend_connections = args.max_backend_connections  # ← NEW
```

No additional validation needed — `_positive_int` in the parser already ensures the value is either `None` (flag absent) or >= 1. An invalid value would have caused argparse to exit before reaching this line.

---

## File 4 of 5 — `src/wichy/llm_backend.py`

**Changes:** +imports, +module vars, +2 helper functions, +refactor `call()` into thin wrapper + `_call_impl()`. The existing `call()` spans lines 265–442.

### 4a — Add imports

**Target:** after existing imports (after line 9, before blank line 10).

```python
import threading
from threading import Semaphore
```

`time` is already imported on line 1; no change needed there.

### 4b — Module-level variables

**Target:** after imports, before exception class definitions (~line 11).

```python
# Global semaphore controlling concurrent call() invocations.
# Lazily initialized; None means "unlimited".
_semaphore: Optional[Semaphore] = None
_semaphore_lock = threading.Lock()
```

### 4c — `_get_backend_semaphore()`

**Target:** after `error_indicates_multimodal_not_supported` function (ends ~line 263), before `call()` (line 265).

```python
def _get_backend_semaphore() -> Optional[Semaphore]:
    """
    Return the global backend semaphore, creating it if needed.

    Thread-safe via double-checked locking. Returns None when
    settings.max_backend_connections is None (no limit).
    """
    if settings.max_backend_connections is None:
        return None
    global _semaphore
    if _semaphore is None:
        with _semaphore_lock:
            if _semaphore is None:
                _semaphore = Semaphore(settings.max_backend_connections)
    return _semaphore
```

**Double-checked locking pattern:** The outer `if _semaphore is None` avoids taking the lock on every call once the semaphore is created. The inner `if _semaphore is None` (after acquiring the lock) prevents two threads from racing to create the semaphore simultaneously.

### 4d — `call()` becomes a thin wrapper; rename existing body to `_call_impl()`

**Step 1 — Rename existing `def call(` to `def _call_impl(`** (line 265).

**Step 2 — Add explicit `retry_count` parameter to `_call_impl`** (line 265 signature).

Current:
```python
def call(context, tool_defs=None, model_str=None, extra_args=None, **extra_kwargs) -> LLMResponse:
```

Becomes:
```python
def _call_impl(
    context,
    tool_defs=None,
    model_str=None,
    extra_args=None,
    retry_count: int = 0,  # ← NEW: explicit parameter, replaces extra_kwargs.pop
    **extra_kwargs,
) -> LLMResponse:
```

**Step 3 — Replace `call()` with a thin wrapper** (line 265).

The new `call()`:
```python
def call(context, tool_defs=None, model_str=None, extra_args=None, **extra_kwargs) -> LLMResponse:
    """Make a single LLM API call..."""
    sem = _get_backend_semaphore()
    if sem is None:
        return _call_impl(context, tool_defs, model_str, extra_args, **extra_kwargs)
    with sem:
        return _call_impl(context, tool_defs, model_str, extra_args, **extra_kwargs)
```

`with sem:` acquires the semaphore on entry and releases it on exit — including if an exception is raised. No manual `acquire()`/`release()` needed.

**Step 4 — Remove `extra_kwargs.pop("retry_count", 0)`** (~line 391).

The line `retry_count = extra_kwargs.pop("retry_count", 0)` is **removed**. `retry_count` now arrives as an explicit named parameter with a default of `0` (Step 2 above).

### 4e — Retry loop: convert tail-recursive `return call(...)` to `return _call_impl(...)`

**Target:** inside the `except Exception as e:` block, in the rate-limit handling section (~lines 389–409).

**Current code:**
```python
if message_indicates_rate_limit(e):
    MAX_RETRIES = 3
    retry_count = extra_kwargs.pop("retry_count", 0)  # ← REMOVE (replaced by Step 4d)
    if retry_count >= MAX_RETRIES:
        raise LLMBackendRateLimitExceeded(retry_count=retry_count)
    backoff = 3 * (2**retry_count)
    console.log(
        f"got rate limited, will retry in {backoff} seconds (attempt {retry_count + 1})"
    )
    user_console.print(
        f"[dim][bold]→[/bold] LLM backend:[/dim] Rate limited, will retry in {backoff} seconds (attempt {retry_count + 1})"
    )
    time.sleep(backoff)
    return call(         # ← CHANGE: was recursive call() → now recursive _call_impl()
        context=context,
        tool_defs=tool_defs,
        model_str=model_str,
        extra_args=extra_args,
        retry_count=retry_count + 1,
        **extra_kwargs,
    )
```

**Why this is safe:** The semaphore is acquired once at the top of `call()` (via `with sem:`) and is held for the entire lifetime of the invocation — including all retries. The recursive `return _call_impl(...)` re-enters `_call_impl` on the **same thread**, does not re-enter `call()`, and therefore does not try to re-acquire the semaphore. There is no deadlock.

The old `return call(...)` would have been a deadlock hazard because it re-enters the public `call()` wrapper, which would try to `with sem:` again — and `Semaphore` is not reentrant. The `while` loop (or recursive `_call_impl`) avoids this because the semaphore boundary is only crossed once per `call()` invocation.

**Note on implementation approach:** The recursive `return _call_impl(...)` approach is chosen over a `while` loop to keep the diff minimal and the logic identical to the original. Both are safe; the recursive form is more readable as a diff.

### 4f — `_reset_backend_semaphore()`

**Target:** end of file (after `call()`, ~line 443).

```python
def _reset_backend_semaphore():
    """
    Reset the global semaphore. Call between tests to ensure clean state.
    Not part of the public API.
    """
    global _semaphore, _semaphore_lock
    _semaphore = None
    _semaphore_lock = threading.Lock()
```

Re-creates both the semaphore reference and the lock. The lock is re-created (rather than just reset) to handle the edge case where a test thread died while holding the lock.

### Module structure after changes

```
1-9     existing imports
10      NEW: blank
11      NEW: import threading
12      NEW: from threading import Semaphore
13      blank
14-60   3 exception classes (unchanged)
62-114  3 pydantic model classes (unchanged)
116-131 function: backend_and_model_from_model_str (unchanged)
133-183 function: parse_generic_backend (unchanged)
185-196 function: message_indicates_context_length_reached (unchanged)
198-207 function: message_indicates_rate_limit (unchanged)
209-263 function: error_indicates_multimodal_not_supported (unchanged)
265     NEW: _get_backend_semaphore()
        ...
        NEW: def call(...)  ← thin wrapper
        NEW: def _call_impl(...)  ← renamed from call(), +retry_count param
        [lines 274-442]   ← existing call() body, unchanged except retry loop (4e)
443+    NEW: _reset_backend_semaphore()
```

---

## File 5 of 5 — `tests/test_llm_backend.py`

**Changes:** add `threading` and `time` imports, add `TestMaxBackendConnections` class (~90 lines total).

### Imports

Add after existing imports at the top of the file:

```python
import threading
import time
```

### New class — `TestMaxBackendConnections`

**Target:** after `TestCallFunction` class (after line 245).

```python
class TestMaxBackendConnections:
    """Tests for the max_backend_connections semaphore."""

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_no_semaphore_when_limit_is_none(self, mock_settings, mock_openai):
        """When max_backend_connections is None, no semaphore is created."""
        mock_settings.max_backend_connections = None
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        from wichy.llm_backend import _get_backend_semaphore, _reset_backend_semaphore
        _reset_backend_semaphore()
        assert _get_backend_semaphore() is None

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_semaphore_created_when_limit_is_set(self, mock_settings, mock_openai):
        """When a limit is set, a Semaphore with that count is created."""
        mock_settings.max_backend_connections = 2
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        from wichy.llm_backend import _get_backend_semaphore, _reset_backend_semaphore
        _reset_backend_semaphore()
        sem = _get_backend_semaphore()
        assert sem is not None
        assert sem._value == 2  # Semaphore internal counter

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_second_caller_blocks_until_slot_frees(self, mock_settings, mock_openai):
        """Second caller blocks while first holds the semaphore; proceeds when freed."""
        mock_settings.max_backend_connections = 1
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        call_times = []
        def track_time(**kw):
            call_times.append(time.time())
            time.sleep(0.3)
            return self._mock_response("ok")
        mock_client.chat.completions.create.side_effect = track_time
        from wichy.llm_backend import call, _reset_backend_semaphore
        _reset_backend_semaphore()

        second_started = threading.Event()
        second_done = threading.Event()
        def second_caller():
            second_started.set()
            call([{"role": "user", "content": "hi"}], model_str="ollama/test")
            second_done.set()

        t = threading.Thread(target=second_caller)
        t.start()
        second_started.wait()  # wait for thread to start and block on semaphore
        time.sleep(0.05)

        # First call is still running; second is blocked
        assert len(call_times) == 1

        result = call([{"role": "user", "content": "hi"}], model_str="ollama/test")

        second_done.wait()
        # Second call happened after first finished
        assert len(call_times) == 2
        assert call_times[1] >= call_times[0] + 0.3

    @patch("wichy.llm_backend.OpenAI")
    @patch("wichy.llm_backend.settings")
    def test_semaphore_released_on_exception(self, mock_settings, mock_openai):
        """Semaphore is released even when call() raises, allowing next call to proceed."""
        mock_settings.max_backend_connections = 1
        mock_settings.ollama_base_url = "http://localhost:11434/v1"
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = RuntimeError("boom")
        from wichy.llm_backend import call, _reset_backend_semaphore
        _reset_backend_semaphore()

        with pytest.raises(RuntimeError):
            call([{"role": "user", "content": "hi"}], model_str="ollama/test")

        # Slot is free; second call succeeds (would deadlock if semaphore not released)
        mock_client.chat.completions.create.side_effect = None
        mock_client.chat.completions.create.return_value = self._mock_response("ok")
        result = call([{"role": "user", "content": "hi"}], model_str="ollama/test")
        assert result.message.content == "ok"

    # --- shared helper (mirrors existing inline pattern, no module-level helper added) ---
    def _mock_response(self, content: str):
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 5
        mock_usage.total_tokens = 10
        mock_message = MagicMock()
        mock_message.content = content
        mock_message.role = "assistant"
        mock_message.tool_calls = None
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_choice.finish_reason = "stop"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.model = "test-model"
        mock_response.usage = mock_usage
        return mock_response
```

**Test rationale:**
- `test_no_semaphore_when_limit_is_none` — verifies lazy-initialization path (no limit = no semaphore overhead)
- `test_semaphore_created_when_limit_is_set` — verifies semaphore is created with the right count
- `test_second_caller_blocks_until_slot_frees` — end-to-end proof of blocking behavior with real threads
- `test_semaphore_released_on_exception` — proves the `with sem:` context manager releases the lock even on exception (without this, the first test would cause a deadlock on subsequent tests)

---

## Edge Cases

| Edge case | Behavior |
|-----------|----------|
| `--max-backend-connections 0` | `_positive_int` rejects it with `ArgumentTypeError`; argparse prints usage and exits before any code runs. |
| Negative values | Same as above — `_positive_int` check `ival < 1` catches negatives. |
| `--mbc` with no value | argparse treats `-mbc` as an option requiring a value (no `nargs` configured); raises `argparse.error` if omitted. Correct. |
| Flag absent | `max_backend_connections=None` in `CliConfig` → `settings.max_backend_connections=None` → `_get_backend_semaphore()` returns `None` → `call()` takes the no-limit path. No behavior change. |
| Changing limit mid-session | Semaphore is created lazily on first `call()` invocation. It persists for the session. Changing the flag requires restarting. Consistent with how `parallel_exec` works. |
| Retry sleep holding the semaphore slot | Thread holds its slot while sleeping on rate-limit backoff. Other callers wait. This is correct — the limit applies to in-flight API calls. |
| Concurrent calls from main thread and thread pool workers | All callers share the same module-level `_semaphore`. Python's `threading.Semaphore` is thread-safe for `acquire()`/`release()`. The `_semaphore_lock` in `_get_backend_semaphore()` protects lazy initialization only. |
| Import order | `llm_backend.py` imports `settings` at module load time. By the time any `call()` is invoked, `__main__.main()` has already set `settings.max_backend_connections`. This is safe. |
| Deadlock from recursive `call()` with Semaphore | Eliminated by the `call()` → `_call_impl()` split. Semaphore is acquired once per `call()` invocation. Retry loop uses `return _call_impl(...)` (internal recursive call, no semaphore re-entry). |
| Test isolation | `_reset_backend_semaphore()` resets module state between tests. The `@patch` decorator also restores originals automatically. |

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Semaphore placement | Module-level in `llm_backend.py`, initialized lazily | Transparent to all callers; only created if limit is set |
| Default | `None` (no limit) | Sentinel pattern matches `auto_compact_threshold`; no behavior change unless flag is passed |
| Validation | Custom `_positive_int` type callable in argparse | Rejects 0 and negatives cleanly with a user-friendly error before any code runs |
| No call-site changes | ✅ | Semaphore is entirely internal to `call()` |
| Semaphore scope | Global across all callers | Session map extractor, sub-agents, memory system, all share the same limit |
| Retry recursion | `return _call_impl(...)` within `_call_impl` | Avoids deadlock (no re-entry of `call()` wrapper); keeps diff minimal |
| `_reset_backend_semaphore` | Re-creates lock with `threading.Lock()` | Handles dead-thread edge case where old lock may be permanently held |

---

## Implementation Order

1. `settings.py` — smallest, no dependencies
2. `cli_parser.py` — no dependencies on backend changes
3. `__main__.py` — 1 line, depends on 1 & 2
4. `llm_backend.py` — core change, depends on 1
5. `tests/test_llm_backend.py` — depends on 4

---

## Call Sites (unchanged — listed for reference)

All 18 call sites are synchronous and require no changes:

| Location | Method | Lines |
|----------|--------|-------|
| `root_agent.py` | `RootAgent.process()` | ~291, 304, 331, 343 |
| `root_agent.py` | `RootAgent.compact_context()` | ~465 |
| `tools/task/base.py` | `TaskAgent._process()` | ~173, 189, 199, 214, 252 |
| `session_map/extractor.py` | `SessionMapExtractor.extract()` | ~242 |
| `session_map/extractor.py` | `SessionMapExtractor.extract_with_validation()` | ~321, 342, 369 |
| `result_offload/query_tool.py` | `QueryTool._call_summarizer()` | ~177 |
| `result_offload/validation.py` | `validate_summarizer_response()` | ~82 |
| `memory/zettelkasten/memory.py` | `AgenticMemorySystem.analyze_content()` | ~141 |
| `memory/zettelkasten/memory.py` | `AgenticMemorySystem.process_memory()` | ~356 |
| `tests/test_llm_backend.py` | (unit tests) | ~161, 201, 240 |
