"""Write an isolated daily event-AI replay; never changes engine or broker data."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config_store import write_json
from accounts import load as load_accounts
from engine_roles import ledger_root
from vibe_fast_exit_sim import _position_at, _time, replay

KST = ZoneInfo('Asia/Seoul')
LIMITATIONS = ('관측 호가와 표시 잔량을 이용한 독립 가상 체결 가정; 실제 체결·대기열·'
               '수수료·세금·시장충격은 검증되지 않았습니다.')


def build(nhplug, day):
    """Return explicit unavailable when inputs or the recorded engine ledger cannot be proven."""
    root = Path(nhplug)
    report = {'day': day, 'status': 'unavailable', 'limitations': LIMITATIONS,
              'worker_coverage': '판단기 전체 가동 시간은 기록으로 확인되지 않았습니다.'}
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError('invalid_day')
        stored = json.loads((root / 'accounts.json').read_text(encoding='utf-8'))
        role = stored['accounts']['paper-shared']['engine']
        checked = load_accounts(root / 'accounts.json')['paper-shared']
        if role not in ('jev', 'vibe') or checked.get('invalid') or checked.get('engine') != role:
            raise ValueError('engine_role_unavailable')
        engine = ledger_root(root, 'vibe')
        if engine is None or engine.name != ('shared-etf-paper' if role == 'vibe' else 'vibe-paper'):
            raise ValueError('engine_role_unavailable')
        report['ledger'] = engine.name
        observations = root / 'vibe-fast-exit-shadow' / f'observations-{day}.jsonl'
        events = root / 'vibe-event-analysis' / 'events.sqlite3'
        ledger = engine / 'orders.sqlite3'
        for path, label in ((observations, 'observations_unavailable'), (events, 'event_database_unavailable'),
                            (ledger, 'engine_ledger_unavailable')):
            if not path.is_file():
                raise ValueError(label)
        with observations.open(encoding='utf-8') as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
        if not rows:
            raise ValueError('observations_empty')
        if any(not isinstance(row, dict) or _time(row.get('observed_at')) is None
               or _time(row['observed_at']).astimezone(KST).date().isoformat() != day for row in rows):
            raise ValueError('observation_day_invalid')
        with sqlite3.connect(f'file:{ledger}?mode=ro', uri=True, timeout=2) as db:
            raw_fills = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
        fills = [(oid, symbol, side, qty, price, _time(stamp)) for oid, symbol, side, qty, price, stamp in raw_fills]
        if any(stamp is None for *_, stamp in fills):
            raise ValueError('ledger_fill_time_invalid')
        proven = 0
        for row in rows:
            if not row.get('symbol'):
                continue
            at = _time(row['observed_at'])
            position = _position_at(fills, row['symbol'], at)
            if (position is None or position['qty'] != row.get('qty')
                    or position['position_id'] != row.get('position_id')
                    or position['latest_fill_id'] != row.get('latest_fill_id')):
                raise ValueError('engine_ledger_identity_unverified')
            proven += 1
        if not proven:
            raise ValueError('engine_ledger_identity_unverified')
        with sqlite3.connect(f'file:{events}?mode=ro', uri=True, timeout=2) as db:
            attempts = db.execute('SELECT id,day,symbol,time,status,result FROM attempts WHERE day=? ORDER BY rowid', (day,)).fetchall()
        if any(_time(item[3]) is None or _time(item[3]).astimezone(KST).date().isoformat() != day for item in attempts):
            raise ValueError('event_day_invalid')
        # A temporary, isolated subset prevents other trading days from entering this replay.
        with tempfile.TemporaryDirectory(prefix='vibe-event-daily-') as folder:
            subset = Path(folder) / 'events.sqlite3'
            with sqlite3.connect(subset) as db:
                db.execute('CREATE TABLE attempts(id TEXT,day TEXT,symbol TEXT,time TEXT,status TEXT,result TEXT)')
                db.executemany('INSERT INTO attempts VALUES (?,?,?,?,?,?)', attempts)
            result = replay(observations, ledger, subset)
        if result.get('mode') != 'offline_simulation_not_broker_fill' or result.get('event_results_status') != 'ok':
            raise ValueError('replay_unavailable')
        triggers = sum(isinstance(row.get('event_trigger'), dict) and row['event_trigger'].get('triggered') is True for row in rows)
        result.update(report)
        result.update(status='ok', reason=None, observation_count=len(rows), position_observations=proven,
                      recorded_triggers=triggers, recorded_attempts=len(attempts),
                      recorded_completed=sum(row[4] == 'completed' for row in attempts))
        if triggers and not attempts:
            result.update(status='partial', reason='trigger_without_attempt_worker_status_unverified')
        return result
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, json.JSONDecodeError) as exc:
        report['reason'] = str(exc) if isinstance(exc, ValueError) and str(exc).replace('_', '').isalnum() else 'input_or_replay_unavailable'
        return report


def run(nhplug, day):
    root = Path(nhplug)
    report = build(root, day)
    try:
        if date.fromisoformat(day).isoformat() != day:
            return report
    except (ValueError, TypeError):
        return report
    # Never replace a previously produced report with an error from a later run.
    path = root / 'vibe-fast-exit-shadow' / 'reviews' / f'event-replay-{day}.json'
    if report['status'] == 'ok' or not path.exists():
        write_json(path, report)
    else:
        report['previous_report_preserved'] = True
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Run the daily replay once')
    parser.add_argument('--day', default=datetime.now(KST).date().isoformat(), help='trading day YYYY-MM-DD')
    args = parser.parse_args()
    from src.config.paths import get_runtime_root
    report = run(get_runtime_root() / 'nhplug', args.day)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
