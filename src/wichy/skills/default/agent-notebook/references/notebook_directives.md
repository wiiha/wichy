# Agent Notebook: Directives Template

**Purpose:** Copy this into your project's `.wichy/notebook_directives.md` and adapt it per project. It defines what to write, when to search, and how to maintain the notebook.

---

## What Goes into the Notebook

Write to the notebook **proactively** during every session. Do not wait for user request. Default to writing rather than skipping.

| Write this | Skip this |
|---|---|
| Decisions made (even small ones) | Already-captured directives in MEMORY.md |
| Mistakes and their root causes | Session summaries already in `contexts/` files |
| Surprising gotchas and edge cases | Research deliverables |
| Insights that feel non-obvious | Trivial one-line shell commands |
| Patterns detected across 2+ sessions | Todo list changes |
| Project state worth remembering at next boot | |

**Maximum 3-5 notes per session.** More is noise.

## When to Write

1. At session start -- open a session record.
2. When something notable happens -- a mistake, a decision, a gotcha.
3. At session end -- close the session, write a summary.

## When to Search (Mandatory)

**Before any non-trivial action in a known project, search the notebook first.**

Search when:
- About to refactor a file you touched before
- About to debug an error that feels familiar
- About to make a decision in a project with history
- About to interpret ambiguous user instruction in a known project

## Invalidation

If a decision or insight is later reversed, do not delete. Mark it invalid:

```sql
UPDATE notes SET invalidated_at = datetime('now') WHERE id = <note_id>;
```

## Anti-Patterns

1. Do not use the notebook as a logbook. Session-level events live in `contexts/`.
2. Do not dump raw errors without root cause. A stack trace is not a `mistake` note.
3. Do not invalidate without explanation. Insert a new `decision` note explaining the reversal.
4. Do not keep every note forever. Review weekly. Delete notes that have proven wrong.
## Access Rules

Use Python's `sqlite3` module or the SQLite CLI for all notebook operations.

Do NOT use DuckDB tools (`duckdb_load`, `duckdb_query`, etc.) to read or write the notebook.
The notebook uses SQLite-specific features (FTS5 virtual tables, triggers, WAL mode)
that DuckDB does not maintain correctly. Writes via DuckDB will bypass triggers and
corrupt the FTS5 index. Always use native SQLite access for the notebook.
