---
name: agent-notebook
description: Always activate this skill on startup. It provides a durable, searchable SQLite notebook for cross-session memory. Before making any non-trivial decision, query the notebook for past context. After any notable learning, write to it proactively. The notebook lives at `.wichy/notebook.db` and dumps to `.wichy/notebook.sql` for Git versioning.
safe_scripts: init_notebook.py, dump_notebook.py
metadata:
  tags:
    [
      memory,
      sqlite,
      agent-memory,
      sessions,
      notebook,
      learning,
      persistence,
      knowledge-store,
    ]
---

# Agent Notebook

A project-local SQLite database where agents record durable observations across sessions.
Unlike chat history (which is ephemeral), the notebook is queryable, searchable via FTS5,
and survives container resets.

The notebook lives at `<project>/.wichy/notebook.db`.
Every agent instance in the same repo should query it and write to it,
but schema migrations are the owner's responsibility. Start simple and add fields only
when you need them.

## Core Philosophy

- **Initialize automatically.** If `notebook.db` does not exist, always run `init_notebook.py`
  before doing any other work in this project. Every project deserves memory.
- **Write proactively, not reactively.** Capture decisions and gotchas as they happen,
  not when the user asks.
- **Query before acting.** Before making significant choices -- opening a file,
  interpreting ambiguous instructions, or working in a project with history -- search
  the notebook for prior context.
- **Curate ruthlessly.** Max 3-5 notes per session. Merge, drop, or invalidate stale
  entries on every read. An unmaintained notebook is worse than none.
- **Git the dump, not the binary.** The `.db` is untracked; dump to `.wichy/notebook.sql`
  and commit that. Keep the binary as a living workspace.

## Why This Matters: Agent Memory as Thinking, Not Filing

Stateless agents start from zero every session. Past decisions, user preferences,
project context -- all evaporate. The notebook is how you stop repeating yourself.

### How Agent Memory Works

Three approaches:

1. **Notebook + Reference Documents hybrid** (recommended)
   Short-form notes in SQLite (observations, decisions, gotchas) plus long-form
   documents in Markdown (plans, reasoning, project overviews). The notebook
   indexes into the documents. Query the notebook first; follow references
   into documents for depth.

2. **Bi-directional linking** (advanced)
   Notes link to other notes. When you write about a decision, link to the
   related gotcha. When you query, follow links to surface connected ideas.
   This is what makes memory a thinking tool, not a filing cabinet.

3. **Single flat notebook** (simplest)
   Everything in one table. Searchable, fast, no schema complexity. Good enough
   for most solo agents. Upgrade when cross-referencing becomes painful.

For most agents, the hybrid approach is the sweet spot.

### Habits That Make Memory Work

**Query before acting.** Before making a decision in a project with history,
search the notebook. "Have I seen this error before?" "What did the user prefer?"
If you never query, you are hoarding, not thinking.

**Decision tracking.** Log not just what you chose, but why. The rejected
alternatives matter as much as the winner. Tag each decision with its domain.

**Organize memory with PARA.**

- **Projects:** Active work with a goal and deadline.
- **Areas:** Ongoing responsibilities (user preferences, recurring tasks).
- **Resources:** Reusable knowledge (commands, patterns, examples).
- **Archives:** Finished work. Review monthly -- much of it becomes Resources.

**Connection over collection.** A notebook with 50 isolated notes is worth less
than one with 10 linked notes. When you write about a gotcha, link it to the
decision it affected and the context that produced it.

### Pitfalls

1. **Collecting without querying.** If you log but never search, it is dead weight.
2. **Letting memory decay.** Old context misleads. Archive aggressively and
   invalidate decisions that no longer apply.
3. **Over-structuring too early.** Start with simple tables. Add complexity only
   when querying becomes painful.
4. **Treating the notebook as a log.** Session transcripts belong elsewhere.
   The notebook is for insights, decisions, and patterns -- not events.

For a deeper dive, see the full primer: `references/primer.md`.

---

## Database Schema

### `sessions`

One row per session.

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    started_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT,
    summary TEXT,
    projects TEXT          -- JSON array of project tags
);
```

### `notes`

The actual learnings.

```sql
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    kind TEXT NOT NULL CHECK(kind IN (
        'decision',       -- We chose X over Y. Why.
        'mistake',        -- What went wrong, root cause, fix.
        'insight',        -- Non-obvious thing that works.
        'gotcha',         -- Recurring trap, surprising behavior.
        'pattern',        -- Confirmed theme across 2+ sessions.
        'context'         -- Domain state worth remembering.
    )),
    body TEXT NOT NULL,    -- Max 3 sentences. Concrete and specific.
    context TEXT,          -- Domain, tool, or file name for filtering
    confidence TEXT DEFAULT 'certain'
        CHECK(confidence IN ('certain', 'probable', 'speculative')),
    created_at TEXT DEFAULT (datetime('now')),
    invalidated_at TEXT    -- If reversed later; never delete
);
```

### `notes_fts`

Full-text search over `body` and `context`. Auto-maintained by triggers included in the init script.

---

## Workflow

### Step 1: Ensure the notebook exists

Before any work in a project, check for `.wichy/notebook.db`. If it does not exist,
initialize it:

```bash
./scripts/init_notebook.py /path/to/project/.wichy
```

**This is mandatory on first encounter with a repo.** Every new agent instance should
initialize memory if it is absent. The user should never have to ask.

### Step 2: Search before acting

```python
import sqlite3
conn = sqlite3.connect('/path/to/project/.wichy/notebook.db')
db = conn.execute

rows = db("""
    SELECT kind, body, context, datetime(created_at)
    FROM notes WHERE notes_fts MATCH 'keyword AND domain'
    ORDER BY rank, created_at DESC LIMIT 10;
""").fetchall()
```

Search before:

- Opening or editing a file you touched before
- Interpreting ambiguous instructions in a known project
- Making a decision in a domain with history

### Step 3: Start a session

```python
session_id = db(
    "INSERT INTO sessions(summary, projects) VALUES (?, ?)",
    ("Research and report", '["analysis", "reporting"]')
).lastrowid
conn.commit()
```

### Step 4: Write notes during work

```python
db(
    "INSERT INTO notes(session_id, kind, body, context) VALUES (?, ?, ?, ?)",
    (session_id, 'insight',
     'User prefers bullet summaries over narrative paragraphs for status updates',
     'communication preferences')
)
conn.commit()
```

**Maximum 3-5 notes per session.**

### Step 5: End the session

```python
db(
    "UPDATE sessions SET ended_at = datetime('now'), summary = ? WHERE id = ?",
    ("Report drafted, pending review", session_id)
)
conn.commit()
conn.close()
```

### Step 6: Dump for git

```bash
./scripts/dump_notebook.py /path/to/project/.wichy/notebook.db
# Commit the resulting notebook.sql
```

---

## Kinds: When to Use Which

| Kind       | Use when                                                                   |
| ---------- | -------------------------------------------------------------------------- |
| `decision` | We chose X over Y. A choice was made, even a small one.                    |
| `mistake`  | Something went wrong and we understood why.                                |
| `insight`  | A surprising tactic or fact that proved useful.                            |
| `gotcha`   | A recurring trap, edge case, or behavior that looks wrong but is expected. |
| `pattern`  | A theme confirmed across 2+ distinct sessions.                             |
| `context`  | Architecture, key resources, or domain state worth remembering.            |

---

## What to Write vs. What to Skip

| Write                            | Skip                                       |
| -------------------------------- | ------------------------------------------ |
| Decisions made (even small ones) | Already-captured directives in static docs |
| Mistakes and their root causes   | Session summaries in `contexts/` files     |
| Gotchas and edge cases           | Research deliverables                      |
| Insights that feel non-obvious   | Trivial one-line actions                   |
| Patterns across 2+ sessions      | Todo list changes                          |
| Domain state worth remembering   |                                            |

---

## Anti-Patterns

1. **Do not use the notebook as a logbook.** Session details belong in ephemeral files.
2. **Do not dump raw errors without root cause.** A stack trace is not a `mistake` note.
3. **Do not invalidate without explanation.** If a decision is reversed, write a new one.
4. **Do not keep every note forever.** Review weekly. Delete notes that have proven wrong.

---

## Available Scripts

### init_notebook.py

**Description:** Creates the notebook database with full schema, FTS5 virtual table, sync triggers, and WAL mode.

**Usage:** `./scripts/init_notebook.py <.wichy-dir>`

**Arguments:**

- `.wichy-dir`: Path to the project's `.wichy/` directory

### dump_notebook.py

**Description:** Dumps the binary `.db` to a text `.sql` dump suitable for git versioning.

**Usage:** `./scripts/dump_notebook.py <notebook.db>`

**Arguments:**

- `notebook.db`: Path to the notebook database file. Outputs to `<dir>/notebook.sql`.

---

## Notes

> CRITICAL: The notebook is a per-repo tool, not a global one. Create it inside each project's `.wichy/` directory.

> CRITICAL: Never commit the `.db` file to git. Commit `notebook.sql` instead.

> CRITICAL: Always use `datetime('now')` for timestamps. The agent has no reliable internal clock. Re-check before writing times.

> CRITICAL: Query before acting in any known domain. Never assume memory from past sessions.

> CRITICAL: If `notebook.db` is missing, run `./scripts/init_notebook.py` before other work.

> CRITICAL: Use Python's `sqlite3` module or the SQLite CLI for all notebook operations.
> Do NOT use DuckDB tools (`duckdb_load`, `duckdb_query`, etc.) to read or write the notebook.
> The notebook uses SQLite-specific features (FTS5 virtual tables, triggers, WAL mode)
> that DuckDB does not maintain correctly. Writes via DuckDB will bypass triggers and
> corrupt the FTS5 index. Always use native SQLite access for the notebook.

The notebook is not an archive. It is a curated tool for faster, more reliable future work.
