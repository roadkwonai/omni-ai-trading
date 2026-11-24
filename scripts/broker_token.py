"""One shared broker token file per API (kiwoom_data, toss_data), safe across processes.

Toss keeps one valid token per client and a new issue revokes the previous one, and every scheduled job shares the
token, so issuing happens under an exclusive file lock after re-reading the file. A token the server just rejected is
passed as `stale`: if another process already replaced it, that one is used; otherwise exactly one new token is issued.
Expiry is judged on the real clock (a replay's `now` must never mark a live token valid or write a past expiry).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from config_store import write_json

from src import file_lock

KST = ZoneInfo('Asia/Seoul')
REFRESH = timedelta(minutes=10)


def _valid(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data['token'] if datetime.fromisoformat(data['expires_at']) - datetime.now(KST) > REFRESH else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def token(path, issue, stale=None):
    """A valid shared token other than `stale`; else `issue()` -> (token, expires_at) under the lock, stored 0600."""
    found = _valid(path)
    if found and found != stale:
        return found
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path.with_suffix('.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        file_lock.lock(fd)
        found = _valid(path)
        if found and found != stale:
            return found
        value, expires = issue()
        write_json(path, {'token': value, 'expires_at': expires.isoformat()})
        return value
    finally:
        os.close(fd)  # closing releases the lock
