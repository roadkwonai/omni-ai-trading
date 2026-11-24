"""AI search: the requests that can be sent again, and the talks and searches with their answers (docs/ai-search-history-plan.md)."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlite_ro import ro_connect
from src.config.paths import get_runtime_root
from src.tools.private_db import private_file

KST = timezone(timedelta(hours=9))
KEEP = 1000  # rows of the request list; the screen shows the last 8 distinct requests
TITLE = 120
# AUTOINCREMENT: a screen that still shows a deleted talk must not add its answer to another one
SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('검색', '질문')), query TEXT NOT NULL, time TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS threads (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL CHECK (kind IN ('검색', '대화')), title TEXT NOT NULL,
    subject TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE, question TEXT NOT NULL,
    answer TEXT NOT NULL, sources TEXT, guides TEXT, local INTEGER NOT NULL, local_query TEXT, time TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS turns_thread ON turns (thread_id);
CREATE INDEX IF NOT EXISTS threads_updated ON threads (updated);
"""


def path() -> Path:
    return get_runtime_root() / 'nhplug' / 'ai-search-history.sqlite3'


def _now() -> str:
    return datetime.now(KST).isoformat(timespec='microseconds')  # the list is ordered by it


def _connect():
    db_path = path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(private_file(db_path), timeout=5)
    try:
        # a list being read does not stop an answer from being kept; the switch ignores the busy timeout, so while another
        # connection makes the file it can report 'locked' at once: that connection switches it, and this write waits
        with suppress(sqlite3.OperationalError):
            db.execute('PRAGMA journal_mode = WAL')
        db.execute('PRAGMA foreign_keys = ON')
        db.executescript(SCHEMA)
    except sqlite3.Error:
        db.close()
        raise
    return closing(db)


def record(kind: str, query: str) -> None:
    with _connect() as db, db:
        db.execute('INSERT INTO history (kind, query, time) VALUES (?, ?, ?)', (kind, query, _now()))
        db.execute('DELETE FROM history WHERE id <= (SELECT MAX(id) FROM history) - ?', (KEEP,))


def _has_talks(db) -> bool:
    """False for a file written before the talks were kept."""
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name = 'threads'").fetchone())


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


def save(kind: str, title: str, turn: dict, *, thread_id: int | None = None, subject: dict | None = None) -> int:
    """Keep one answered request and return its talk: `thread_id` when that talk still exists, else a new one."""
    now = _now()
    packed = [json.dumps(turn[key], ensure_ascii=False) if turn.get(key) else None for key in ('sources', 'guides', 'local_query')]
    with _connect() as db, db:
        known = thread_id is not None and db.execute('UPDATE threads SET updated = ? WHERE id = ?', (now, thread_id)).rowcount
        if not known:
            thread_id = db.execute('INSERT INTO threads (kind, title, subject, created, updated) VALUES (?, ?, ?, ?, ?)',
                                   (kind, ' '.join(title.split())[:TITLE], json.dumps(subject, ensure_ascii=False) if subject else None,
                                    now, now)).lastrowid
        db.execute('INSERT INTO turns (thread_id, question, answer, sources, guides, local, local_query, time) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   (thread_id, turn['question'], turn['answer'], packed[0], packed[1], int(bool(turn.get('local'))), packed[2], now))
    return thread_id


def threads(find: str = '', limit: int = 20, archived: bool = False) -> list[dict]:
    """Talks and searches, the last used first; the archived ones only when asked for. `find` looks in titles, questions and answers."""
    db_path = path()
    if not db_path.exists():
        return []
    where, values = ['archived = ?'], [int(archived)]
    if find := find.strip():
        # shortcut: reads every answer (0.1 s at 1,000 talks, 1.4 s at 10,000), an FTS5 trigram table when that is too slow
        like = '%' + find.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where.append("(title LIKE ? ESCAPE '\\' OR id IN (SELECT thread_id FROM turns WHERE question LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\'))")
        values += [like] * 3
    with ro_connect(db_path, timeout=2) as db:
        if not _has_talks(db):
            return []
        rows = db.execute('SELECT id, kind, title, updated, (SELECT COUNT(*) FROM turns WHERE thread_id = threads.id) FROM threads WHERE '
                          + ' AND '.join(where) + ' ORDER BY updated DESC, id DESC LIMIT ?', (*values, limit)).fetchall()
    return [dict(zip(('id', 'kind', 'title', 'updated', 'turns'), row)) for row in rows]


def _unpack(text: str | None, kind: type):
    try:
        value = json.loads(text) if text else None
    except ValueError:
        return None
    return value if isinstance(value, kind) else None


def load(thread_id: int) -> dict | None:
    db_path = path()
    if not db_path.exists():
        return None
    with ro_connect(db_path, timeout=2) as db:
        head = db.execute('SELECT id, kind, title, subject, archived, updated FROM threads WHERE id = ?',
                          (thread_id,)).fetchone() if _has_talks(db) else None
        if not head:
            return None
        rows = db.execute('SELECT question, answer, sources, guides, local, local_query, time FROM turns WHERE thread_id = ? ORDER BY id',
                          (thread_id,)).fetchall()
    return {'id': head[0], 'kind': head[1], 'title': head[2], 'subject': _unpack(head[3], dict), 'archived': bool(head[4]),
            'updated': head[5],
            'turns': [{'question': q, 'answer': a, 'sources': _unpack(s, list) or [], 'guides': _unpack(g, list) or [],
                       'local': bool(local), 'local_query': _unpack(lq, dict), 'time': stamp} for q, a, s, g, local, lq, stamp in rows]}


def delete(thread_id: int) -> None:
    with _connect() as db, db:
        db.execute('DELETE FROM threads WHERE id = ?', (thread_id,))


def archive(thread_id: int, archived: bool) -> None:
    with _connect() as db, db:
        db.execute('UPDATE threads SET archived = ? WHERE id = ?', (int(archived), thread_id))


def clear() -> None:
    """Talks, searches and the request list; what was archived stays."""
    with _connect() as db, db:
        db.execute('DELETE FROM threads WHERE archived = 0')
        db.execute('DELETE FROM history')
    try:
        with _connect() as db:
            db.execute('VACUUM')  # the deleted answers leave the file too
    except sqlite3.OperationalError:
        pass  # in use; the next clear gives the space back
