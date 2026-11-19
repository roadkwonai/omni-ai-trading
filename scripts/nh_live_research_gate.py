"""Read-only evidence summary; never grants live trading permission."""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import Counter, deque
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from etf_universe import is_code

KST = ZoneInfo('Asia/Seoul')


def _timestamp(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime):
        raise ValueError('invalid timestamp')
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('timestamp requires timezone')
    return stamp.astimezone(KST)


def _open(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError('research database unavailable')
    return sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)


def _observations(path, now):
    with closing(_open(path)) as db:
        rows = db.execute('SELECT status, decision_time FROM price_observations').fetchall()
    counts = Counter()
    times = []
    for status, value in rows:
        if status not in ('observed', 'insufficient', 'not_recorded'):
            raise ValueError('unknown observation status')
        stamp = _timestamp(value)
        if stamp > now:
            raise ValueError('future observation')
        counts[status] += 1
        times.append(stamp)
    return {'available': True, 'observed': counts['observed'], 'insufficient': counts['insufficient'],
            'not_recorded': counts['not_recorded'], 'latest': max(times).isoformat() if times else None,
            'record_dates': len({stamp.date() for stamp in times}), 'independent_samples': None}


def _research(path, now):
    with closing(_open(path)) as db:
        # the latest-run table; a model file written before 11/19 has only the old per-run table (same distinct targets)
        table = 'jev_predictions_latest' if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='jev_predictions_latest'").fetchone() else 'jev_predictions'
        columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if not {'source', 'offset', 'symbol', 'jev_time'}.issubset(columns):
            raise ValueError('Jev research schema unavailable')
        rows = db.execute(f'SELECT source, offset, symbol, jev_time FROM {table}').fetchall()
    samples, times = set(), []
    for source, offset, symbol, value in rows:
        if not isinstance(source, str) or not source or type(offset) is not int or offset < 0 or not isinstance(symbol, str) or not symbol:
            raise ValueError('invalid research identity')
        stamp = _timestamp(value)
        if stamp > now:
            raise ValueError('future research record')
        samples.add((source, offset, symbol))
        times.append(stamp)
    return {'available': True, 'unique_predictions': len(samples),
            'latest': max(times).isoformat() if times else None, 'independent_samples': None}


def _fills(path, now):
    with closing(_open(path)) as db:
        scope = db.execute("SELECT value FROM meta WHERE name='account_sha256'").fetchone()
        if not scope or not isinstance(scope[0], str) or not re.fullmatch(r'[0-9a-f]{64}', scope[0]):
            raise ValueError('paper account identity unavailable')
        rows = db.execute('''SELECT f.symbol,f.side,f.quantity,f.price,f.time,
            i.symbol,i.side,i.qty,i.status,i.order_number
            FROM fills f LEFT JOIN intents i ON i.id=f.order_id ORDER BY f.rowid''').fetchall()
        unresolved = db.execute("SELECT COUNT(*) FROM intents WHERE status IS NULL OR status!='terminal'").fetchone()[0]
    lots, times = {}, []
    closed_units = 0
    for symbol, side, qty, price, value, intent_symbol, intent_side, intent_qty, status, number in rows:
        if (not is_code(symbol) or side not in ('BUY', 'SELL')
                or type(qty) is not int or qty <= 0 or type(price) not in (int, float)
                or not math.isfinite(price) or price <= 0 or symbol != intent_symbol or side != intent_side
                or type(intent_qty) is not int or qty > intent_qty or status != 'terminal'
                or type(number) is not int or number <= 0):
            raise ValueError('fill is not linked to a terminal order')
        stamp = _timestamp(value)
        if stamp > now or (times and stamp < times[-1]):
            raise ValueError('fill ordering unavailable')
        times.append(stamp)
        queue = lots.setdefault(symbol, deque())
        if side == 'BUY':
            queue.append(qty)
        else:
            remaining = qty
            while remaining and queue:
                matched = min(remaining, queue[0])
                queue[0] -= matched
                remaining -= matched
                closed_units += matched
                if queue[0] == 0:
                    queue.popleft()
            if remaining:
                raise ValueError('sale exceeds tracked buys')
    return {'available': True, 'local_reconciled_fills': len(rows), 'closed_units': closed_units,
            'open_units': sum(sum(queue) for queue in lots.values()), 'unresolved_orders': unresolved,
            'latest_confirmation': times[-1].isoformat() if times else None,
            'broker_independently_checked': False}


def evaluate(root, now=None):
    """Summarize local evidence; prices and fills cannot by themselves promote a strategy."""
    root = Path(root)
    now = _timestamp(now or datetime.now(KST))
    result = {'status': 'NOT_READY', 'as_of': now.isoformat(), 'account_scope': 'paper',
              'costs': {'available': False, 'net_return_pct': None,
                        'reason': '실제 체결별 수수료·세금·슬리피지 근거를 확인하지 못했습니다.'},
              'promotion_criteria': {'available': False, 'reason': '승격 기준과 독립 전진 검증이 확인되지 않았습니다.'},
              'live_guards': {'evaluated': False},
              'limitations': ['로컬 원장만 조회했으며 증권사 내역과 별도 대조하지 않았습니다.',
                              '가격 방향 관측과 예측 건수는 체결 성과 또는 독립 표본 수가 아닙니다.',
                              '이 결과는 실계좌 주문 권한이나 자동 승격을 부여하지 않습니다.']}
    for name, fn, filename in (
            ('price_observations', _observations, 'paper-observations.sqlite3'),
            ('jev_research', _research, 'paper-model.sqlite3'),
            ('paper_fills', _fills, 'orders.sqlite3')):
        try:
            result[name] = fn(root / filename, now)
        except (OSError, sqlite3.Error, ValueError, TypeError, OverflowError):
            result[name] = {'available': False, 'reason': '자료가 없거나 형식·시각을 검증할 수 없습니다.'}
    return result


def main():
    from src.config.paths import get_runtime_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    report = evaluate(get_runtime_root() / 'nhplug/shared-etf-paper')
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
