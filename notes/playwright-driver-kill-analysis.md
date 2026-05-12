# Playwright Driver Kill -- Post-Mortem

**Purpose:** Analysis of what happened when I accidentally killed the Playwright browser driver process in the wichy container, why `web_fetch` broke but `browser_navigate` still worked, and the broader zombie process problem.

**Date:** 2026-05-11

---

## What I Did

Ran `kill -9` on several processes:
- PID 1628: Playwright Node driver (`cli.js run-driver`)
- PIDs 1639, 1641, 1642, 1655, 1659: Chromium `headless_shell` children
- PIDs 63413, 63450: Additional Chromium renderers
- PIDs 55246, 55249, 55251: Orphaned `ddgs` processes

**Result:** `web_fetch` tool immediately returned:
> "Connection closed while reading from the driver"

But `browser_navigate` and `browser_page_info` continued working.

---

## Why `web_fetch` Broke But Browser Tools Worked

### Architecture

All web tools share **one** `BrowserManager` singleton inside the wichy server (PID 1). It runs a dedicated asyncio event loop in a daemon thread and maintains:
- One Playwright instance
- One Chromium browser
- One BrowserContext
- One persistent Page

Source files:
- `/opt/wichy/lib/python3.12/site-packages/wichy/helpers/browser.py` -- BrowserManager
- `/opt/wichy/lib/python3.12/site-packages/wichy/tools/fetch_webpage.py` -- Tool definitions

### The Bug: Stale Page Reference + No Recovery in `web_fetch`

When I killed the driver, the `BrowserManager` still held stale Python object references to the dead browser/context/page.

**`browser_navigate` has built-in crash recovery** inside `navigate()`:
```
navigate() -> page.goto() fails -> detects crash -> _recover_from_crash()
-> spawns new browser + new page -> retries -> succeeds
```

**`web_fetch` in `_fetch_webpage()` does NOT have recovery:**
```
_fetch_webpage():
  page = await get_page()              # Gets stale dead page
  await navigate(...)                   # Triggers recovery internally
  # But navigate() creates a NEW page, replaces manager's _page
  await page.content()                  # Uses STALE page -> CRASHES
```

`web_fetch` caches the page reference before `navigate()`, then calls `page.content()` on the stale object after `navigate()` replaced the manager's internal page with a fresh one.

### Health Check Also Buggy

`_is_browser_alive()` checks `self._browser.contexts` -- a purely local Python `set()` call that does **not** communicate with the driver. So it returns `True` even when the driver is dead.

### Timeline

1. Kill driver + chromium processes
2. `web_fetch` called first -> gets stale page -> `navigate()` recovers but `page.content()` fails on stale ref -> error
3. `browser_navigate` called second -> recovery already completed -> new browser alive -> works
4. `web_fetch` called third -> now works too (auto-recovery done)

| Tool | Recovery built-in? | Caches stale Page? | Survives driver kill? |
|------|-------------------|-------------------|-----------------------|
| browser_navigate | Yes | No | Yes |
| browser_page_info | Yes | No | Yes |
| browser_raw | Yes | No | Yes |
| browser_screenshot | Yes | No | Yes |
| browser_act | Yes | No | Yes |
| web_fetch | **No** | **Yes** | **No** |

---

## The Zombie Process Problem

### Current State After Kill

The container accumulated zombies across multiple sessions:
- **May 9:** 3 scheduler/tmux/go/git zombies
- **May 10:** 4 scheduler/tmux zombies
- **May 11:** 3 ddgs zombies + 10 headless_shell zombies (from this kill)
- **Total:** ~20 zombie processes in the process table

### Root Cause: PID 1 Does Not Reap Children

In Linux, when a process's parent dies before reaping it, the kernel re-parents it to PID 1. PID 1 **must** call `wait()` on these children to clean them up.

The wichy Python server at PID 1 does **not** handle `SIGCHLD` or call `os.waitpid()`. So every orphaned child becomes a zombie and stays in the process table forever.

This is a classic Docker issue: containers without a proper init system (`tini`, `dumb-init`, `systemd`) accumulate zombies.

### Prevention

| Approach | Fix |
|----------|-----|
| Container level | Run Docker with `--init` or use `tini` as entrypoint |
| Wichy level | Add `signal.signal(signal.SIGCHLD, lambda s, f: os.waitpid(-1, os.WNOHANG))` |
| Agent level | Never `kill` Playwright/Chromium processes directly. Let Playwright manage its lifecycle |

---

## Recovery Options (Without Container Restart)

The `BrowserManager` has built-in `_recover_from_crash()` that auto-triggers on the next tool call. Options ranked by safety:

1. **Wait and retry** -- `web_fetch` auto-recovers on the next call (confirmed: worked on second attempt)
2. **Call `browser_status`** -- triggers health check, may force recovery
3. **Kill the new driver** (if recovery is stuck) -- `kill` the current `run-driver` PID, then retry
4. **No admin endpoint exists** -- wichy has no `/api/browser/reset` or similar. Cannot signal PID 1 externally

### How Playwright Driver Communicates

- Via stdio pipes (length-prefixed JSON), not sockets
- PID 1 holds file descriptors 11 (write) and 18 (read) to the driver
- Driver binary: `/opt/wichy/lib/python3.12/site-packages/playwright/driver/node` (120MB)
- No standalone restart script -- only launched via Python `async_playwright()` API

---

## Lessons Learned

1. **Do not `kill` browser processes** -- Playwright manages its own lifecycle. Killing breaks the pipe without notifying the Python side.
2. **`web_fetch` has a bug** -- it caches a stale `Page` reference across `navigate()` which replaces the manager's page. It should re-fetch the page after navigation or use a manager method with recovery.
3. **`_is_browser_alive()` is too weak** -- checking `self._browser.contexts` is purely local. Should probe the actual driver (e.g., `page.url` or `browser.version`).
4. **Container needs an init reaper** -- the zombie accumulation will continue across sessions until wichy handles `SIGCHLD` or the container uses `tini`.
5. **`browser_navigate` is more robust than `web_fetch`** -- direct browser tools all have crash recovery built into their manager methods.

---

*Written 2026-05-11. Based on analysis of `/opt/wichy/lib/python3.12/site-packages/wichy/helpers/browser.py` and `/opt/wichy/lib/python3.12/site-packages/wichy/tools/fetch_webpage.py`.*
