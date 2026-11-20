"""Local search and question history for the AI search dashboard."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlite_ro import ro_connect
from src.config.paths import get_runtime_root

KST = timezone(timedelta(hours=9))


def path() -> Path:
    return get_runtime_root() / 'nhplug' / 'ai-search-history.sqlite3'


def record(kind: str, query: str) -> None:
    db_path = path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.touch(mode=0o600, exist_ok=True)
    with closing(sqlite3.connect(db_path, timeout=2)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('검색', '질문')),
                query TEXT NOT NULL,
                time TEXT NOT NULL
            )
        """)
        db.execute('INSERT INTO history (kind, query, time) VALUES (?, ?, ?)',
                   (kind, query, datetime.now(KST).isoformat(timespec='seconds')))


def recent() -> list[tuple[str, str]]:
    db_path = path()
    if not db_path.exists():
        return []
    with ro_connect(db_path, timeout=2) as db:
        return db.execute("""
            SELECT kind, query FROM history
            GROUP BY kind, query
            ORDER BY MAX(id) DESC
            LIMIT 8
        """).fetchall()
