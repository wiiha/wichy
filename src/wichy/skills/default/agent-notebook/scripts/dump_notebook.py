#!/usr/bin/env python3
"""
Dump notebook SQLite DB to a text .sql file for git versioning.
Outputs to <notebook-dir>/notebook.sql
"""

import sqlite3
import sys
from pathlib import Path


def dump_db(db_path: Path) -> Path:
    conn = sqlite3.connect(str(db_path))
    sql_path = db_path.with_suffix(".sql")
    with open(sql_path, "w") as f:
        for line in conn.iterdump():
            f.write(line + "\n")
    conn.close()
    print(f"Dumped {db_path} -> {sql_path}")
    return sql_path


def main():
    if len(sys.argv) < 2:
        print("Usage: dump_notebook.py <notebook.db>")
        sys.exit(1)

    db_path = Path(sys.argv[1])
    if not db_path.exists():
        print(f"Error: {db_path} not found", file=sys.stderr)
        sys.exit(1)

    dump_db(db_path)


if __name__ == "__main__":
    main()
