"""Compare recorded Jev choices with subsequently fetched program flow, without placing orders."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from config_store import write_json
from etf_universe import SYMBOLS, is_code
from fade_shadow import rest_of_position
from jev_program_paper import KST, RESEARCH, Tape, proposed_action, quote_capture, session_bounds
from sqlite_ro import day_rows, ro_connect
from src import file_lock
from trend_exit_shadow import review as review_trend_exit

SOURCE = RESEARCH.parent / 'shared-etf-paper/market-research.sqlite3'
BUCKET_MINUTES, MAX_SYMBOLS = 10, 2


def decisions(path, now):
    with ro_connect(path) as db:
        rows = db.execute("SELECT digest,time,question,inputs,response FROM model_inputs WHERE engine='jev' AND question IN ('candidate','holding') AND time>=? AND time<=? ORDER BY time DESC", ((now - timedelta(minutes=15)).isoformat(), now.isoformat())).fetchall()
        evidence_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_evidence'").fetchone()
        evidence = {d: (t, e) for d, t, e in db.execute(
            "SELECT digest,time,evidence FROM model_evidence WHERE engine='jev' AND question='holding' AND time>=? AND time<=?",
            ((now - timedelta(minutes=15)).isoformat(), now.isoformat()))} if evidence_exists else {}
    selected = {}
    for digest, stamp, question, raw, response in rows:
        try:
            state = json.loads(raw)['state']
            answer = json.loads(response)
            symbol = (state.get('position') or {}).get('symbol') if question == 'holding' else answer.get('choice')
            symbol = SYMBOLS.get(symbol, symbol)
            observed = datetime.fromisoformat(stamp).astimezone(KST)
            try:
                requested = datetime.fromisoformat(state.get('news_checked_at', stamp))
                if not timedelta(0) <= observed - requested <= timedelta(minutes=2):
                    requested = observed
            except (TypeError, ValueError):
                requested = observed
            requested = requested.astimezone(KST)
            if not is_code(symbol) or not symbol.isdecimal() or requested.date() != now.date():
                continue
            item = {'digest': digest, 'recorded_at': stamp, 'at': requested.isoformat(),
                    'question': question, 'symbol': symbol, 'choice': answer.get('choice')}
            if question == 'holding':
                item['position'] = {key: state['position'].get(key) for key in
                                    ('entry_average', 'bid', 'peak_bid', 'exit_profile', 'half_already_sold', 'quantity')}
                item['reviewable_at'] = stamp
                if state.get('recent_news_headlines') and digest in evidence:
                    evidence_at, raw_evidence = evidence[digest]
                    try:
                        available = datetime.fromisoformat(evidence_at)
                        if observed <= available <= min(now, requested + timedelta(minutes=2)):
                            news = (json.loads(raw_evidence).get('why_news') or {}).get('choice')
                            if news in ('UP', 'DOWN', 'MIXED'):
                                item.update(market_news=news, reviewable_at=available.isoformat())
                    except (TypeError, ValueError, AttributeError):
                        pass
            selected.setdefault((question, symbol), item)
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(selected.values(), key=lambda x: (x['question'] == 'holding', x['recorded_at']), reverse=True)


def exit_outcome(folder, pick, now):
    """Compare one subsequent observed bid with verified fills for the same remainder."""
    at = datetime.fromisoformat(pick['reviewable_at'])
    qty = pick['position']['quantity']
    code = pick['symbol']
    if type(qty) is not int or qty <= 0:
        return {'status': 'quantity_unknown'}
    with ro_connect(folder / 'orders.sqlite3') as db:
        fills = [(side, n, p, datetime.fromisoformat(t)) for side, n, p, t in db.execute(
            'SELECT side,quantity,price,time FROM fills WHERE symbol=? AND time<=? ORDER BY time,rowid', (code, now.isoformat()))]
    if sum(n if side == 'BUY' else -n for side, n, _, t in fills if t <= at) != qty:
        return {'status': 'quantity_changed'}
    _, actual = rest_of_position(fills, at, qty)
    first_fill = next((t for _, _, _, t in fills if t > at), None)
    until = min(now, at + timedelta(minutes=2))
    with ro_connect(folder / 'trading-ledger.sqlite3') as db:
        rows = db.execute("SELECT time,json_extract(data,'$.bid') FROM events_v2 WHERE event='holding_quote' "
                          "AND time>? AND time<=? AND json_extract(data,'$.symbol')=? AND json_extract(data,'$.qty')=? "
                          "AND json_extract(data,'$.bid')>0 ORDER BY time LIMIT 1", (at.isoformat(), until.isoformat(), code, qty))
        quote = rows.fetchone()
    if not quote or (first_fill and datetime.fromisoformat(quote[0]) >= first_fill):
        return {'status': 'no_comparable_next_bid'}
    return {'status': 'compared' if actual is not None else 'awaiting_confirmed_exit', 'next_bid_at': quote[0],
            'next_bid': quote[1], 'actual_remaining_avg_price': actual,
            'gross_difference_krw': round((quote[1] - actual) * qty, 2) if actual is not None else None}


def run(now=None, source=SOURCE, root=RESEARCH, fetch_program=None, fetch_bars=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    start, end = session_bounds(now.date())
    if not start + timedelta(minutes=BUCKET_MINUTES) <= now < end:
        return {'status': 'outside_session'}
    starts, _ = day_rows(Path(source).with_name('trading-day-start.jsonl'), now.date().isoformat())
    if now.weekday() >= 5 or not starts or starts[-1].get('note') != 'trading_day':
        return {'status': 'closed_or_unknown'}
    import kiwoom_data
    fetch_program = fetch_program or kiwoom_data.stock_program_rows
    fetch_bars = fetch_bars or kiwoom_data.day_bars
    folder = Path(root) / 'audit' / now.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    bucket = now.replace(minute=now.minute // BUCKET_MINUTES * BUCKET_MINUTES, second=0, microsecond=0)
    path = folder / f'{bucket:%H%M}.json'
    with (folder / '.lock').open('a') as lock:
        file_lock.lock(lock)
        if path.exists():
            return json.loads(path.read_text())
        result = {'status': 'ok', 'time': now.isoformat(), 'comparison': 'retrospective_download', 'path': str(path),
                  'decisions': [], 'captures': {}}
        try:
            picks = decisions(source, now)
            codes = list(dict.fromkeys(item['symbol'] for item in picks))[:MAX_SYMBOLS]
            books, _ = (day_rows(Path(source).with_name('holding-book.jsonl'), now.date().isoformat())
                        if any(p.get('position', {}).get('half_already_sold') is True for p in picks) else ([], 0))
            if not picks:
                result['status'] = 'awaiting_jev_sample'
            for code in codes:
                result['captures'][code] = capture = {}
                try:
                    rows = fetch_program(code, now.date(), now=now)
                    capture.update(quote_capture(code, now, rows, []), program_rows=len(rows))
                    bars = fetch_bars(code, now.date(), now=now)['bars']
                    capture.update(quote_capture(code, now, rows, bars))
                    tape = Tape(now.date(), rows, bars)
                    capture.update(valid_program_rows=len(tape.points), valid_bars=len(tape.bars))
                    if code in SYMBOLS.values():
                        try:
                            etf_rows = kiwoom_data.call('ka40010', {'stk_cd': code}, 'etftisl_trnsn', max_pages=1, timeout=2)
                            capture['etf_flow'] = {'source': 'ka40010', 'received_at': datetime.now(KST).isoformat(),
                                                   'rows': etf_rows, 'status': 'observed' if etf_rows else 'empty'}
                        except Exception as exc:
                            capture['etf_flow'] = {'source': 'ka40010', 'status': 'read_failed',
                                                   'error_type': type(exc).__name__}
                    for pick in (item for item in picks if item['symbol'] == code):
                        at = datetime.fromisoformat(pick['at'])
                        signal = tape.signal(at)
                        if not rows:
                            signal = {'status': 'program_source_empty'}
                        held = pick['question'] == 'holding'
                        action = proposed_action(signal, held)
                        disagreement = action == 'defer_entry' or (action == 'review_exit' and pick['choice'] == 'HOLD')
                        comparison = {**pick, 'signal': signal, 'proposed_action': action, 'disagreement': disagreement}
                        if held:
                            lo = (at - timedelta(seconds=15)).isoformat()
                            book = next((row for row in reversed(books or []) if row.get('symbol') == code
                                         and lo <= row['time'] <= pick['at']), None)
                            comparison['trend_exit_shadow'] = review_trend_exit(pick['position'], tape, at, signal, book,
                                                                                pick.get('market_news'))
                            if comparison['trend_exit_shadow']['action'].startswith('review_exit_'):
                                try:
                                    comparison['trend_exit_outcome'] = exit_outcome(Path(source).parent, pick, now)
                                except (sqlite3.Error, TypeError, ValueError):
                                    comparison['trend_exit_outcome'] = {'status': 'ledger_unavailable'}
                        result['decisions'].append(comparison)
                except Exception as exc:
                    capture.update(status='read_failed', error_type=type(exc).__name__)
                    result['status'] = 'partial'
        except Exception as exc:
            result.update(status='source_failed', error_type=type(exc).__name__)
        result['finished_at'] = datetime.now(KST).isoformat()
        write_json(path, result, indent=None)
        return result


if __name__ == '__main__':
    result = run()
    result['captures'] = {code: {k: v for k, v in capture.items() if k not in ('program', 'bars')}
                          for code, capture in result.get('captures', {}).items()}
    result['signal_statuses'] = Counter(item['signal']['status'] for item in result.get('decisions', []))
    print(json.dumps(result, ensure_ascii=False))
