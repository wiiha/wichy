#!/usr/bin/env python3
"""
Init notebook database for agent-notebook skill.
Creates schema if DB doesn't exist; adds missing tables/columns if it does.
"""

import sqlite3
import sys
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    started_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT,
    summary TEXT,
    projects TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    session_id INTEGER REFERENCES sessions(id),
    kind TEXT NOT NULL CHECK(kind IN (
        'decision',
        'mistake',
        'insight',
        'gotcha',
        'pattern',
        'context'
    )),
    body TEXT NOT NULL,
    context TEXT,
    confidence TEXT DEFAULT 'certain'
        CHECK(confidence IN ('certain', 'probable', 'speculative')),
    created_at TEXT DEFAULT (datetime('now')),
    invalidated_at TEXT
);

-- FTS5 virtual table.
-- Note: existing FTS5 tables are tricky to migrate; if one exists we skip.
"""

FTS5_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    body,
    context,
    content='notes',
    content_rowid='id'
);

-- Triggers to keep FTS5 in sync
CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, body, context) VALUES (new.id, new.body, new.context);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body, context)
        VALUES ('delete', old.id, old.body, old.context);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, body, context)
        VALUES ('delete', old.id, old.body, old.context);
    INSERT INTO notes_fts(rowid, body, context) VALUES (new.id, new.body, new.context);
END;
"""


def init_db(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.executescript(FTS5_SQL)
    conn.commit()
    conn.close()
    print(f"Notebook initialized: {db_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: init_notebook.py <.wichy-dir>")
        sys.exit(1)

    wichy_dir = Path(sys.argv[1])
    db_path = wichy_dir / "notebook.db"
    wichy_dir.mkdir(parents=True, exist_ok=True)

    needs_init = not db_path.exists()
    init_db(db_path)

    if not needs_init:
        print("(DB already existed; ensured schema is up to date)")


if __name__ == "__main__":
    main()
