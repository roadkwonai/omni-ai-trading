"""Shared plumbing for the dashboard-managed settings files (user 2026-11-13).

Values stay in small JSON files that every runner reads each cycle (accounts.json, strategy-settings.json,
tunables.json, dashboard-prefs.json, ai-modes.json, watchlist-manual.json). What SQLite does better is the history:
every save records what changed (old -> new, when, from where) in one table, settings-history.sqlite3, so the
dashboard can show one timeline instead of a per-file log. A history failure never blocks a save, except for the
live-order switch, whose audit is required.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def write_json(path, data, indent=1):
    """Every dashboard-managed settings file is written this way (write_text)."""
    write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def write_text(path, text):
    """Atomic, durable write (temp file in the same folder, fsync, rename, folder fsync), private to the user (0600,
    folder 0700): a reader in another process sees the old file or the new one, never a half-written one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())  # a crash right after must not leave an empty settings file
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def history_path(saved=None):
    """The history file; with `saved` (the settings file just written) the one next to its nhplug root, or its folder,
    so a rehearsal on a copy never writes the runtime history (11-18 16:01)."""
    override = os.environ.get('VIBE_TRADING_SETTINGS_HISTORY', '').strip()
    if override:
        return Path(override)
    if saved is not None:
        saved = Path(os.path.abspath(saved))  # not resolve(): a symlinked runtime folder keeps its nhplug name
        return next((p for p in saved.parents if p.name == 'nhplug'), saved.parent) / 'settings-history.sqlite3'
    home = Path(os.environ.get('VIBE_TRADING_HOME') or Path.home() / '.vibe-trading').expanduser()
    return home / 'nhplug' / 'settings-history.sqlite3'


def _flat(value, prefix=''):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(_flat(item, f'{prefix}.{key}' if prefix else str(key)))
        return out
    return {prefix: value}


def changes(old, new):
    """[(key, old, new)] between two settings dicts, nested keys joined with dots; unchanged keys left out."""
    a, b = _flat(old or {}), _flat(new or {})
    return [(k, a.get(k), b.get(k)) for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]


def record(kind, old, new, who='dashboard', required=False, now=None, saved=None):
    """Append what changed between `old` and `new` to the history; returns the number of rows written.
    `required`: raise if the history cannot be written (the live-order switch audit); `saved`: see history_path."""
    rows = changes(old, new)
    if not rows:
        return 0
    stamp = (now or datetime.now(KST)).isoformat(timespec='seconds')
    dump = lambda v: None if v is None else json.dumps(v, ensure_ascii=False)
    try:
        path = history_path(saved)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(path, timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS changes (time TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, '
                       'old TEXT, new TEXT, who TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS changes_time ON changes(time)')
            db.executemany('INSERT INTO changes VALUES (?,?,?,?,?,?)',
                           [(stamp, kind, key, dump(a), dump(b), who) for key, a, b in rows])
        os.chmod(path, 0o600)
        return len(rows)
    except (OSError, sqlite3.Error):
        if required:
            raise
        return 0


def history(limit=200, kind=None):
    """Newest changes first: [(time, kind, key, old, new, who)] (values as JSON text); [] when there is none."""
    try:
        from sqlite_ro import ro_connect
        with ro_connect(history_path()) as db:
            sql, args = 'SELECT time, kind, key, old, new, who FROM changes', ()
            if kind:
                sql, args = sql + ' WHERE kind=?', (kind,)
            return db.execute(sql + ' ORDER BY rowid DESC LIMIT ?', (*args, limit)).fetchall()
    except (OSError, sqlite3.Error):
        return []


def import_jsonl(path, kind, to_change, saved=None):
    """One-time move of an old JSON-lines audit file into the history (then renamed *.imported); `to_change(line)`
    gives (time, key, old, new) or None."""
    path = Path(path)
    if not path.is_file():
        return 0
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            item = to_change(json.loads(line))
        except (ValueError, TypeError, KeyError):
            item = None
        if item:
            rows.append(item)
    target = history_path(saved)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(target, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS changes (time TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, '
                   'old TEXT, new TEXT, who TEXT NOT NULL)')
        db.executemany('INSERT INTO changes VALUES (?,?,?,?,?,?)',
                       [(t, kind, k, json.dumps(o), json.dumps(n), 'imported') for t, k, o, n in rows])
    path.rename(path.with_suffix(path.suffix + '.imported'))
    return len(rows)
