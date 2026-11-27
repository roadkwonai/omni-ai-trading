"""Append-only SQLite history of scanner rankings and watchlist selections (market-research.sqlite3).

movers.json and watchlist.json are overwritten every scan/selection; this keeps every version for later
learning and review. A failed write never blocks the scan or selection.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlite_ro import ro_connect

ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
SNAPSHOT_HISTORY_INDEX = 'CREATE INDEX IF NOT EXISTS snapshots_kind_id ON snapshots(kind, id)'


def _connect(path):
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA journal_mode=WAL')  # the runner, the learning watch and daily jobs write while reports read
    return db


def record(kind, payload, path=None):
    path = Path(path or ROOT / 'market-research.sqlite3')
    try:
        with _connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, time TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS snapshots_kind_time ON snapshots(kind, time)')
            db.execute(SNAPSHOT_HISTORY_INDEX)
            db.execute('INSERT INTO snapshots(kind, time, payload) VALUES (?,?,?)',
                       (kind, str(payload.get('time', '')), json.dumps(payload, ensure_ascii=False)))
        path.chmod(0o600)
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def record_input(engine, question, inputs, response, path=None):
    """What a model was actually given (no images) and what it answered, keyed by a digest of the inputs.

    Returns the digest, or None when the write failed; a failure never blocks trading.
    """
    path = Path(path or ROOT / 'market-research.sqlite3')
    try:
        text = json.dumps(inputs, ensure_ascii=False, sort_keys=True, default=str)
        answer = json.dumps(response, ensure_ascii=False, sort_keys=True, default=str)
        digest = hashlib.sha256(f'{engine}:{question}:{text}:{answer}'.encode()).hexdigest()
        with _connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_inputs (digest TEXT PRIMARY KEY, time TEXT NOT NULL, engine TEXT NOT NULL, '
                       'question TEXT NOT NULL, inputs TEXT NOT NULL, response TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS model_inputs_time ON model_inputs(time)')
            db.execute('INSERT OR IGNORE INTO model_inputs VALUES (?,?,?,?,?,?)',
                       (digest, datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), engine, question, text, answer))
        path.chmod(0o600)
        return digest
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def record_evidence(digest, engine, question, evidence, path=None):
    """Why a model answered as it did (jev_context.EVIDENCE answers), keyed by the model input's digest. Never raises."""
    path = Path(path or ROOT / 'market-research.sqlite3')
    try:
        with _connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_evidence (digest TEXT PRIMARY KEY, time TEXT NOT NULL, engine TEXT NOT NULL, '
                       'question TEXT NOT NULL, evidence TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS model_evidence_time ON model_evidence(time)')
            db.execute('INSERT OR REPLACE INTO model_evidence VALUES (?,?,?,?,?)',
                       (digest, datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), engine, question,
                        json.dumps(evidence, ensure_ascii=False, sort_keys=True)))
        path.chmod(0o600)
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def history(kind, limit=50, path=None):
    """Newest `limit` snapshots of `kind`, re-read only when the database (or its WAL) changed: a dashboard rerun asks
    for the same vibe advice several times. The dicts are shared between callers: read them, do not change them."""
    path = Path(path or ROOT / 'market-research.sqlite3')
    try:
        stamp = tuple((s.st_mtime_ns, s.st_size) for s in (path.stat(), *(w.stat() for w in [Path(f'{path}-wal')] if w.exists())))
    except OSError:
        return []
    return list(_history(str(path), kind, limit, stamp))


@lru_cache(maxsize=32)
def _history(path, kind, limit, stamp):
    try:
        with ro_connect(path) as db:
            return tuple(json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM snapshots WHERE kind=? ORDER BY id DESC LIMIT ?', (kind, limit)))
    except (OSError, sqlite3.Error):
        return ()
