"""Read-only, exploratory study of vibe strength entries; never changes trading state.

The 90% hypothesis was chosen after inspecting three losing trades on 2026-11-16.
It is not an independently validated rule, and fees and execution of blocked trades
are unknown. Only explicitly linked and fully closed trades enter outcome groups.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
RANGE_THRESHOLD = 90.0
LIMITATION = ('90% 기준은 2026-11-16 손실 3건을 본 후 정한 탐색 가설로 선택 편향이 있으며, '
              '비용·세금·차단 시 대체 거래와 미체결 기회를 반영하지 않습니다. ' 
              '확인 체결가 차이는 가설의 수익성이나 실제 차단 효과를 증명하지 않습니다.')


def stamp(value):
    try:
        at = datetime.fromisoformat(value)
        return at.astimezone(KST) if at.tzinfo is not None and at.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def number(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def market_evidence(item):
    """Use only the exact persisted model input whose digest verifies."""
    record = item.get('_study_model_input')
    started = stamp(item.get('time'))
    if not isinstance(record, dict) or started is None:
        return {'status': 'unavailable'}
    try:
        text, answer, digest = record['inputs'], record['response'], item['input_digest']
        if (not all(isinstance(x, str) for x in (text, answer, digest))
                or hashlib.sha256(f'vibe:scores:{text}:{answer}'.encode()).hexdigest() != digest):
            raise ValueError('digest_mismatch')
        context = json.loads(text)['context']
        captured, stored = stamp(context['time']), stamp(record['stored_at'])
        market = context['market_regime_themes_global']
        as_of = datetime.strptime(market['as_of'], '%Y-%m-%dT%H:%M').replace(tzinfo=KST)
        if (captured is None or stored is None or captured.date() != started.date()
                or not captured <= started <= stored or as_of.date() != started.date()
                or not timedelta(0) <= captured - as_of <= timedelta(minutes=20)):
            raise ValueError('market_time_invalid')
        regimes = {key: market[key]['regime'] for key in ('코스피', '코스닥')
                   if isinstance(market.get(key), dict) and market[key].get('regime') in ('상승장', '횡보장', '하락장')}
        if not regimes:
            raise ValueError('market_missing')
        return {'status': 'ok', 'as_of': as_of.isoformat(), 'model_input_recorded_at': stored.isoformat(),
                'regimes': regimes}
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {'status': 'unavailable'}


def _episodes(fills):
    """Group one symbol's fills from flat to flat, without assuming absent history."""
    books, by_order = {}, {}
    for row in fills:
        oid, symbol, side, qty, price, raw_time = row
        at, price = stamp(raw_time), number(price)
        if (not isinstance(oid, str) or not oid or not re.fullmatch(r'\d{6}', str(symbol))
                or side not in ('BUY', 'SELL') or type(qty) is not int or qty <= 0
                or price is None or price <= 0 or at is None or oid in by_order):
            raise ValueError('fill_invalid')
        book = books.setdefault(symbol, {'held': 0, 'orders': [], 'buys': [], 'sells': [], 'pnl': 0.0, 'cost': 0.0, 'last': None})
        if book['last'] is not None and at < book['last']:
            raise ValueError('fill_time_order_invalid')
        book['last'] = at
        if side == 'BUY':
            book['cost'] += qty * price
            book['held'] += qty
            book['buys'].append(oid)
        else:
            if qty > book['held']:
                raise ValueError('sell_exceeds_position')
            average = book['cost'] / book['held']
            book['pnl'] += (price - average) * qty
            book['cost'] -= average * qty
            book['held'] -= qty
            book['sells'].append(oid)
        book['orders'].append(oid)
        by_order[oid] = book
        if book['held'] == 0:
            book['closed_at'] = at.isoformat()
            books[symbol] = {'held': 0, 'orders': [], 'buys': [], 'sells': [], 'pnl': 0.0, 'cost': 0.0, 'last': at}
    return by_order


def analyze(advice, executions, events, fills, intents, links, day):
    """Join explicit advice/decision/cycle evidence; temporal coincidences remain unverified."""
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('day_invalid')
    advice_by_id = {}
    duplicate_ids = set()
    for item in advice:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            continue
        ident = item['id']
        if ident in advice_by_id:
            duplicate_ids.add(ident)
        advice_by_id[ident] = item
    execution_by_key = {}
    duplicate_exec = set()
    for ident, idx, outcome, at in executions:
        key = (ident, idx)
        if key in execution_by_key:
            duplicate_exec.add(key)
        execution_by_key[key] = (outcome, at)
    events_by_key = {}
    for item in events:
        if not isinstance(item, dict) or item.get('event') != 'submitted':
            continue
        key = (item.get('advice_id'), item.get('decision_idx'))
        if isinstance(key[0], str) and type(key[1]) is int:
            events_by_key.setdefault(key, []).append(item)
    link_by_cycle = {}
    for oid, cid in links:
        link_by_cycle.setdefault(cid, []).append(oid)
    intent_by_id = {row[0]: row for row in intents}
    episodes = _episodes(fills)
    by_id = {row[0]: row for row in fills}
    rows = []
    for ident, item in advice_by_id.items():
        started = stamp(item.get('time'))
        if started is None or started.date().isoformat() != day:
            continue
        decisions = item.get('decisions')
        if not isinstance(decisions, list):
            continue
        for idx, decision in enumerate(decisions):
            if not isinstance(decision, dict) or decision.get('action') != 'BUY' or decision.get('route') != 'strength':
                continue
            code = decision.get('symbol')
            if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
                continue
            technical_all = item.get('technical_basic')
            technical = (technical_all.get(code) or {}) if isinstance(technical_all, dict) else {}
            if not isinstance(technical, dict):
                technical = {}
            range_pct = number(technical.get('range_position_pct'))
            if range_pct is not None and not 0 <= range_pct <= 100:
                range_pct = None
            raw_asof = technical.get('as_of')
            try:
                bar_at = datetime.strptime(day + raw_asof, '%Y-%m-%d%H:%M').replace(tzinfo=KST) if isinstance(raw_asof, str) else None
            except ValueError:
                bar_at = None
            valid_tech = bar_at is not None and bar_at <= started
            if not valid_tech:
                range_pct = None
            key = (ident, idx)
            execution = execution_by_key.get(key)
            result = {'advice_id': ident, 'decision_idx': idx, 'symbol': code,
                      'model_started_at': started.isoformat(), 'decision_available_at': None,
                      'market_context': market_evidence(item),
                      'technical_as_of': bar_at.isoformat() if valid_tech else None,
                      'range_position_pct': range_pct, 'rsi': number(technical.get('rsi')) if valid_tech else None,
                      'vs_vwap_pct': number(technical.get('vs_vwap_pct')) if valid_tech else None,
                      'hypothesis_group': ('range_ge_90' if range_pct is not None and range_pct >= RANGE_THRESHOLD else
                                           'range_lt_90' if range_pct is not None else 'technical_unavailable'),
                      'execution_outcome': execution[0] if execution else 'no_execution_record',
                      'evidence': 'unverified', 'status': 'association_unverified'}
            matching = events_by_key.get(key, [])
            if ident in duplicate_ids or key in duplicate_exec or len(matching) > 1:
                result['status'] = 'duplicate_or_ambiguous'
            elif len(matching) == 1:
                event = matching[0]
                available = stamp(event.get('decision_available_at') or item.get('available_at'))
                sent = stamp(event.get('time'))
                orders = link_by_cycle.get(event.get('cycle_id'), [])
                if (available is None or sent is None or not started <= available <= sent
                        or event.get('symbol') != code or event.get('source') != 'vibe-trading'
                        or len(orders) != 1 or not execution
                        or stamp(execution[1]) is None or stamp(execution[1]) < sent
                        or execution[0] != 'buy_submitted'
                        or (result['market_context'].get('status') == 'ok'
                            and stamp(result['market_context']['model_input_recorded_at']) > available)):
                    result['status'] = 'explicit_link_incomplete'
                else:
                    oid = orders[0]
                    intent = intent_by_id.get(oid)
                    if (not intent or intent[1:3] != (code, 'BUY') or type(intent[3]) is not int
                            or intent[3] <= 0 or event.get('qty') != intent[3]):
                        result['status'] = 'intent_unverified'
                    else:
                        result.update(evidence='explicit', decision_available_at=available.isoformat())
                        fill = by_id.get(oid)
                        if fill is None:
                            result['status'] = 'terminal_no_confirmed_fill' if intent[4] == 'terminal' else 'unresolved_no_confirmed_fill'
                        elif fill[1] != code or fill[2] != 'BUY' or fill[3] > intent[3] or stamp(fill[5]) < sent:
                            result['status'] = 'fill_unverified'
                        elif fill[3] < intent[3] or intent[4] != 'terminal':
                            result['status'] = 'partial_or_unresolved'
                        else:
                            episode = episodes[oid]
                            if len(episode['buys']) != 1:
                                result['status'] = 'multiple_entries_unattributable'
                            elif episode['held'] != 0 or not episode['sells']:
                                result['status'] = 'open_position'
                            else:
                                result.update(status='closed_single_entry', fill_at=fill[5],
                                              close_at=episode['closed_at'], realized_before_costs=round(episode['pnl'], 2))
            rows.append(result)
    rows.sort(key=lambda row: row['model_started_at'])
    groups = {}
    for name in ('range_ge_90', 'range_lt_90', 'technical_unavailable'):
        selected = [x for x in rows if x['hypothesis_group'] == name]
        confirmed = [x for x in selected if x['status'] == 'closed_single_entry']
        groups[name] = {'decisions': len(selected), 'verified_closed': len(confirmed),
                        'realized_before_costs': round(sum(x['realized_before_costs'] for x in confirmed), 2) if confirmed else None,
                        'status_counts': dict(Counter(x['status'] for x in selected))}
    return {'day': day, 'mode': 'read_only_exploratory', 'hypothesis': 'range_position_pct >= 90',
            'threshold_pct': RANGE_THRESHOLD, 'hypothesis_selection': '2026-11-16 손실 3건을 본 뒤 사후 선정',
            'limitations': LIMITATION, 'comparison_scope': '동일 원장의 직접 연결된 단일 진입·확인 종료만',
            'groups': groups, 'decisions': rows}


def load_and_analyze(nhplug, day, ledger=None):
    """Read existing databases and logs; missing evidence is an error, never an empty success."""
    from engine_roles import ledger_root
    from sqlite_ro import ro_connect
    nhplug = Path(nhplug)
    root = Path(ledger) if ledger else ledger_root(nhplug, 'vibe')
    if root is None:
        raise ValueError('ledger_unavailable')
    with ro_connect(nhplug / 'shared-etf-paper' / 'market-research.sqlite3') as db:
        advice = [json.loads(x[0]) for x in db.execute("SELECT payload FROM snapshots WHERE kind='vibe_advice' AND time>=? AND time<? ORDER BY id", (day, (date.fromisoformat(day) + timedelta(days=1)).isoformat()))]
        for item in advice:
            if not isinstance(item, dict) or not isinstance(item.get('input_digest'), str):
                continue
            row = db.execute("SELECT time,inputs,response FROM model_inputs WHERE digest=? AND engine='vibe' AND question='scores'", (item['input_digest'],)).fetchone()
            if row:
                item['_study_model_input'] = dict(zip(('stored_at', 'inputs', 'response'), row))
    with ro_connect(root / 'orders.sqlite3') as db:
        fills = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
        executions = db.execute('SELECT advice_id,idx,outcome,time FROM advice_executions').fetchall()
        links = db.execute('SELECT order_id,cycle_id FROM decision_order_links').fetchall()
        intents = db.execute('SELECT id,symbol,side,qty,status FROM intents').fetchall()
    wanted = {(item['id'], index) for item in advice if isinstance(item, dict) and isinstance(item.get('id'), str)
              for index, decision in enumerate(item.get('decisions') if isinstance(item.get('decisions'), list) else [])
              if isinstance(decision, dict) and decision.get('action') == 'BUY' and decision.get('route') == 'strength'}
    events = []
    with (root / 'events.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            if 'submitted' not in line or day not in line:
                continue
            event = json.loads(line)
            if (isinstance(event, dict) and isinstance(event.get('advice_id'), str)
                    and type(event.get('decision_idx')) is int
                    and (event['advice_id'], event['decision_idx']) in wanted):
                events.append(event)
    result = analyze(advice, executions, events, fills, intents, links, day)
    result['ledger'] = root.name
    result['ledger_scope'] = '지정 원장 또는 현재 엔진 역할; 과거 날짜의 역할은 별도 확인 필요'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', default=datetime.now(KST).date().isoformat())
    parser.add_argument('--nhplug', type=Path, default=Path.home() / '.vibe-trading/nhplug')
    parser.add_argument('--ledger', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(load_and_analyze(args.nhplug, args.day, args.ledger), ensure_ascii=False, indent=2))
    except (OSError, ValueError, TypeError, sqlite3.Error, json.JSONDecodeError):
        print(json.dumps({'day': args.day, 'mode': 'unavailable', 'reason': 'input_unavailable_or_invalid'}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
