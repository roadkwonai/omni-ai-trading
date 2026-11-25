"""Paper ETF model settings applied only at an order-free cycle boundary."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src import file_lock

DEFAULT = {'version': 1, 'jev': True, 'decision': True}
JEV_MODEL = 'jev-1.14.0'
DECISIONS_MODEL = 'gpt-6-luna'


def _validate(data):
    if (not isinstance(data, dict) or set(data) != set(DEFAULT)
            or type(data['version']) is not int or data['version'] != 1
            or type(data['jev']) is not bool or type(data['decision']) is not bool):
        raise ValueError('paper AI settings invalid')
    return data


def _read(path, missing):
    try:
        return _validate(json.loads(path.read_text(encoding='utf-8')))
    except FileNotFoundError:
        return missing


def read_modes(root):
    try:
        return _validate(json.loads((Path(root) / 'ai-modes.json').read_text(encoding='utf-8')))
    except FileNotFoundError as exc:
        raise ValueError('paper AI settings unavailable') from exc


def read_pending_modes(root):
    return _read(Path(root) / 'ai-modes.pending.json', None)


def _write(path, data):
    import config_store
    # kept separate from config_store.write_json: apply_pending_modes' crash recovery depends on this exact order
    fd, name = tempfile.mkstemp(prefix='.ai-modes-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        config_store.replace(name, path)
        config_store.sync_folder(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_modes(root, *, jev, decision, who='dashboard'):
    import config_store
    data = _validate({'version': 1, 'jev': jev, 'decision': decision})
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'ai-modes.lock').open('a', encoding='utf-8') as lock:
        file_lock.lock(lock)
        try:
            before = _read(root / 'ai-modes.pending.json', None) or _read(root / 'ai-modes.json', None)
        except ValueError:  # an unreadable earlier file: the history shows the new value only
            before = None
        _write(root / 'ai-modes.pending.json', data)
    config_store.record('ai_modes', before, data, who, saved=root / 'ai-modes.json')  # requested; the runner applies it at a safe cycle
    return data


def apply_pending_modes(root):
    """Runner-only: call with runner.lock held and no unresolved order."""
    import config_store
    root = Path(root)
    with (root / 'ai-modes.lock').open('a', encoding='utf-8') as lock:
        file_lock.lock(lock)
        pending = read_pending_modes(root)
        if pending is not None:
            _write(root / 'ai-modes.json', pending)
            (root / 'ai-modes.pending.json').unlink()
            config_store.sync_folder(root)
        return read_modes(root)
