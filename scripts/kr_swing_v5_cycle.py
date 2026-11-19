"""Run one guarded v5 paper cycle against one validated daily snapshot."""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from src.config.paths import get_runtime_root

ROOT = Path(__file__).resolve().parents[1]
BASELINE = date(2026, 11, 5)
TIMEOUT = 120


def session_days(runtime: Path, registered: date, end: date) -> list[str]:
    """Read the common calendar published by the validated-data pipeline."""
    path = runtime / 'data/current/005930.KS.csv'
    with path.open(newline='') as stream:
        rows = [date.fromisoformat(row['date']) for row in csv.DictReader(stream)]
    if rows != sorted(set(rows)) or not rows or rows[-1] != end:
        raise ValueError('validated snapshot calendar is incomplete')
    return [day.isoformat() for day in rows if registered < day <= end]


def _step(label: str, args: list[str]) -> dict:
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts' / args[0]), *args[1:]],
                                cwd=ROOT, shell=False, capture_output=True, text=True,
                                timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'{label} stage unavailable') from exc
    if result.returncode:
        # Provider messages can contain credentials or account identifiers.
        raise RuntimeError(f'{label} stage failed; later stages were not run')
    try:
        output = result.stdout.strip()
        try:
            value = json.loads(output)
        except json.JSONDecodeError:
            value = json.loads(output.splitlines()[-1])
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, IndexError, AttributeError, TypeError) as exc:
        raise RuntimeError(f'{label} response invalid') from exc


def run_cycle(as_of: date, today: date, runtime: Path, *, execute: bool = False) -> str:
    """Publish closes and compare before considering one guarded paper order."""
    if as_of >= today:
        raise ValueError('as-of must be a completed prior date')
    if as_of < BASELINE:
        raise ValueError('as-of precedes baseline')
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = os.open(runtime / 'cycle.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        benchmark = json.loads((runtime / 'benchmark.json').read_text())
        registered = date.fromisoformat(benchmark['registered_date_kst'])
        if date.fromisoformat(benchmark['last_session']) > as_of:
            raise ValueError('benchmark is ahead of requested date')
        data = _step('data', ['kr_swing_v5_daily_data.py', '--as-of', as_of.isoformat()])
        if data.get('status') != 'published' or data.get('last_session') != as_of.isoformat():
            raise RuntimeError('data response invalid')
        snapshot = (runtime / 'data/current').resolve(strict=True)
        if snapshot.parent != (runtime / 'data/versions').resolve():
            raise ValueError('invalid data snapshot pointer')
        sessions = session_days(runtime, registered, as_of)
        # Re-verification is idempotent; no result is published without each close.
        for day in sessions:
            valuation = _step('valuation', ['kr_swing_v5_valuation.py', '--date', day, '--snapshot', str(snapshot)])
            if valuation.get('status') != 'verified':
                raise RuntimeError('valuation response invalid')
        if as_of >= registered:
            _step('benchmark', ['kr_swing_v5_benchmark.py', '--state', str(runtime / 'benchmark.json'),
                                '--data', str(snapshot), '--as-of', as_of.isoformat()])
        fd, name = tempfile.mkstemp(prefix='.sessions-', suffix='.json', dir=runtime)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(sessions, stream)
            comparison = _step('comparison', ['kr_swing_v5_compare.py', '--sessions', name])
        finally:
            os.unlink(name)
        if comparison.get('status') not in {'awaiting_future_sessions', 'observing'}:
            raise RuntimeError('comparison response invalid')
        # A read-only pass verifies the exact same closed-bar date before writes.
        paper = _step('paper read-only', ['kr_swing_v5_forward.py', '--data', str(snapshot)])
        if paper.get('day') != as_of.isoformat() or paper.get('status') not in {'wait', 'ready'}:
            raise RuntimeError('paper signal is unavailable or stale')
        if execute and paper['status'] == 'ready':
            result = _step('paper order', ['kr_swing_v5_forward.py', '--data', str(snapshot), '--execute'])
            if result.get('status') not in {'terminal', 'wait'}:
                raise RuntimeError('paper order result requires reconciliation')
            return str(result['status'])
        return str(paper['status'])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    root = get_runtime_root() / 'nhplug/strategies/kr_swing_v5'
    status = run_cycle(args.as_of, datetime.now(ZoneInfo('Asia/Seoul')).date(), root, execute=args.execute)
    print(json.dumps({'status': status}, ensure_ascii=False))


if __name__ == '__main__':
    main()
