---
name: harnessed-build
description: >
  A structured multi-phase workflow for building software with agentic assistance.
  Use this skill whenever the user asks to implement, modify, or extend a codebase.
  Prevents the invisible-state trap, monolithic coding, and late-stage bug discovery.
  Trigger phrases: "build a", "implement", "add a feature", "create a", "fix the bug in",
  "refactor", "add support for", "integrate", "develop", or any open-ended coding task.
metadata:
  tags: [build, coding, workflow, agentic, harness, verification, architecture]
---

# Harnessed Build

A production-grade agentic workflow for building software. Based on research into agent anti-patterns and harness patterns, this skill ensures coding tasks converge to correct output instead of spiraling through repeated review rounds.

## Why This Exists

Without a harness, agents exhibit predictable failure modes that waste time and produce fragile code:

| Anti-Pattern | Symptom | Harness Fix |
| ---- | ---- | ---- |
| **Invisible State** | Agent "forgets" constraints found in round N-1 | External spec + checklist per phase |
| **Monolithic Mega-Prompt** | Single "write this feature" produces subtly wrong code | Phased delivery with deterministic gates between stages |
| **All-or-Nothing Autonomy** | Either uncontrolled edits or excessive friction | Calibrated stages: user approves spec+plan, agent executes, gate enforces |
| **No Verification Loop** | Bugs found only at "review time" | Each stage ends with a generate-evaluate-revise cycle |

## Workflow Overview

Every coding task follows five phases. **No phase may be skipped.** The agent may not begin Phase B until Phase A artifacts are locked. No stage may be merged with another.

```
A: Specification  →  B: Architecture Plan  →  C: Staged Implementation  →  D: Verification Loop  →  E: Deterministic Guard
      (user-led)       (agent-drafted,          (agent executes, gates            (agent self-critique)        (tools enforce)
                        user-locks)              block progress until pass)
```

### Who Does What

| Phase | User | Agent |
| ----- | ---- | ----- |
| A | Reviews + locks spec | Researches API, drafts spec, proposes invariants |
| B | Reviews + locks plan | Drafts file structure, state model, stage decomposition |
| C | Nothing (async) | Implements stages sequentially, runs gates autonomously |
| D | Approves or rejects critique | Performs structured self-critique against locked spec |
| E | Nothing (async) | Runs deterministic tools; fixes issues until all pass |

---

## Phase A: Specification

**Before any code is written.** The agent produces a structured spec. The user reviews it and *explicitly* confirms it is locked.

### A1. Agent: Research and Draft

Run a research task to produce:

1. **API/Domain Contract** — All endpoints, types, constraints, error cases.
2. **Invariant List** — Non-negotiable behavioral rules (e.g., "GET /messages is consumptive", "callback_data ≤ 64 bytes", "response body must be drained before close").
3. **State Model** — What is in-memory, what is persistent, what is per-user. Any concurrency model.
4. **Dependency Map** — External libraries, services, environment variables.

### A2. Agent: Output Format

Write the spec to `SPEC.md` in the project root using this template:

```markdown
# <Project> — Specification

## 1. Overview
What this is and what it does.

## 2. API / Domain Contract

### Endpoint Table
| Method | Path | Request | Response | Error Cases |
| ---- | ---- | ---- | ---- | ---- |
| GET | /health | — | `{"status":"ok"}` | 503 if degraded |

### Invariant List (Non-Negotiable)
1. **[INV-001]** GET /messages is consumptive: buffer clears on read.
2. **[INV-002]** callback_data must not exceed 64 bytes.
3. **[INV-003]** ...

## 3. State Model
In-memory: `CallbackStore`, `pendingAnswers`
Persistent: none
External: `wichy_server` at `127.0.0.1:7891`

## 4. Concurrency Model
Number of goroutines, what protects shared state, what is lock-free.

## 5. Configuration
| Env Var | Default | Required? | Notes |
| ---- | ---- | ---- | ---- |
| WICHY_SERVER_URL | http://127.0.0.1:7891 | no | Must include http:// scheme |

## 6. Security Model
Single authorized user, no server auth, network isolation assumptions.
```

### A3. User: Lock the Spec

The user must explicitly say: **"Spec locked"** before any further work. If the user requests changes after lock, return to A1.

**DO NOT PROCEED to Phase B without explicit user lock.**

---

## Phase B: Architecture Plan

**Before any code is written.** The agent drafts a detailed implementation plan. The user reviews and locks it.

### B1. Agent: Draft Plan

The plan must include:

1. **Package/file list** — Every file to create/modify with a one-line purpose.
2. **Data flow diagram** — Input → processing → output with stage boundaries.
3. **Stage decomposition** — Independent, verifiable increments (see Phase C).
4. **Verification method per stage** — Specific gate: builds? lints? passes which tests?
5. **Known risk list** — Areas of complexity that require extra scrutiny.

### B2. Agent: Output Format

Write the plan to `PLAN.md` using this template:

```markdown
# <Project> — Implementation Plan

## File Inventory
- `internal/config/config.go` — Env/config loading
- `internal/api/client.go` — HTTP client with drain-and-close discipline
- ...

## Stage Decomposition

| Stage | Deliverable | Files | Gate |
| ---- | ---- | ---- | ---- |
| 1 | Config + types | `config.go`, `types.go` | `go build`, `go vet`, unit tests |
| 2 | HTTP client | `client.go` | `go build`, `go vet`, mock server test |
| 3 | Poller core | `poller.go` | `go build`, `go vet`, consumptive-read test |
| 4 | Telegram wrapper | `bot.go`, `handlers.go` | `go build`, `go vet`, auth reject test |
| 5 | Callback system | `callbacks.go` | `go build`, `go vet`, token length ≤ 64b test |
| 6 | End-to-end | `main.go` wiring | Full integration test |

## Risk Register
- Risk: Concurrent access to shared maps → Mitigation: `sync.RWMutex` per map
- Risk: In-memory token store unbounded growth → Mitigation: hard cap + eventual prune
```

### B3. User: Lock the Plan

The user must explicitly say: **"Plan locked"** before any further work.

**DO NOT PROCEED to Phase C without explicit user lock.**

---

## Phase C: Staged Implementation

**The agent works alone.** Implement stages sequentially. Each stage is an independent, verifiable increment.

### C1. Rules for the Agent

- **One stage at a time.** Never start stage N+1 before stage N passes its gate.
- **Minimal edits.** Only create/modify files listed for the current stage in PLAN.md.
- **Self-verify before reporting.** Run the gate commands yourself. If they fail, fix them. Loop up to 3 times per gate.
- **Report result.** After each stage, report: gate commands run, output summary, and whether it passed.

### C2. Stage Gate Definitions (by language)

#### Go
```bash
# Minimum gate per stage
go build ./...
go vet ./...
# If tests exist in this stage:
go test -race ./...
```

#### Python
```bash
python -m py_compile <files>
# If configured:
ruff check .
pytest --tb=short
```

#### TypeScript / JavaScript
```bash
npx tsc --noEmit
npx eslint .
# If configured:
npx jest --passWithNoTests
```

### C3. When a Gate Fails (≤3 retries)

If a build/lint/test gate fails 3 times in a row on the *same* stage:

1. **Stop.** Report the failing output to the user.
2. **Do not guess.** Do not proceed to the next stage.
3. **Wait for user direction.** The user may loosen the gate, or provide a hint, or ask you to restart the stage.

### C4. Constraint Checklist (Apply During Every Stage)

Before declaring a stage done, verify these invariants against the locked spec (`SPEC.md`):

- [ ] No mutex is held across network I/O or external API calls.
- [ ] All HTTP response bodies are drained before close.
- [ ] No struct field loaded from config is left unused.
- [ ] All callback/token/payload sizes respect documented limits.
- [ ] All goroutines have panic recovery or are supervised.
- [ ] All shared mutable state is protected by a sync primitive.
- [ ] No unbounded map growth without documented cap or TTL.
- [ ] All errors are propagated or logged; none are silently discarded.
- [ ] No plaintext secrets logged or returned in error messages.
- [ ] All interpolated strings in APIs (Markdown, SQL, etc.) are escaped or parameterized.

If any item violates the SPEC, the stage is **not done**. Fix it.

---

## Phase D: Verification Loop

**After all stages pass gates.** A structured self-critique against the locked spec.

### D1. Agent: Structured Self-Critique

Treat this as a formal review. For every invariant in `SPEC.md`, quote the exact line(s) where it is enforced.

Then produce `VERIFICATION.md`:

```markdown
# Verification Report

## Invariant Coverage

| Invariant ID | Location | How Enforced | Status |
| ---- | ---- | ---- | ---- |
| INV-001 | `internal/api/client.go:87` | `DrainBody()` before `resp.Body.Close()` | OK |
| INV-002 | `internal/callbacks/callbacks.go:42` | `AssertMaxTokenLen()` in tests | OK |
| INV-003 | ... | ... | MISSING → see below |

## Gaps
1. **INV-003** (no retry on 5xx): Not implemented.
   Risk: transient failures lose consumptive messages.
   Recommendation: add bounded retry with backoff.

## User Decision Required
- Accept gap #1 as known limitation? (Y/n)
- Should I implement retry before closing verification?
```

### D2. User: Review and Decide

The user reviews gaps. For each gap:
- **Accept** — document as known limitation in SPEC.md and proceed.
- **Fix now** — return to Phase C for a targeted fix + gate.
- **Defer** — create a GitHub issue / TODO with a deadline.

**DO NOT mark the task "done" until the user explicitly accepts or defers all gaps.**

---

## Phase E: Deterministic Guard

**Final stage.** Run static analysis tooling that deterministically catches bugs agents miss.

### E1. Run the Tool Suite

#### Go
```bash
go test -race ./...          # catches data races
go vet ./...                 # catches common mistakes
staticcheck ./...            # catches unused code, ineffectual assignments
```

#### Python
```bash
pytest --tb=short
ruff check .
mypy . --strict (if configured)
```

#### TypeScript
```bash
npx tsc --noEmit
npx eslint . --max-warnings=0
```

### E2. Fix Until Clean

The agent runs the full tool suite and fixes every issue until clean output. This is non-negotiable.

**The task is not complete until the user says "Looks good" or the tool suite is clean without user objections.**

---

## Examples

### Example 1: New Feature Request

**User says:** "Add support for polling pending questions from the wichy_server API and presenting them as inline keyboards."

**Agent actions (following this skill):**

1. **Phase A:** Research wichy_server API (questions endpoint), draft SPEC.md with invariant: "questions may expire; answers must reference existing QIDs". Wait for: "Spec locked".
2. **Phase B:** Decompose into stages: (1) Question type + JSON client, (2) Poller integration, (3) Inline keyboard builder, (4) Batch-answer collection (/done command), (5) Submit to server. Wait for: "Plan locked".
3. **Phase C:** Implement stages 1–5, running `go build && go vet` (and tests) between each.
4. **Phase D:** Self-critique: confirm QID existence check before answer storage, confirm unbounded `pendingAnswers` map has a cap or prune, quote enforcing code.
5. **Phase E:** `go test -race ./...`, `go vet`, fix any races or staticcheck warnings.

### Example 2: Bug Fix Request

**User says:** "There's a race condition in the callback handler."

**Agent actions (following this skill):**

1. **Phase A (mini):** Briefly confirm the bug description against current code. Document the race as a temporary invariant in `SPEC.md` (or `BUG.md` if no project SPEC exists yet). Wait for lock.
2. **Phase B (mini):** Propose a fix approach: "Replace shared map access with atomic.Value for the token store, or use a mutex but scope it to only map ops, not Telegram sends." Wait for lock.
3. **Phase C:** Implement the fix in one stage. Gate: `go test -race ./...` specifically targeting the callback path.
4. **Phase D:** Confirm the race is gone by explaining why the new access pattern is safe.
5. **Phase E:** Full tool suite passes cleanly.

---

## Common Failure Modes (and How the Harness Prevents Them)

| Symptom | Root Cause | Harness Prevention |
| ---- | ---- | ---- |
| Bug fixed in round N, reintroduced in round N+2 | Invisible state — constraint only in chat history | Lock spec → invariant checklist survives sessions |
| Agent writes 5 files for a 1-file change | Monolithic prompt → unscoped autonomy | Stage decomposition restricts files per stage |
| Code reviewed by eye and still has race condition | No verification loop + no race test | Phase D checklist + Phase E `go test -race` |
| "It works on my machine" | No lint/build gate enforced | Phase C gates must pass deterministically |
| Agent forgets to drain HTTP bodies | Constraint not tracked externally | Invariant checklist item, `go vet` may catch |
| Security config loaded but never used | No static analysis to find unused fields | Phase E `staticcheck` catches unused fields |

---

## Critical Rules (Non-Negotiable)

1. **No code before spec lock.** No code, no scaffolding, no `go mod init`.
2. **No plan before spec lock.** Architecture depends on invariants; invariants live in the spec.
3. **No stage N+1 before stage N gate passes.** Parallel stages are only allowed if PLAN.md explicitly lists them as independent.
4. **The gate is the definition of done.** A stage is not done when "it looks right." It is done when the gate commands succeed.
5. **Self-critique is mandatory.** The agent must quote the code that enforces each invariant. "Trust me, it's safe" is not valid.
6. **Deterministic tools are the final authority.** Static analysis, race detectors, linters — these catch what a code review agent misses.

> **If the user says "just do it" or "skip the spec"**: Politely refuse. Explain that the harness exists precisely because "just do it" produces the bugs listed above. Offer to do a *minimal* version of Phase A (2-3 sentences) if the feature is genuinely trivial.
