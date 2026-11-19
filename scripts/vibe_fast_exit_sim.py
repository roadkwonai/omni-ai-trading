"""Offline, isolated fast-exit limit-order replay from recorded observations.

No broker calls or writes to an engine ledger. A hypothetical fill assumes the
next independently recorded bid and its displayed depth remain available; it is
not an actual fill. Costs, queue priority and market impact are unknown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from datetime import timedelta
from pathlib import Path

from vibe_fast_exit_watch import _time, compare_observations

SOURCE_LABELS = {'jev_event': 'Jev 추가 판단', 'jev_existing': 'Jev 기존 판단 재사용',
                 'gpt': 'GPT 판단', 'unknown': '판단 출처 미확인'}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def decision_source(record):
    """Classify only recorded provenance; a missing source alone does not prove GPT."""
    source = record.get('source')
    if source in ('jev_event', 'jev_existing'):
        return source
    if source in ('gpt', 'openai', 'openai-codex'):
        return 'gpt'
    usage = record.get('usage')
    if source is None and isinstance(usage, dict):
        provider, model = usage.get('provider'), usage.get('model')
        if ((isinstance(provider, str) and provider.lower().startswith('openai'))
                or (isinstance(model, str) and ('gpt-' in model.lower() or model.lower().startswith('openai')))):
            return 'gpt'
    return 'unknown'


def _jev_exit_verified(record, analysis, source):
    """A Jev exit needs its own recorded confidence and threshold, never an inferred default."""
    if source not in ('jev_event', 'jev_existing'):
        return True
    evidence = record.get('model_evidence')
    if not isinstance(evidence, dict) or evidence.get('choice') != 'SELL':
        return False
    confidence, threshold = evidence.get('confidence'), evidence.get('sell_confidence')
    return (type(confidence) in (int, float) and type(threshold) in (int, float)
            and math.isfinite(confidence) and math.isfinite(threshold)
            and 0 <= threshold <= confidence <= 1
            and type(analysis.get('confidence')) in (int, float)
            and type(analysis.get('sell_confidence')) in (int, float)
            and analysis.get('confidence') == confidence and analysis.get('sell_confidence') == threshold)


def summarize_event_sources(results):
    """Keep known Jev request attempts separate from unknown counts and monetary costs."""
    counts = {name: {'label': label, 'attempts': 0, 'completed': 0,
                     'api_calls': 'unknown', 'api_calls_known': 0,
                     'api_calls_unknown_attempts': 0, 'api_cost': 'unknown'}
              for name, label in SOURCE_LABELS.items()}
    for _, status, raw in results:
        try:
            record = json.loads(raw) if raw else {}
            source = decision_source(record) if isinstance(record, dict) else 'unknown'
        except (TypeError, ValueError):
            record, source = {}, 'unknown'
        counts[source]['attempts'] += 1
        if status == 'completed':
            counts[source]['completed'] += 1
        recorded = record.get('api_calls') if isinstance(record, dict) else None
        raw_source = record.get('source') if isinstance(record, dict) else None
        valid = (type(recorded) is int and recorded in (0, 1)
                 and ((raw_source == 'jev_existing' and recorded == 0)
                      or (raw_source == 'jev_event' and recorded == 1)
                      or raw_source == 'unavailable'))
        if valid:
            counts[source]['api_calls_known'] += recorded
        else:
            counts[source]['api_calls_unknown_attempts'] += 1
    return counts


def simulate(rows, ledger_fills=None):
    """Replay one isolated sell limit per position generation, never using its triggering quote."""
    orders, chronological = {}, []
    for row in rows:
        if not isinstance(row, dict) or row.get('mode') != 'observation_only':
            continue
        inputs = row.get('inputs')
        if not isinstance(inputs, dict):
            continue
        at, quote_at = _time(row.get('observed_at')), _time(inputs.get('quote_at'))
        evaluated = _time(inputs.get('evaluated_at'))
        symbol, generation, qty = row.get('symbol'), row.get('position_id'), row.get('qty')
        bid = inputs.get('bid')
        if (row.get('state') not in ('candidate', 'wait', 'unavailable') or at is None or evaluated is None or quote_at is None
                or not quote_at <= evaluated <= at or at - quote_at > timedelta(seconds=30)
                or not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol)
                or not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{20}', generation)
                or type(qty) is not int or qty <= 0 or not _number(bid)):
            continue
        chronological.append((at, quote_at, symbol, generation, qty, bid, row))
    chronological.sort(key=lambda r: r[0])
    for at, quote_at, symbol, generation, qty, bid, row in chronological:
        key = symbol, generation
        order = orders.get(key)
        if order is None:
            entry = row['inputs'].get('entry_price')
            if row.get('state') != 'candidate' or row.get('candidate') is not True or not _number(entry):
                continue
            orders[key] = {'symbol': symbol, 'position_id': generation, 'order_at': at.isoformat(),
                           'trigger_quote_at': quote_at.isoformat(), 'limit': bid, 'quantity': qty,
                           'remaining': qty, 'entry_price': entry, 'fills': [], 'last_quote': quote_at}
            continue
        if (not order['remaining'] or order.get('position_changed') or quote_at <= order['last_quote']
                or quote_at <= _time(order['order_at'])):
            continue
        order['last_quote'] = quote_at
        if (qty != order['quantity'] or at.date() != _time(order['order_at']).date()
                or (ledger_fills is not None and any(s == symbol and _time(order['order_at']) <= t <= at
                                                     for s, t in ledger_fills))):
            order['position_changed'] = True
            continue
        size = row['inputs'].get('bid_size')
        # Size must be the displayed best-bid depth from the same verified quote.
        if (ledger_fills is None or bid < order['limit'] or row['inputs'].get('bid_size_verified') is not True
                or type(size) is not int or size <= 0):
            continue
        fill_qty = min(size, order['remaining'])
        order['fills'].append({'quote_at': quote_at.isoformat(), 'observed_at': at.isoformat(),
                               'price': order['limit'], 'quantity': fill_qty, 'displayed_bid_size': size})
        order['remaining'] -= fill_qty
    output = []
    for order in orders.values():
        order.pop('last_quote')
        order['state'] = ('position_changed_unknown' if order.get('position_changed') else
                          'filled_assumed' if order['remaining'] == 0 else
                          'partial_assumed' if order['fills'] else 'pending_unknown')
        order['gross_pnl_assumed'] = sum((f['price'] - order['entry_price']) * f['quantity'] for f in order['fills']) if order['fills'] else None
        order['costs'] = 'unknown'
        order['limitation'] = '독립 기록의 가상 지정가 재현; 관측 잔량·호가 유지와 체결은 보장되지 않으며 수수료·세금·시장충격 미반영'
        output.append(order)
    return output


def _candidate_matches_ledger(row, fills):
    at = _time(row.get('observed_at'))
    evaluated = _time((row.get('inputs') or {}).get('evaluated_at')) if isinstance(row.get('inputs'), dict) else None
    symbol = row.get('symbol')
    if at is None or evaluated is None or evaluated > at or not isinstance(symbol, str):
        return False
    held, generation, latest = 0, None, None
    for order_id, code, side, qty, stamp in fills:
        if code != symbol or stamp > at:
            continue
        if evaluated <= stamp <= at:
            return False
        if (not isinstance(order_id, str) or not order_id or type(qty) is not int or qty <= 0
                or side not in ('BUY', 'SELL') or (latest is not None and stamp < latest)):
            return False
        latest = stamp
        if side == 'BUY':
            if not held:
                generation = hashlib.sha256(f'{symbol}\0{order_id}'.encode()).hexdigest()[:20]
            held += qty
        else:
            held -= qty
            if held < 0:
                return False
            if not held:
                generation = None
    return held == row.get('qty') and generation is not None and generation == row.get('position_id')


def _position_at(fills, symbol, at):
    held, average, generation, latest = 0, 0.0, None, None
    for order_id, code, side, qty, price, stamp in fills:
        if code != symbol or stamp > at:
            continue
        if (not isinstance(order_id, str) or not order_id or side not in ('BUY', 'SELL')
                or type(qty) is not int or qty <= 0 or not _number(price)
                or (latest is not None and stamp < latest[1])):
            return None
        latest = (order_id, stamp)
        if side == 'BUY':
            if not held:
                generation = hashlib.sha256(f'{symbol}\0{order_id}'.encode()).hexdigest()[:20]
            average = (average * held + price * qty) / (held + qty)
            held += qty
        else:
            held -= qty
            if held < 0:
                return None
            if not held:
                average, generation = 0.0, None
    return {'qty': held, 'entry_price': average, 'position_id': generation,
            'latest_fill_id': latest[0] if latest else None}


def simulate_event_results(rows, results, fills):
    """Recreate experimental AI exits without treating model output as an order or fill."""
    output, rejected = [], 0
    ledger_times = [(symbol, stamp) for _, symbol, _, _, _, stamp in fills]
    for attempt_id, status, result in results:
        try:
            if status != 'completed':
                continue
            record = json.loads(result)
            event, analysis = record['event'], record['analysis']
            if (record.get('mode') != 'analysis_only' or analysis.get('stance') != 'exit'
                    or analysis.get('status') != 'ok'):
                continue
            source = decision_source(record)
            if not _jev_exit_verified(record, analysis, source):
                raise ValueError('unverified Jev confidence')
            evidence = record.get('model_evidence') or {}
            provenance = {'decision_source': source, 'decision_source_label': SOURCE_LABELS[source],
                          'api_cost': 'unknown'}
            if source in ('jev_event', 'jev_existing'):
                provenance.update(model_confidence=evidence['confidence'],
                                  sell_confidence=evidence['sell_confidence'])
            symbol, qty = event['symbol'], event['qty']
            started, done = _time(event['observed_at']), _time(record['completed_at'])
            if (event.get('id') != attempt_id or not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol)
                    or type(qty) is not int or qty <= 0 or started is None or done is None
                    or not timedelta(0) <= done - started <= timedelta(seconds=60)):
                raise ValueError('invalid event')
            before, after = _position_at(fills, symbol, started), _position_at(fills, symbol, done)
            if (before is None or before != after or before['qty'] != qty
                    or before['position_id'] != event.get('position_id')
                    or before['latest_fill_id'] != event.get('latest_fill_id')):
                raise ValueError('position changed')
            # The order can only use a quote received after model completion.
            eligible = []
            for row in rows:
                if not isinstance(row, dict) or row.get('symbol') != symbol or row.get('position_id') != before['position_id']:
                    continue
                inputs = row.get('inputs')
                if not isinstance(inputs, dict):
                    continue
                at, evaluated, quote_at = _time(row.get('observed_at')), _time(inputs.get('evaluated_at')), _time(inputs.get('quote_at'))
                if (row.get('mode') == 'observation_only' and row.get('qty') == qty and at and evaluated and quote_at
                        and row.get('state') in ('candidate', 'wait', 'unavailable')
                        and done < quote_at <= evaluated <= at and at - quote_at <= timedelta(seconds=15)
                        and at - done <= timedelta(seconds=60) and _number(inputs.get('bid'))):
                    eligible.append((at, row))
            if not eligible:
                output.append({'event_id': attempt_id, 'symbol': symbol, **provenance,
                               'state': 'quote_unavailable',
                               'costs': 'unknown', 'limitation': 'AI 판단 후 유효한 별도 호가 없음; 주문·체결 미확인'})
                continue
            _, quote = min(eligible, key=lambda item: item[0])
            order_at = _time(quote['observed_at'])
            if _position_at(fills, symbol, order_at) != before:
                raise ValueError('position changed')
            first = {**quote, 'state': 'candidate', 'candidate': True,
                     'inputs': {**quote['inputs'], 'entry_price': before['entry_price']}}
            later = []
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get('inputs'), dict):
                    continue
                at, quote_at = _time(row.get('observed_at')), _time(row['inputs'].get('quote_at'))
                if at and quote_at and at > order_at and timedelta(0) <= at - quote_at <= timedelta(seconds=15):
                    later.append(row)
            simulated = simulate([first] + later, ledger_times)
            matching = next((item for item in simulated if item['symbol'] == symbol
                             and item['position_id'] == before['position_id']), None)
            if matching is None:
                raise ValueError('simulation unavailable')
            output.append({'event_id': attempt_id, **provenance, 'model_completed_at': done.isoformat(),
                           'scenario': 'independent_per_event', **matching})
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
            rejected += 1
    return output, rejected


def replay(observation_file, ledger_file, event_results_file=None):
    try:
        with Path(observation_file).open(encoding='utf-8') as stream:
            rows = [json.loads(line) for line in stream if line.strip()]
        path = Path(ledger_file)
        if not path.is_file():
            raise ValueError('ledger unavailable')
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
            raw_fills = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
        full_fills = [(order_id, symbol, side, qty, price, _time(stamp)) for order_id, symbol, side, qty, price, stamp in raw_fills]
        fills = [(order_id, symbol, side, qty, stamp) for order_id, symbol, side, qty, _, stamp in full_fills]
        if any(not isinstance(symbol, str) or at is None for _, symbol, _, _, at in fills):
            raise ValueError('invalid ledger fill')
        rejected, approved = 0, set()
        for i, row in enumerate(rows):
            if isinstance(row, dict) and row.get('candidate') is True and not _candidate_matches_ledger(row, fills):
                rows[i] = {**row, 'candidate': False, 'state': 'unavailable'}
                rejected += 1
            elif isinstance(row, dict) and row.get('candidate') is True and isinstance(row.get('inputs'), dict):
                approved.add((row.get('symbol'), row.get('position_id'), row['inputs'].get('evaluated_at')))
        ledger_fills = [(symbol, at) for _, symbol, _, _, at in fills]
        comparisons = compare_observations(observation_file, ledger_file)
        comparisons = [item for item in comparisons if (item.get('state') == 'unavailable' and 'symbol' not in item) or
                       (item.get('symbol'), item.get('position_id'), item.get('candidate_at')) in approved]
        result = {'simulation': simulate(rows, ledger_fills), 'engine_comparison': comparisons,
                  'rejected_unverified_candidates': rejected, 'mode': 'offline_simulation_not_broker_fill', 'costs': 'unknown'}
        if event_results_file is not None:
            event_path = Path(event_results_file)
            if not event_path.is_file():
                result.update(event_simulation=[], event_results_status='unavailable')
            else:
                try:
                    with sqlite3.connect(f'file:{event_path}?mode=ro', uri=True, timeout=2) as db:
                        event_results = db.execute('SELECT id,status,result FROM attempts ORDER BY rowid').fetchall()
                    event_sim, event_rejected = simulate_event_results(rows, event_results, full_fills)
                    sources = summarize_event_sources(event_results)
                    result.update(event_simulation=event_sim, rejected_event_results=event_rejected,
                                  event_sources=sources,
                                  event_api_calls={'known_attempts': sum(item['api_calls_known'] for item in sources.values()),
                                      'unknown_attempts': sum(item['api_calls_unknown_attempts'] for item in sources.values()),
                                      'note': 'Jev 호출 시도 기록 기준; 서버 수신·과금 금액은 미확인'},
                                  event_results_status='ok')
                except sqlite3.Error:
                    result.update(event_simulation=[], event_results_status='unavailable')
        return result
    except (OSError, ValueError, TypeError, sqlite3.Error, json.JSONDecodeError):
        return {'simulation': [], 'engine_comparison': [], 'mode': 'unavailable', 'costs': 'unknown'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observations', type=Path)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--event-results', type=Path, help='isolated event-advisor SQLite (read-only)')
    args = parser.parse_args()
    print(json.dumps(replay(args.observations, args.ledger, args.event_results), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
