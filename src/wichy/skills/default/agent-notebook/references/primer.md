# Primer: Personal Knowledge Stores for LLM Agents

**Purpose:** Why agents need durable memory, how to build it, and how to use it.
Not about data pipelines. About thinking.

---

## The Problem: Stateless Agents Have Goldfish Memory

By default, agent instances start from zero every session. Past decisions, user
preferences, project context, mistakes made and fixes found -- all evaporate.
This produces repeated effort, repeated mistakes, and no compounding expertise.

A personal knowledge store is durable memory you own. Queryable. Portable.
Independent of any single model provider. It turns an agent from a stateless tool
into a cumulative collaborator.

---

## Three Patterns for Agent Memory

### 1. Notebook + Reference Documents Hybrid (Recommended)

Short-form observations live in SQLite (decisions, gotchas, patterns).
Long-form reasoning lives in Markdown files (plans, architectures, project overviews).
The notebook indexes into the documents. Query fast in SQLite; follow references
into documents for depth.

This is the sweet spot for most solo agents.

### 2. Bi-Directional Linking (Advanced)

Notes link to other notes. Writing about a decision? Link to the related gotcha.
Querying a pattern? Follow links to see what else it touched. This is what makes
memory a thinking tool rather than a filing cabinet. Obsidian and Logseq do this
for humans; agents can embed links in `context` or `meta` fields.

### 3. Single Flat Notebook (Simplest)

One table. Searchable. No schema complexity. Good enough for most solo agents.
Upgrade when cross-referencing becomes painful.

---

## Key Tools

| Tool | Purpose |
|---|---|
| sqlite-utils | Create tables, batch inserts, WAL mode |
| Datasette | Web UI for exploring and searching your notebook |
| sqlite-vec | Vector search for semantic similarity |
| FTS5 | Built-in full-text search in SQLite |

---

## Habits That Make Memory Work

### Query Before Acting

Before making decisions in a project with history, search the notebook.
"Have I seen this before?" "What did the user prefer?"
A store you never query is hoarding, not thinking.

### Decision Tracking

Log not just what you chose, but why. The rejected alternatives matter.
Tag each decision with its domain or project.

### Apply PARA to Memory

- **Projects:** Active work with a goal and deadline.
- **Areas:** Ongoing responsibilities (user preferences, recurring tasks).
- **Resources:** Reusable knowledge (commands, patterns, examples).
- **Archives:** Finished work. Review monthly. Much of it becomes Resources.

### Connection Over Collection

50 isolated notes < 10 linked notes. When you write about a gotcha, link it to
the decision it affected and the context that produced it. Bi-directional linking
is the single most important differentiator of a thinking tool versus a filing
cabinet.

---

## Pitfalls

1. **Collecting without querying.** If you log but never search, it is dead weight.
2. **Letting memory decay.** Old context misleads. Archive aggressively and
   invalidate decisions that no longer apply.
3. **Over-structuring too early.** Start simple. Add complexity only when
   querying becomes painful.
4. **Treating the notebook as a log.** Session transcripts belong elsewhere.
   The notebook is for insights, decisions, and patterns -- not events.

---

## Bottom Line

A personal knowledge store is not a research topic. It is a practical upgrade
to agent reliability. Start with one SQLite file per project, write 3-5 notes per
session, query before acting, and review monthly. The goal is connection:
linking today's action to yesterday's lesson so the agent stops starting from zero.
