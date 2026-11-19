"""Daily copy of the trading records (launchd job com.vibetrading.trading-backup, weekdays after the close).

SQLite files are copied with the online backup API (consistent even while a service has them open); event logs and
small settings files are copied as they are. Kept under ~/.vibe-trading/backups/YYYY-MM-DD for KEEP_DAYS days.
Only the backup folder is ever pruned; the originals are never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
NHPLUG = Path.home() / '.vibe-trading/nhplug'
TARGET = Path.home() / '.vibe-trading/backups'
KEEP_DAYS = 14
FILES = ('events.jsonl', 'trading-day-start.jsonl', 'accounts.json', 'strategy-settings.json', 'exit-plans.json',
         'watchlist.json', 'watchlist-manual.json', 'market-context.json', 'ai-modes.json')


def backup(source=NHPLUG, target=TARGET, today=None):
    today = today or datetime.now(KST).date()
    folder = Path(target) / today.isoformat()
    copied = []
    for path in sorted(Path(source).rglob('*')):
        if not path.is_file() or path.is_symlink() or 'backup' in path.parts[-2] or path.suffix in ('.tmp', '.lock'):  # a link's target is copied as itself
            continue
        out = folder / path.relative_to(source)
        if path.suffix == '.sqlite3':
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as src, sqlite3.connect(out) as dst:
                src.backup(dst)
        elif path.name in FILES or path.name.endswith('.jsonl'):
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(path, out)
        else:
            continue
        os.chmod(out, 0o600)
        copied.append(str(out.relative_to(folder)))
    cutoff = today - timedelta(days=KEEP_DAYS)
    removed = []
    for old in Path(target).iterdir() if Path(target).exists() else []:
        try:
            if old.is_dir() and datetime.fromisoformat(old.name).date() < cutoff:
                shutil.rmtree(old)
                removed.append(old.name)
        except ValueError:
            continue  # not a dated backup folder
    return {'folder': str(folder), 'files': len(copied), 'removed': removed}


if __name__ == '__main__':
    print(json.dumps(backup(), ensure_ascii=False))
