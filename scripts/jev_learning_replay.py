"""Build a Jev judgment, execution and learning report from a closed backup."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache, partial
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS, is_code
from jev_entry_study import DATABASES, atomic_write, immutable, output_folder, snapshot_folder
from position_paths import close_after, close_at, pct, positions

KST = ZoneInfo('Asia/Seoul')
EVENTS = ('jev_shadow', 'jev_holding_decision', 'jev_error', 'market_context',
          'watchlist_strength', 'strength_entry_review_trial', 'v2_entry_gate', 'v2_execution_gate',
          'submitted', 'sell_submitted')


def normalized_time(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    return stamp.astimezone(KST) if stamp.utcoffset() is not None else None


def stock_code(value):
    return SYMBOLS.get(value, value) if isinstance(value, str) else None


def index_name(value):
    value = str(value or '').upper()
    if value == '코스닥' or value.startswith('KOSDAQ'):
        return 'KOSDAQ'
    if value == '코스피' or value.startswith('KOSPI'):
        return 'KOSPI'
    return None


def candidate_market(symbol, context, archive):
    state = archive.get('state') or {}
    details = (state.get('watchlist_details') or {}).get(symbol) or {}
    market = index_name((context.get(symbol) or {}).get('index') or details.get('index'))
    criteria = (archive.get('criteria') or {}).get(symbol)
    match = re.search(r'개별주식\((KOSPI(?:200)?|KOSDAQ(?:150)?)\)', criteria) if isinstance(criteria, str) else None
    return market or (index_name(match[1]) if match else None)


def ranked_symbols(probabilities):
    if not isinstance(probabilities, dict):
        return []
    ranked = sorted(((-value, symbol) for name, value in probabilities.items()
                     if is_code(symbol := stock_code(name)) and type(value) in (int, float) and 0 <= value <= 1))
    return list(dict.fromkeys(symbol for _, symbol in ranked))[:2]


def attach_price_observations(decisions, bars_for, as_of):
    from src.tools.nhplug_calendar import shifted_time
    from datetime import time

    days = {}
    tolerance = timedelta(minutes=2)
    for rows in decisions.values():
        for row in rows:
            at = normalized_time(row.get('response_time')) or normalized_time(row['time'])
            if at is None or at > as_of:
                continue
            day = at.date().isoformat()
            if day not in days:
                days[day] = bars_for(day)
            session_end = datetime.combine(at.date(), shifted_time(time(15, 30), at.date()), KST)
            symbols = [row.get('symbol') or row.get('choice'), *(item['symbol'] for item in row.get('strength', [])),
                       *row.get('ranked_symbols', [])]
            observed = row['price_observations'] = []
            for symbol in list(dict.fromkeys(code for code in symbols if is_code(code)))[:3]:
                series = days[day].get(symbol, [])
                base = close_at(series, at, observed_only=True, max_age=tolerance)
                item = {'symbol': symbol, 'label_type': 'price_observation', 'base_price': base}
                for minutes in (5, 15, 30):
                    target = at + timedelta(minutes=minutes)
                    latest = min(session_end, as_of)
                    item[f'r{minutes}_pct'] = (pct(close_after(series, target, min(tolerance, latest - target),
                                                              observed_only=True), base)
                                               if base is not None and target <= latest else None)
                item['close_pct'] = (pct(close_after(series, session_end, timedelta(0), observed_only=True), base)
                                     if base is not None and at < session_end <= as_of else None)
                observed.append(item)


def strength_policies(events, context, submissions, row, archive, evidence_for):
    from strength_entry_review import compare

    state = archive.get('state') or {}
    details = state.get('watchlist_details') or {}
    selected = {item['symbol']: item for item in events
                if ((item['event'] == 'watchlist_strength' or item['event'] == 'strength_entry_review_trial')
                    and is_code(item.get('symbol')))}
    sent = {item['symbol']: item for item in submissions if item.get('source') == 'watchlist_strength'}
    response_at = normalized_time(row['response_time'])
    results = []
    for symbol, pick in (selected | sent).items():
        submission = sent.get(symbol)
        timing = (pick.get('entry_timing') or {}).copy()
        if 'open_gap_pct' in pick:
            timing['open_gap_pct'] = pick['open_gap_pct']
        gate_at = normalized_time(timing.get('gate_time')) or normalized_time(pick['time'])
        if gate_at is None:
            continue
        market = candidate_market(symbol, context, archive)
        index = {'KOSPI': '코스피', 'KOSDAQ': '코스닥'}.get(market)
        baseline = True if submission else False if any(
            gate.get('permitted') is False and stock_code(gate.get('symbol') or gate.get('choice')) == symbol
            for gate in row['gates']) else None
        evidence = evidence_for(symbol, gate_at) if evidence_for else {}
        results.append({'symbol': symbol, 'market': market, 'at': gate_at.isoformat(), 'evidence': evidence, 'policies': compare(
            baseline=baseline, pressure=evidence.get('pressure'),
            symbol=symbol, model_choice=row['choice'], model_fresh=bool(row['input_linked'] and response_at
            and timedelta(0) <= gate_at - response_at <= timedelta(minutes=1)),
            market=state.get('market_regime_themes_global'), index=index,
            market_news=state.get('recent_news_headlines'), stock=details.get(symbol), timing=timing, now=gate_at)})
    return results


def cycle_reports(ledger, start, end, as_of):
    marks = ','.join('?' for _ in EVENTS)
    query = f'SELECT digest,time,event,data FROM events_v2 WHERE event IN ({marks}) AND time>=? AND time<? ORDER BY time,rowid'
    seen, cycles = set(), defaultdict(list)
    for digest, stamp, kind, raw in ledger.execute(query, (*EVENTS, start.isoformat(), end.isoformat())):
        at = normalized_time(stamp)
        if digest in seen or at is None or at > as_of:
            continue
        seen.add(digest)
        data = json.loads(raw)
        if cycle := data.get('cycle_id'):
            cycles[(at.date().isoformat(), cycle)].append({**data, 'time': stamp, 'event': kind})
    return cycles


def read_fills(orders, as_of):
    query = ('SELECT f.order_id,f.symbol,f.side,f.quantity,f.price,f.time,l.cycle_id FROM fills f '
             'LEFT JOIN decision_order_links l ON l.order_id=f.order_id WHERE f.time<=? ORDER BY f.time,f.rowid')
    by_cycle, by_symbol = defaultdict(list), defaultdict(list)
    invalid = set()
    for order_id, symbol, side, quantity, price, stamp, cycle in orders.execute(query, (as_of.isoformat(),)):
        at = normalized_time(stamp)
        if (at is None or at > as_of or not is_code(symbol) or side not in ('BUY', 'SELL')
                or type(quantity) is not int or quantity <= 0 or type(price) not in (int, float)
                or not math.isfinite(price) or price <= 0):
            invalid.add(symbol)
            continue
        row = {'order_id': order_id, 'symbol': symbol, 'side': side, 'quantity': quantity, 'price': price,
               'time': at.isoformat(), 'cycle_id': cycle}
        by_symbol[symbol].append(row)
        if cycle:
            by_cycle[(at.date().isoformat(), cycle)].append(row)
    return by_cycle, by_symbol, invalid


def stored_evidence(path, symbol, at, *, sell_share=None, minimum_krw=None):
    from jev_candidate_evidence import flow_at, program_at

    day = at.date().isoformat()
    flow = flow_at(path, day, symbol, at)
    program = program_at(path, day, symbol, at)
    window = flow['windows'][3]
    pressure = None
    if sell_share is not None and window['status'] == 'ready':
        total = window['buy_krw'] + window['sell_krw']
        if total >= minimum_krw:
            pressure = {'symbol': symbol, 'source': 'krx_trade_flow_3m',
                        'source_at': at.replace(second=0, microsecond=0).isoformat(), 'received_at': at.isoformat(),
                        'selling': window['sell_krw'] / total >= sell_share}
    return {'flow': flow, 'program': program, 'pressure': pressure}


def outcome_samples(fills, invalid, opening, start, end, as_of, cost_bps):
    samples, audited = [], []
    for symbol, rows in fills.items():
        in_scope = [r for r in rows if start <= r['time'][:10] <= end]
        if not in_scope:
            continue
        if symbol in invalid:
            audited.append({'symbol': symbol, 'status': 'invalid_fill'})
            continue
        balance = 0
        for row in rows:
            balance += row['quantity'] if row['side'] == 'BUY' else -row['quantity']
            if balance < 0:
                audited.append({'symbol': symbol, 'status': 'unmatched_sell'})
                break
        else:
            for book in positions((symbol, r['side'], r['quantity'], r['price'], r['time']) for r in rows):
                day, stamp = book['opened'].date().isoformat(), book['opened'].isoformat()
                if not start <= day <= end:
                    continue
                matches = opening.get((symbol, stamp), [])
                closed = book.get('closed')
                audit = {'symbol': symbol, 'opened': stamp, 'closed': closed.isoformat() if closed else None}
                if len(matches) != 1:
                    audit['status'] = 'opening_link_ambiguous' if matches else 'opening_link_missing'
                elif not closed:
                    audit['status'] = 'position_open'
                elif not matches[0][2]:
                    audit['status'] = 'market_unknown'
                else:
                    audit['status'] = 'eligible'
                audited.append(audit)
                if audit['status'] != 'eligible':
                    continue
                order_id, route, market = matches[0]
                cost = (book['spent'] + book['proceeds']) * (cost_bps or 0) / 10_000
                samples.append({'sample_id': order_id, 'position_id': order_id, 'purpose': 'candidate',
                                'market': market, 'route': route, 'symbol': symbol, 'day': day,
                                'label_type': 'paper_fill', 'cost_basis': 'included' if cost_bps is not None else 'excluded',
                                'return_pct': round(100 * (book['realized'] - cost) / book['spent'], 6),
                                'outcome_available_at': book['closed'].isoformat(), 'observed_through': as_of.isoformat()})
    return samples, audited


def linked_input(inputs, purpose, item):
    digest = item.get('input_digest' if purpose == 'holding' else 'jev_input_digest')
    archive = inputs.get((purpose, digest))
    if archive is None:
        return digest, 'missing', {}
    if archive[0] > normalized_time(item['time']):
        return digest, 'recorded_after_decision', {}
    return digest, 'linked', archive[1]


def decisions_for_cycle(events, inputs, fills, opening, evidence_for):
    decisions = []
    context = next((item.get('candidates') or {} for item in events if item['event'] == 'market_context'
                    and item.get('context') == 'entry'), {})
    submissions = [item for item in events if item['event'] == 'submitted']
    gates = [{key: item[key] for key in ('time', 'permitted', 'reason', 'choice', 'symbol', 'source', 'strength_reasons')
              if key in item} for item in events if item['event'] in ('v2_entry_gate', 'v2_execution_gate')]
    for item in events:
        kind = item['event']
        if kind not in ('jev_shadow', 'jev_holding_decision', 'jev_error'):
            continue
        purpose = 'holding' if kind == 'jev_holding_decision' else 'candidate'
        digest, input_status, archive = linked_input(inputs, purpose, item)
        state = archive.get('state') or {}
        choice = item.get('choice' if purpose == 'holding' else 'jev_choice')
        row = {'time': item['time'], 'cycle_id': item['cycle_id'], 'input_digest': digest,
               'input_linked': input_status == 'linked', 'input_status': input_status,
               'choice': stock_code(choice), 'symbol': item.get('symbol'), 'model': item.get('model', item.get('jev_model')),
               'response_time': item.get('model_response_time', item.get('jev_response_time')), 'event': kind,
               'confidence': item.get('confidence' if purpose == 'holding' else 'jev_confidence'),
               'source_error': item.get('error_type'), 'experience_version': (state.get('jev_experience') or {}).get('version'),
               'candidates': list(archive.get('criteria') or item.get('jev_probabilities') or {}),
               'ranked_symbols': ranked_symbols(item.get('jev_probabilities')),
               'gates': gates if purpose == 'candidate' else []}
        row['orders'] = ([f for f in fills if f['symbol'] == item.get('symbol') and f['side'] == 'SELL'] if purpose == 'holding'
                         else [f for f in fills if f['side'] == 'BUY'])
        if purpose == 'candidate':
            row['submissions'] = [{key: s.get(key) for key in ('symbol', 'source', 'time', 'qty')} for s in submissions]
            row['strength'] = strength_policies(events, context, submissions, row, archive, evidence_for)
        decisions.append((purpose, row))

    entry_archives = [linked_input(inputs, 'candidate', item)[2] for item in events if item['event'] == 'jev_shadow']
    for fill in fills:
        if fill['side'] != 'BUY':
            continue
        symbol = fill['symbol']
        matched = [s for s in submissions if s.get('symbol') == symbol]
        if len(matched) != 1:
            continue
        route = matched[0].get('source')
        market = next((name for archive in entry_archives or [{}]
                       if (name := candidate_market(symbol, context, archive))), None)
        if route and route != 'manual':
            opening[(symbol, fill['time'])].append((fill['order_id'], route, market))
    return decisions


def collect(ledger, research, orders, *, start, end, as_of, cost_bps=None, evidence_for=None, bars_for=None):
    if (as_of.utcoffset() is None or end < start or end == date.max or end > as_of.astimezone(KST).date()
            or cost_bps is not None and (not math.isfinite(cost_bps) or not 0 <= cost_bps <= 1_000)):
        raise ValueError('유효한 기준 시각, 날짜 범위와 비용 가정이 필요합니다')
    as_of = as_of.astimezone(KST)
    stop = end + timedelta(days=1)
    counts, decisions, opening = Counter(), defaultdict(list), defaultdict(list)
    inputs = {}
    model_query = ("SELECT digest,time,question,inputs,response FROM model_inputs WHERE engine='jev' AND time>=? AND time<? "
                   "AND question IN ('candidate','holding','watchlist') ORDER BY time,rowid")
    for digest, stamp, purpose, raw, answer in research.execute(model_query, (start.isoformat(), stop.isoformat())):
        at = normalized_time(stamp)
        if at is None or at > as_of:
            continue
        archive = json.loads(raw)
        inputs[(purpose, digest)] = (at, archive)
        if purpose == 'watchlist':
            answer = json.loads(answer)
            decisions[purpose].append({'time': stamp, 'input_digest': digest, 'input_linked': True,
                                       'model': archive.get('model'), 'choice': stock_code(answer.get('choice')),
                                       'ranked_symbols': ranked_symbols(answer.get('probabilities')),
                                       'candidates': list(archive.get('criteria') or {})})
    cycles = cycle_reports(ledger, start, stop, as_of)
    by_cycle, by_symbol, invalid = read_fills(orders, as_of)
    for identity, events in cycles.items():
        fills = by_cycle.get(identity, [])
        for purpose, row in decisions_for_cycle(events, inputs, fills, opening, evidence_for):
            decisions[purpose].append(row)
        counts['submitted'] += sum(item['event'] == 'submitted' for item in events)
    for purpose in ('watchlist', 'candidate', 'holding'):
        rows = decisions[purpose]
        counts[purpose] = sum(not row.get('source_error') for row in rows)
        counts[f'{purpose}_missing_input'] = sum(not row['input_linked'] for row in rows)
    counts['wait'] = sum(row['choice'] == 'WAIT' for row in decisions['candidate'])
    counts['invalid_fill_symbols'] = len(invalid)
    policy_counts = {key: Counter() for key in ('current', 'strict', 'conditional')}
    for row in decisions['candidate']:
        for candidate in row['strength']:
            for policy, result in candidate['policies'].items():
                policy_counts[policy][result['status']] += 1
    if bars_for:
        attach_price_observations(decisions, bars_for, as_of)
    samples, positions_audit = outcome_samples(by_symbol, invalid, opening, start.isoformat(), end.isoformat(), as_of, cost_bps)
    return {'as_of': as_of.isoformat(), 'cost_bps_each_side': cost_bps, 'counts': dict(counts), 'policy_counts': policy_counts,
            'decisions': dict(decisions), 'samples': samples, 'positions': positions_audit,
            'position_status': dict(Counter(item['status'] for item in positions_audit))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--from', dest='start', required=True, type=date.fromisoformat)
    parser.add_argument('--to', dest='end', required=True, type=date.fromisoformat)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--cost-bps', type=float, help='매수·매도 각각의 비용 가정. 생략하면 비용 미반영으로 기록')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--evidence-db', type=Path)
    parser.add_argument('--sell-share', type=float, help='3분 매도대금 비중 기준. 격리 연구에서 고정한 값')
    parser.add_argument('--minimum-turnover', type=int, help='같은 3분 구간의 최소 거래대금 원 단위')
    parser.add_argument('--eligible-from', help='경험 요약을 격리 비교에 제공할 최초 시각')
    parser.add_argument('--expires-at', help='격리 경험 요약 만료 시각')
    args = parser.parse_args()
    as_of = normalized_time(args.as_of)
    if as_of is None:
        parser.error('--as-of에는 시간대가 필요합니다')
    eligible, expires = normalized_time(args.eligible_from), normalized_time(args.expires_at)
    if (args.eligible_from or args.expires_at) and not (eligible and expires):
        parser.error('경험 게시에는 시간대를 포함한 적용·만료 시각이 모두 필요합니다')
    if args.sell_share is not None or args.minimum_turnover is not None:
        if not (args.evidence_db and args.sell_share is not None and 0.5 < args.sell_share <= 1
                and args.minimum_turnover is not None and args.minimum_turnover > 0):
            parser.error('매도 압력 기준에는 증거 DB, 매도 비중과 양의 최소 거래대금이 필요합니다')
    try:
        snapshot = snapshot_folder(args.snapshot)
        output = output_folder(args.output, snapshot)
        evidence_for = None
        if args.evidence_db:
            evidence = args.evidence_db.expanduser().resolve(strict=True)
            evidence_for = lru_cache(maxsize=64)(partial(stored_evidence, evidence, sell_share=args.sell_share,
                                                       minimum_krw=args.minimum_turnover))
        with immutable(snapshot / DATABASES[0]) as ledger, immutable(snapshot / DATABASES[1]) as research, \
                immutable(snapshot / DATABASES[2]) as orders:
            from position_paths import day_bars_from_connection

            report = collect(ledger, research, orders, start=args.start, end=args.end, as_of=as_of,
                             cost_bps=args.cost_bps, evidence_for=evidence_for,
                             bars_for=partial(day_bars_from_connection, ledger))
        version = None
        if eligible and expires:
            from jev_experience import build, publish

            if eligible.date() <= args.end:
                raise ValueError('경험 적용 시각은 관찰 범위의 마지막 날짜 다음이어야 합니다')
            artifact = build(report['samples'], published_at=datetime.now(KST), eligible_from=eligible, expires_at=expires)
            if artifact['lessons']:
                version = publish(artifact, output / 'experience').stem
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(output / 'jev-learning-replay.json', report)
        print(json.dumps({'counts': report['counts'], 'samples': len(report['samples']),
                          'policy_counts': report['policy_counts'], 'experience_version': version}, ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f'복기 실패: {exc}\n')


if __name__ == '__main__':
    main()
