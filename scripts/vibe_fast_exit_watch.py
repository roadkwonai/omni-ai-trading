"""독립 가상 청산 후보 관찰기. 읽기 전용이며 주문·실행기 원장을 수정하지 않는다."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
POLL_INTERVAL = 5  # filesystem metadata only; no NH calls
MAX_STATE_AGE = timedelta(minutes=3)


def _json(path, missing=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return missing


def _time(value):
    try:
        at = datetime.fromisoformat(value)
        return at.astimezone(KST) if at.tzinfo is not None and at.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _open_cycle(db, symbol, qty):
    """Return all order/cycle links and the opening time for this verified open position."""
    rows = db.execute('SELECT f.side,f.quantity,f.time,l.cycle_id,f.order_id FROM fills f '
                      'LEFT JOIN decision_order_links l ON l.order_id=f.order_id '
                      'WHERE f.symbol=? ORDER BY f.rowid DESC', (symbol,)).fetchall()
    remaining, buys, opened, opening_order = qty, [], None, None
    for side, amount, stamp, cycle, order_id in rows:
        at = _time(stamp)
        if type(amount) is not int or amount <= 0 or side not in ('BUY', 'SELL') or at is None:
            return None
        remaining += -amount if side == 'BUY' else amount
        if side == 'BUY':
            if not isinstance(cycle, str) or not cycle:
                return None
            buys.append(cycle)
            opened, opening_order = at, order_id
        if remaining == 0:
            if not isinstance(opening_order, str) or not opening_order:
                return None
            generation = hashlib.sha256(f'{symbol}\0{opening_order}'.encode()).hexdigest()[:20]
            return opened, buys, generation, rows[0][4], _time(rows[0][2])
        if remaining < 0:
            return None
    return None


def _sources(root, pending):
    """Resolve all open-cycle sources in one streaming pass; conflicting sources fail closed."""
    expected = {(code, cid) for code, cycles in pending.items() for cid in cycles}
    if not expected:
        return set()
    path = Path(root) / 'events.jsonl'
    if not path.is_file():
        return set()
    found, conflicts = {}, set()
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if isinstance(row, dict) and row.get('event') == 'submitted':
                key = (row.get('symbol'), row.get('cycle_id'))
                if key in expected:
                    source = row.get('source')
                    if key in found and found[key] != source:
                        conflicts.add(key)
                    found[key] = source
    allowed = {'vibe', 'vibe-trading', 'split'}
    return {code for code, cycles in pending.items()
            if all((code, c) not in conflicts and found.get((code, c)) in allowed for c in cycles)
            and any(found.get((code, c)) in {'vibe', 'vibe-trading'} for c in cycles)}


def positions(root, now):
    """Cross-check fresh broker snapshot and verified fills; no database writes."""
    from nh_paper_isolated import owned_positions
    root = Path(root)
    state = _json(root / 'account-state.json')
    if not isinstance(state, dict) or not isinstance(state.get('holdings'), dict):
        raise ValueError('account_state_unavailable')
    observed = _time(state.get('time'))
    if observed is None or observed.date() != now.astimezone(KST).date() or not timedelta(0) <= now - observed <= MAX_STATE_AGE:
        raise ValueError('account_state_stale')
    path = root / 'orders.sqlite3'
    if not path.is_file():
        raise ValueError('ledger_unavailable')
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
        db.execute('BEGIN')
        owned = owned_positions(db)
        pending = db.execute("SELECT COUNT(*) FROM intents WHERE status != 'terminal'").fetchone()[0]
        if pending:
            raise ValueError('pending_order')
        if set(state['holdings']) != set(owned):
            raise ValueError('holdings_mismatch')
        requests = _json(root / 'exit-requests.json', [])
        plans = _json(root / 'exit-plans.json', {})
        oversold = _json(root / 'oversold-plans.json', None)
        if (not isinstance(requests, (list, dict)) or any(not isinstance(c, str) or not re.fullmatch(r'\d{6}', c) for c in requests)
                or not isinstance(plans, dict) or (oversold is not None and not isinstance(oversold, dict))):
            raise ValueError('position_plan_unavailable')
        out, cycles = [], {}
        for code, qty in sorted(owned.items()):
            row = state['holdings'].get(code)
            if not re.fullmatch(r'\d{6}', code) or not isinstance(row, dict) or row.get('rsdl_qty') != qty:
                raise ValueError('holdings_mismatch')
            avg = row.get('phs_pr')
            if type(avg) not in (int, float) or not 0 < avg < float('inf'):
                raise ValueError('average_price_unavailable')
            if code in requests or (code in plans and (not isinstance(plans[code], dict) or plans[code].get('exiting'))):
                continue
            if oversold is not None and code in oversold:
                continue
            position = _open_cycle(db, code, qty)
            if position is None or position[4] is None or position[4] > observed:
                continue  # the account snapshot predates this fill or provenance is unknown
            cycles[code] = position[1]
            out.append({'symbol': code, 'qty': qty, 'entry_at': position[0], 'entry_price': avg,
                        'position_id': position[2], 'latest_fill_id': position[3]})
    verified = _sources(root, cycles)
    return [item for item in out if item['symbol'] in verified]


def _position_current(root, position, now):
    """Recheck just the observed holding and pending status without scanning events again."""
    root, code = Path(root), position['symbol']
    state = _json(root / 'account-state.json')
    at = _time(state.get('time')) if isinstance(state, dict) else None
    if at is None or at.date() != now.date() or not timedelta(0) <= now - at <= MAX_STATE_AGE:
        return False
    holdings = state.get('holdings')
    row = holdings.get(code) if isinstance(holdings, dict) else None
    if not isinstance(row, dict) or row.get('rsdl_qty') != position['qty'] or row.get('phs_pr') != position['entry_price']:
        return False
    requests = _json(root / 'exit-requests.json', [])
    plans = _json(root / 'exit-plans.json', {})
    oversold = _json(root / 'oversold-plans.json', None)
    if (not isinstance(requests, (list, dict)) or not isinstance(plans, dict) or (oversold is not None and not isinstance(oversold, dict))
            or code in requests or (code in plans and (not isinstance(plans[code], dict) or plans[code].get('exiting')))
            or (oversold is not None and code in oversold)):
        return False
    with sqlite3.connect(f"file:{root / 'orders.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
        db.execute('BEGIN')
        pending = db.execute("SELECT COUNT(*) FROM intents WHERE status != 'terminal'").fetchone()[0]
        latest = db.execute('SELECT order_id,time FROM fills WHERE symbol=? ORDER BY rowid DESC LIMIT 1', (code,)).fetchone()
        signed, invalid = db.execute("SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN quantity WHEN side='SELL' THEN -quantity ELSE 0 END),0), SUM(CASE WHEN side NOT IN ('BUY','SELL') OR quantity <= 0 OR quantity IS NULL THEN 1 ELSE 0 END) FROM fills WHERE symbol=?", (code,)).fetchone()
        stamp = _time(latest[1]) if latest else None
        return pending == 0 and not invalid and latest is not None and latest[0] == position['latest_fill_id'] and stamp is not None and stamp <= at and signed == position['qty']


def _existing_exit(root, position, quote, observed, advice):
    """Recheck the ordinary runner's stop, partial target, flatten and model sell."""
    from nh_paper_isolated import market_view, position_since_open, stop_symbol_index, take_profit_target
    from strategy_settings import read_settings
    from session_times import load
    shared = Path(root).parent / 'shared-etf-paper'
    settings, status = read_settings(shared)
    if status == 'invalid' or not isinstance(advice, dict) or not isinstance(advice.get('decisions'), list):
        return None
    bid = quote.get('bidp') if isinstance(quote, dict) else None
    if type(bid) is not int or bid <= 0:
        return None
    symbol, qty = position['symbol'], position['qty']
    avg = Decimal(str(position['entry_price']))
    import engine_exits  # the same levels as the vibe runner it watches
    lv = engine_exits.levels(engine_exits.defensive_plan(settings), market_view(shared), stop_symbol_index(shared, symbol), symbol)
    if engine_exits.stop_hit(avg, bid, lv) or engine_exits.target_hit(avg, bid, lv, 'all'):
        return True
    if observed.strftime('%H:%M') >= load(shared)['flatten']:
        return True
    if qty >= 2 and engine_exits.target_hit(avg, bid, lv, 'half'):
        with sqlite3.connect(f"file:{Path(root) / 'orders.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
            history = position_since_open(db, symbol, qty)
        if take_profit_target(history) > history['sold']:
            return True
    return any(isinstance(d, dict) and d.get('action') == 'SELL' and d.get('symbol') == symbol
               for d in advice['decisions'])


def _model_position_matches(root, advice_root, symbol, advice):
    from vibe_runner import model_position_check
    if advice is None:
        return False
    with sqlite3.connect(f"file:{Path(root) / 'orders.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
        return model_position_check(db, advice_root, symbol, advice)


def _context_status(root, advice, observed, symbol):
    """Read source status from the exact persisted model input, never infer missing news."""
    unavailable = {'status': 'unavailable', 'observed_at': None}
    result = {'market': dict(unavailable), 'news': dict(unavailable)}
    try:
        digest = advice.get('input_digest') if isinstance(advice, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            return result
        path = Path(root).parent / 'shared-etf-paper' / 'market-research.sqlite3'
        if not path.is_file():
            return result
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
            row = db.execute("SELECT inputs FROM model_inputs WHERE digest=? AND engine='vibe' AND question='scores'", (digest,)).fetchone()
        context = json.loads(row[0])['context'] if row else None
        if not isinstance(context, dict):
            return result
        captured = _time(context.get('time'))
        if captured is None or not timedelta(0) <= observed - captured <= timedelta(minutes=5):
            return result
        from nh_paper_isolated import stop_symbol_index
        market = context.get('market_regime_themes_global')
        index = stop_symbol_index(Path(root).parent / 'shared-etf-paper', symbol)
        relevant = market.get(index) if isinstance(market, dict) and index else None
        if (isinstance(relevant, dict) and relevant.get('regime') in ('상승장', '횡보장', '하락장')
                and isinstance(market.get('as_of'), str)):
            # market_context.summary serializes its Korean observation time at minute precision.
            market_at = datetime.strptime(market['as_of'], '%Y-%m-%dT%H:%M').replace(tzinfo=KST)
            if timedelta(0) <= captured - market_at <= timedelta(minutes=20):
                result['market'] = {'status': 'ok', 'observed_at': market_at.isoformat()}
        news = context.get('market_news')
        if isinstance(news, list) and news:
            published = [_time(item.get('published')) for item in news if isinstance(item, dict)]
            if published and all(stamp is not None and timedelta(0) <= captured - stamp <= timedelta(hours=24) for stamp in published):
                result['news'] = {'status': 'ok', 'observed_at': captured.isoformat()}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, json.JSONDecodeError):
        pass
    return result


def _liquidity(quote, symbol, observed):
    """Accept first bid depth only when price, symbol, session and quote time agree."""
    try:
        if quote.get('iem_cd') != symbol or quote.get('market_status') != '1':
            return None
        bid, level, size = quote.get('bidp'), quote.get('bidp1'), quote.get('bidp_rsqn1')
        at = datetime.strptime(observed.strftime('%Y%m%d') + quote['hoga_bsop_hour'], '%Y%m%d%H:%M:%S').replace(tzinfo=KST)
        if (type(bid) is not int or type(level) is not int or bid <= 0 or level != bid
                or type(size) is not int or size <= 0 or not timedelta(0) <= observed - at <= timedelta(seconds=15)):
            return None
        return {'bid': bid, 'bid_size': size, 'bid_size_verified': True, 'quote_at': at.isoformat()}
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def _snapshot_quote(root, symbol, now):
    """Consume only the runner's already-verified quote; never call a broker."""
    from kr_swing_v5_forward import fresh_quote
    record = _json(Path(root) / 'fast-exit-quotes' / f'{symbol}.json')
    if not isinstance(record, dict) or record.get('symbol') != symbol:
        raise ValueError('quote_snapshot_unavailable')
    observed = _time(record.get('observed_at'))
    if observed is None or observed.date() != now.date() or not timedelta(0) <= now - observed <= timedelta(seconds=15):
        raise ValueError('quote_snapshot_stale')
    quote = fresh_quote({'rsp_cd': '00000', 'Output_0': record.get('quote')}, symbol, now)
    at = datetime.strptime(now.strftime('%Y%m%d') + quote['hoga_bsop_hour'], '%Y%m%d%H:%M:%S').replace(tzinfo=KST)
    if not at <= observed or not timedelta(0) <= now - at <= timedelta(seconds=15):
        raise ValueError('quote_snapshot_stale')
    return {**quote, '_snapshot_observed_at': observed.isoformat()}


def _recorded_bars(event, symbol, now):
    """Use only the shared runner's recorded same-day validated bars and their event time."""
    from nh_paper_isolated import ETF7_SYMBOLS
    if not isinstance(event, dict) or event.get('market_source') != 'NH KRX 1-minute completed bars':
        return None, None
    observed = _time(event.get('time'))
    if observed is None or observed.date() != now.date() or not timedelta(0) <= now - observed <= timedelta(seconds=30):
        return None, None
    names = {v: k for k, v in ETF7_SYMBOLS.items()}
    markets = event.get('market_bars')
    rows = (markets.get(names.get(symbol, symbol)) or markets.get(symbol)) if isinstance(markets, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None, None
    # The recording path stores only valid_bars, which has already checked this original KST session date.
    day = observed.strftime('%Y%m%d')
    if any('bsop_date' in row and row['bsop_date'] != day for row in rows):
        return None, None
    return [{**row, 'bsop_date': day} for row in rows], observed


def _recent_bars(path, symbols, now):
    """Find the newest still-fresh recorded event for each missing symbol."""
    found = {}
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                if '"market_bars"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(event, dict) or event.get('event') not in ('market_context', 'decision'):
                    continue
                at = _time(event.get('time'))
                if at is None or not timedelta(0) <= now - at <= timedelta(seconds=30):
                    continue
                for code in symbols:
                    if code in found and found[code][1] >= at:
                        continue
                    rows, observed = _recorded_bars(event, code, now)
                    if rows:
                        found[code] = (rows, observed)
    except OSError:
        pass
    return found


def inspect_once(read, root, advice_root, now):
    """Consume stored inputs for verified positions without any external data call."""
    from nh_paper_isolated import RecordedBars
    from vibe_fast_exit import evaluate
    from vibe_event_trigger import detect
    from vibe_runner import load_advice
    result = []
    try:
        held = positions(root, now)
        event = RecordedBars(None, Path(root).parent / 'shared-etf-paper').latest()
    except (OSError, ValueError, TypeError, sqlite3.Error, json.JSONDecodeError):
        return [{'state': 'unavailable', 'reason': 'position_or_plan_unavailable'}]
    if not held:
        return [{'state': 'unavailable', 'reason': 'no_verified_general_position'}]
    recorded = {position['symbol']: _recorded_bars(event, position['symbol'], now) for position in held}
    recent = _recent_bars(Path(root).parent / 'shared-etf-paper' / 'events.jsonl', set(recorded), now)
    for code, (bars, at) in recent.items():
        if recorded[code][1] is None or at > recorded[code][1]:
            recorded[code] = bars, at
    for position in held:
        code = position['symbol']
        event_trigger = None
        try:
            observed = datetime.now(KST)
            quote = _snapshot_quote(root, code, observed)
            bars, received = recorded[code]
            if not _position_current(root, position, observed):
                raise ValueError('position_changed')
            try:
                event_trigger = detect(symbol=code, position_id=position['position_id'], qty=position['qty'], now=observed,
                                       entry_at=position['entry_at'], entry_price=position['entry_price'], profile='normal',
                                       bars=bars, bars_symbol=code if bars is not None else None,
                                       bars_observed_at=received, quote=quote,
                                       quote_observed_at=quote.get('_snapshot_observed_at'))
            except (ValueError, TypeError, KeyError, AttributeError):
                event_trigger = {'state': 'unavailable', 'triggered': False, 'reasons': ['trigger_error']}
            advice = load_advice(advice_root, observed)
            if not _model_position_matches(root, advice_root, code, advice):
                advice = None
            existing_exit = _existing_exit(root, position, quote, observed, advice)
            context = _context_status(root, advice, observed, code)
            evaluation = evaluate(symbol=code, now=observed, entry_at=position['entry_at'],
                                  entry_price=position['entry_price'], profile='normal', existing_exit=existing_exit,
                                  bars=bars, bars_symbol=code if bars is not None else None,
                                  bars_observed_at=received, quote=quote, advice=advice,
                                  context_status=context)
            liquidity = _liquidity(quote, code, observed)
            if liquidity is not None:
                evaluation.setdefault('inputs', {}).update(liquidity)
            if event_trigger.get('triggered') and not _position_current(root, position, datetime.now(KST)):
                event_trigger = {'state': 'unavailable', 'triggered': False, 'reasons': ['position_changed']}
            result.append({'symbol': code, 'qty': position['qty'], 'position_id': position['position_id'],
                           'latest_fill_id': position['latest_fill_id'], 'event_trigger': event_trigger,
                           'actual_exit_time': None, **evaluation})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            if isinstance(event_trigger, dict) and event_trigger.get('triggered'):
                try:
                    current = _position_current(root, position, datetime.now(KST))
                except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
                    current = False
                if not current:
                    event_trigger = {'state': 'unavailable', 'triggered': False, 'reasons': ['position_changed']}
            result.append({'symbol': code, 'qty': position['qty'], 'position_id': position['position_id'],
                           'latest_fill_id': position['latest_fill_id'], 'event_trigger': event_trigger,
                           'state': 'unavailable', 'reason': 'market_data_unavailable'})
    return result


def append_results(path, observations, now):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        for item in observations:
            stream.write(json.dumps({'observed_at': now.isoformat(), 'mode': 'observation_only', **item}, ensure_ascii=False) + '\n')


def _quote_fingerprint(root):
    directory = Path(root) / 'fast-exit-quotes'
    try:
        return tuple(sorted((path.name, path.stat().st_mtime_ns, path.stat().st_size)
                            for path in directory.glob('*.json') if re.fullmatch(r'\d{6}\.json', path.name)))
    except OSError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    from src.config.paths import get_runtime_root
    from engine_roles import ledger_root
    nhplug = get_runtime_root() / 'nhplug'
    directory = nhplug / 'vibe-fast-exit-shadow'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'observer.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = object()
        from session_times import MARKET_OPEN, load as session_clock
        while True:
            now = datetime.now(KST)
            close = session_clock(nhplug / 'shared-etf-paper')['close']  # the runners stop here (was a fixed 15:20)
            if now.weekday() < 5 and MARKET_OPEN <= now.strftime('%H:%M') < close:
                try:
                    root = ledger_root(nhplug, 'vibe')
                    fingerprint = (str(root), _quote_fingerprint(root))
                    if args.once or fingerprint != previous:
                        observations = inspect_once(None, root, nhplug / 'vibe-paper', now)
                        append_results(directory / f'observations-{now:%Y-%m-%d}.jsonl', observations, datetime.now(KST))
                        previous = fingerprint
                except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                    append_results(directory / f'observations-{now:%Y-%m-%d}.jsonl',
                                   [{'state': 'unavailable', 'reason': 'observer_input_unavailable'}], datetime.now(KST))
            if args.once or now.weekday() >= 5 or now.strftime('%H:%M') >= close:
                break
            time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()


def compare_observations(observation_file, ledger_file):
    """Join candidates to later confirmed engine sells, never treating the observed bid as a fill."""
    candidates = []
    try:
        with Path(observation_file).open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                if isinstance(row, dict) and row.get('mode') == 'observation_only' and row.get('candidate') is True:
                    inputs = row.get('inputs')
                    if not isinstance(inputs, dict):
                        raise ValueError('candidate inputs unavailable')
                    at = _time(inputs.get('evaluated_at'))
                    quote_at = _time(inputs.get('quote_at'))
                    bid = inputs.get('bid')
                    if (at is None or quote_at is None or not timedelta(0) <= at - quote_at <= timedelta(seconds=15)
                            or type(bid) not in (int, float) or not 0 < bid < float('inf')
                            or type(row.get('qty')) is not int or row['qty'] <= 0
                            or not isinstance(row.get('position_id'), str)
                            or not isinstance(row.get('symbol'), str) or not re.fullmatch(r'\d{6}', row['symbol'])):
                        raise ValueError('candidate invalid')
                    candidates.append({'position_id': row['position_id'], 'symbol': row['symbol'], 'candidate_at': at,
                                       'quote_at': quote_at, 'observed_bid': bid, 'observed_qty': row['qty']})
        path = Path(ledger_file)
        if not path.is_file():
            raise ValueError('ledger unavailable')
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
            fills = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
        positions, sells, known, last_by_symbol = {}, {}, set(), {}
        for order_id, symbol, side, qty, price, stamp in fills:
            at = _time(stamp)
            if (not isinstance(order_id, str) or not order_id or not isinstance(symbol, str)
                    or not re.fullmatch(r'\d{6}', symbol) or side not in ('BUY', 'SELL')
                    or type(qty) is not int or qty <= 0 or type(price) not in (int, float)
                    or not 0 < price < float('inf') or at is None):
                raise ValueError('fill invalid')
            current = positions.get(symbol)
            if symbol in last_by_symbol and at < last_by_symbol[symbol]:
                raise ValueError('fill time reversed')
            last_by_symbol[symbol] = at
            if side == 'BUY':
                if current is None:
                    generation = hashlib.sha256(f'{symbol}\0{order_id}'.encode()).hexdigest()[:20]
                    current = {'id': generation, 'qty': 0, 'last': at}
                    positions[symbol] = current
                    known.add((symbol, generation))
                current['qty'] += qty
            else:
                if current is None or current['qty'] < qty:
                    raise ValueError('sell exceeds holding')
                sells.setdefault((symbol, current['id']), []).append({'time': at, 'order_id': order_id,
                                                                       'qty': qty, 'price': price})
                current['qty'] -= qty
            current['last'] = at
            if not current['qty']:
                del positions[symbol]
        comparisons = []
        for c in candidates:
            key = (c['symbol'], c['position_id'])
            after = [x for x in sells.get(key, []) if x['time'] > c['candidate_at']]
            actual = [{'time': item['time'].isoformat(), 'order_id': item['order_id'], 'qty': item['qty'],
                       'price': item['price'], 'price_minus_observed_bid': item['price'] - c['observed_bid']}
                      for item in after]
            comparisons.append({**c, 'candidate_at': c['candidate_at'].isoformat(), 'quote_at': c['quote_at'].isoformat(),
                                'state': 'engine_sells_recorded' if actual else 'awaiting_engine_sell' if key in known else 'unavailable',
                                'engine_sell_fills': actual,
                                'limitation': '관측 호가는 체결가가 아니며 가격 차이는 체결 가능성·손익 차이가 아님'})
        return comparisons
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, json.JSONDecodeError):
        return [{'state': 'unavailable', 'reason': 'observation_or_ledger_unavailable'}]
