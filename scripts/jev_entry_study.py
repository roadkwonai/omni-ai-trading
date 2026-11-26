"""Study Jev alternatives from a closed backup without contacting a broker or model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS
from position_paths import close_after, close_at, day_bars, pct
from sqlite_ro import ro_connect
from src.tools.nhplug_calendar import shifted_time

KST = ZoneInfo('Asia/Seoul')
EVENTS = ('jev_shadow', 'v2_entry_gate', 'v2_execution_gate', 'submitted', 'v2_stopped', 'stopped')
DATABASES = ('trading-ledger.sqlite3', 'market-research.sqlite3', 'orders.sqlite3')


def immutable(path):
    return closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True))


def snapshot_folder(path):
    path = path.expanduser().resolve()
    if (path / 'shared-etf-paper').is_dir():
        path = (path / 'shared-etf-paper').resolve()
    live = (Path.home() / '.vibe-trading' / 'nhplug').resolve()
    if path == live or live in path.parents:
        raise ValueError('운영 폴더는 백업으로 사용할 수 없습니다')
    for name in DATABASES:
        db = path / name
        if db.is_symlink():
            raise ValueError(f'백업 DB는 심볼릭 링크일 수 없습니다: {name}')
        if not db.is_file():
            raise ValueError(f'백업 파일이 없습니다: {name}')
        if any(Path(str(db) + suffix).exists() for suffix in ('-wal', '-shm')):
            raise ValueError(f'열려 있거나 WAL이 남은 백업입니다: {name}')
    return path


def output_folder(path, snapshot):
    path = path.expanduser().resolve()
    protected = [(Path.home() / '.vibe-trading' / 'nhplug').resolve(),
                 (Path.home() / '.vibe-trading' / 'backups').resolve(), snapshot]
    if any(path == base or base in path.parents or path in base.parents for base in protected):
        raise ValueError('출력은 운영·백업 경로와 분리된 폴더에 지정해야 합니다')
    return path


def scratch_ledger(source, directory):
    target = Path(directory) / 'trading-ledger.sqlite3'
    with immutable(source) as db, closing(sqlite3.connect(target)) as copy:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('백업 원장 무결성 검사 실패')
        db.backup(copy)
        copy.execute('PRAGMA journal_mode=DELETE')
    return target


def read_events(db, day):
    start = day.isoformat()
    end = (day + timedelta(days=1)).isoformat()
    marks = ','.join('?' for _ in EVENTS)
    rows = db.execute(f'SELECT digest, data FROM events_v2 WHERE event IN ({marks}) '
                      'AND time >= ? AND time < ? ORDER BY time, source, offset', (*EVENTS, start, end))
    seen, cycles, stopped = set(), defaultdict(list), False
    for digest, raw in rows:
        if digest in seen:
            continue
        seen.add(digest)
        item = json.loads(raw)
        if item.get('event') in ('v2_stopped', 'stopped'):
            stopped = True
        if item.get('cycle_id'):
            cycles[item['cycle_id']].append(item)
    return cycles, stopped


def prices(series, at, day):
    base = close_at(series, at, observed_only=True, max_age=timedelta(minutes=2))
    reference = datetime.combine(day, shifted_time(time(15), day), KST)
    close = datetime.combine(day, shifted_time(time(15, 30), day), KST)
    result = {'base': base, 'r5_pct': None, 'r15_pct': None, 'last_pct': None,
              'close_pct': None, 'source': 'recorded_completed_1m',
              'last_reference': reference.isoformat(), 'close_reference': close.isoformat(), 'fill_verified': False}
    if base is None or base <= 0:
        return result
    for minutes in (5, 15):
        target = at + timedelta(minutes=minutes)
        if target <= close:
            tolerance = min(timedelta(minutes=2), close - target)
            result[f'r{minutes}_pct'] = pct(close_after(series, target, tolerance, observed_only=True), base)
    if at < reference:
        result['last_pct'] = pct(close_at(series, reference, observed_only=True, max_age=timedelta(minutes=2)), base)
    if at < close:
        result['close_pct'] = pct(close_after(series, close, timedelta(0)), base)
    return result


def study_day(ledger, research, orders, day, source):
    cycles, stopped = read_events(ledger, day)
    start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
    fills = defaultdict(list)
    for cid, symbol, side, quantity, price, stamp in orders.execute(
        'SELECT l.cycle_id, f.symbol, f.side, f.quantity, f.price, f.time FROM fills f '
        'JOIN decision_order_links l ON l.order_id=f.order_id WHERE f.time>=? AND f.time<?', (start, end)):
        fills[cid].append({'symbol': symbol, 'side': side, 'quantity': quantity, 'price': price, 'time': stamp})
    all_buys = [(symbol, stamp) for symbol, stamp in orders.execute(
        "SELECT symbol,time FROM fills WHERE side='BUY' AND time>=? AND time<? ORDER BY time", (start, end))]
    digests = sorted({e.get('jev_input_digest') for events in cycles.values() for e in events
                      if e.get('event') == 'jev_shadow' and e.get('jev_input_digest')})
    known = set()
    for offset in range(0, len(digests), 500):
        batch = digests[offset:offset + 500]
        placeholders = ','.join('?' for _ in batch)
        known.update(row[0] for row in research.execute(
            f'SELECT digest FROM model_inputs WHERE digest IN ({placeholders})', batch))
    intents = defaultdict(list)
    for cid, symbol, side, qty, status in orders.execute(
        'SELECT l.cycle_id, i.symbol, i.side, i.qty, i.status FROM intents i '
        'JOIN decision_order_links l ON l.order_id=i.id WHERE i.day=?', (day.strftime('%Y%m%d'),)):
        intents[cid].append({'symbol': symbol, 'side': side, 'quantity': qty, 'status': status})
    pending = orders.execute("SELECT COUNT(*) FROM intents WHERE status!='terminal'").fetchone()[0]
    bars = day_bars(Path(ledger.execute('PRAGMA database_list').fetchone()[2]), start)
    counts = Counter()
    records = []
    cycle_rows = []
    multiple_replies = 0
    for cid, events in cycles.items():
        replies = [e for e in events if e.get('event') == 'jev_shadow']
        if len(replies) > 1:
            multiple_replies += 1
        for reply in replies[:1]:
            counts['candidate_responses'] += 1
            digest = reply.get('jev_input_digest')
            matched = digest in known
            counts['inputs_linked'] += matched
            submissions = [e for e in events if e.get('event') == 'submitted']
            buys = [f for f in fills[cid] if f['side'] == 'BUY']
            counts['cycles_with_submission'] += bool(submissions)
            counts['cycles_with_buy_fill'] += bool(buys)
            selected = SYMBOLS.get(reply.get('jev_choice'), reply.get('jev_choice'))
            counts['fill_choice_differs'] += bool(buys and all(f['symbol'] != selected for f in buys))
            raw_time = reply.get('jev_response_time') or reply.get('time')
            try:
                at = datetime.fromisoformat(raw_time)
                if at.utcoffset() is None:
                    raise ValueError
            except (TypeError, ValueError):
                at = None
            final_symbols = {s.get('symbol') for s in submissions} | {f['symbol'] for f in buys}
            gates = [e for e in events if e.get('event') in ('v2_entry_gate', 'v2_execution_gate')]
            sent_qty = sum(i['quantity'] for i in intents[cid] if i['side'] == 'BUY')
            filled_qty = sum(f['quantity'] for f in buys)
            execution = ('partial_fill' if sent_qty and 0 < filled_qty < sent_qty else
                         'filled' if filled_qty and sent_qty and filled_qty >= sent_qty else
                         'submitted_unconfirmed' if submissions else
                         'blocked' if any(e.get('permitted') is False for e in gates) else 'unknown')
            raw_probabilities = reply.get('jev_probabilities')
            probabilities = raw_probabilities if isinstance(raw_probabilities, dict) else {}
            by_symbol = {}
            for name, value in probabilities.items():
                symbol = SYMBOLS.get(name, name)
                if (re.fullmatch(r'[0-9A-Za-z]{6}', symbol) and type(value) in (int, float)
                        and 0 <= value <= 1):
                    by_symbol[symbol] = max(value, by_symbol.get(symbol, 0))
            ranked = sorted(by_symbol.items(), key=lambda pair: (-pair[1], pair[0]))
            cycle_rows.append({'cycle_id': cid, 'execution_status': execution, 'response_time': raw_time, 'model_choice': selected,
                               'model_second_symbol': ranked[1][0] if len(ranked) > 1 else None,
                               'input_digest': digest, 'input_linked': matched, 'gates': gates,
                               'submissions': submissions, 'intents': intents[cid], 'fills': fills[cid]})
            rank = 0
            for model_rank, (symbol, value) in enumerate(ranked, 1):
                if symbol == selected or symbol in final_symbols:
                    continue
                rank += 1
                if rank > 2:
                    break
                records.append({
                    'source': source, 'day': start, 'cycle_id': cid, 'symbol': symbol, 'rank': rank,
                    'model_rank': model_rank, 'probability': value, 'model_choice': selected, 'response_time': raw_time,
                    'input_digest': digest, 'input_linked': matched,
                    'submissions': submissions, 'fills': fills[cid],
                    'gates': gates,
                    'next_buy_fill_time': next((stamp for code, stamp in all_buys if code == symbol and stamp > raw_time), None) if at else None,
                    'eligibility': 'unknown',
                    'missing': ['candidate_quote', 'candidate_guard_state', 'independent_entry_decision'],
                    'price_movement': prices(bars.get(symbol, []), at, day) if at else None,
                })
    counts['alternatives'] = len(records)
    counts['alternative_symbols'] = len({record['symbol'] for record in records})
    counts['alternative_cycles'] = len({record['cycle_id'] for record in records})
    for execution in ('blocked', 'submitted_unconfirmed', 'partial_fill', 'filled', 'unknown'):
        counts[f'cycles_{execution}'] = sum(row['execution_status'] == execution for row in cycle_rows)
    missing_inputs = counts['candidate_responses'] - counts['inputs_linked']
    warnings = []
    if not stopped:
        warnings.append('session_stop_not_recorded')
    if pending:
        warnings.append('unresolved_order_in_snapshot')
    if missing_inputs:
        warnings.append('model_inputs_missing')
    if not counts['candidate_responses']:
        warnings.append('candidate_responses_missing')
    if multiple_replies:
        warnings.append('multiple_replies_in_cycle')
    warnings.append('cross_database_capture_not_atomic')
    warnings.append('alternative_quote_and_capacity_unavailable')
    latest = {
        'event': ledger.execute('SELECT MAX(time) FROM events_v2 WHERE time>=? AND time<?', (start, end)).fetchone()[0],
        'input': research.execute('SELECT MAX(time) FROM model_inputs WHERE time>=? AND time<?', (start, end)).fetchone()[0],
        'fill': orders.execute('SELECT MAX(time) FROM fills WHERE time>=? AND time<?', (start, end)).fetchone()[0],
    }
    return {'day': start, 'source': source, 'engine': 'jev', 'status': 'incomplete',
            'completeness': {'warnings': warnings, 'unresolved_orders': pending,
                             'candidate_inputs_missing': missing_inputs, 'latest_timestamps': latest},
            'counts': dict(counts), 'cycles': cycle_rows, 'candidates': records}


def atomic_write(path, payload):
    fd, temp = tempfile.mkstemp(prefix='.jev-entry-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--from', dest='start', required=True, type=date.fromisoformat)
    parser.add_argument('--to', dest='end', required=True, type=date.fromisoformat)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.start > args.end or args.end == date.max:
        parser.error('유효한 분석 날짜 범위를 지정해야 합니다')
    try:
        snapshot = snapshot_folder(args.snapshot)
        output = output_folder(args.output, snapshot)
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='jev-entry-study-') as scratch:
            ledger_copy = scratch_ledger(snapshot / DATABASES[0], scratch)
            with ro_connect(ledger_copy) as ledger, immutable(snapshot / DATABASES[1]) as research, immutable(snapshot / DATABASES[2]) as orders:
                summaries = []
                day = args.start
                while day <= args.end:
                    report = study_day(ledger, research, orders, day, str(snapshot))
                    atomic_write(output / f'{day.isoformat()}.json', report)
                    summaries.append({'day': report['day'], 'status': report['status'], 'counts': report['counts'],
                                      'warnings': report['completeness']['warnings']})
                    day += timedelta(days=1)
        print(json.dumps({'source': str(snapshot), 'days': summaries}, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'분석 실패: {exc}\n')


if __name__ == '__main__':
    main()
