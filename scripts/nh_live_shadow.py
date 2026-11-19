"""The dashboard's virtual run of a live account: what the live mirror (nh_live_follow) would do today from its engine's
paper ledger.

Observation only: it reads the engine ledger, the paper runner's recorded 1-minute closes and the mirror's own state
files; it never calls a broker or a model, never orders and never writes the mirror's files. Prices are the ledger's
fill prices and recorded 1-minute closes, not live bids/asks or fills.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS

KST = ZoneInfo('Asia/Seoul')


def ledger_shadow(path, day, events, capital=None, price_cap=None, shares_for=None, stop_pct=None, *, root=None,
                  record=None, now=None):
    """What the live mirror would do from the engine's own ledger fills: each paper tranche becomes `shares_for(...)` live
    shares (one by default), sold down as the ledger sells (mirror_target), at the ledger's fill prices, within the
    capital and price cap. Today's recorded closes run the live stop from the live average (`stop_pct`, latched for the
    day until the ledger buys it again on a later day) and the daily loss latch (the account's daily_loss_krw within the
    guard's limit; latched: everything is sold and nothing more bought that day). With the mirror's runtime `root` and
    the account `record` the replay follows the mirror itself, reading its state without writing it: the per-symbol
    stops of stop_percent_for_positions (plan stops for 낙폭과대/신규주, the regime-tightened 1% from today's market view)
    and the hand trades from the time they were made (hand buys leave at the flatten time; `now` decides whether that
    time has passed). Fills before `day` set the
    carried book. `overnight` marks a simulated position already open on an earlier Korean calendar day; it does not
    assert that an actual account held it. A missing ledger means the mirror buys nothing, so the result is empty."""
    import sqlite3
    from nh_live_follow import apply_mirror_units, fill_id, mirror_target
    from src.tools.nhplug_live_guard import LIMIT
    from accounts import DEFAULT_BUDGET
    capital, price_cap = (capital or DEFAULT_BUDGET['live']['capital_krw'], price_cap or DEFAULT_BUDGET['live']['order_price_max_krw'])
    limit = ((record or {}).get('budget') or {}).get('daily_loss_krw')
    loss_cap = min(limit, LIMIT) if type(limit) is int and limit > 0 else LIMIT  # as LiveOrderGuard.observe_loss
    prices, timeline = {}, []
    for event in events:  # today's recorded 1-minute closes
        if isinstance(event, dict) and str(event.get('time', '')).startswith(day):
            for name, bars in (event.get('market_bars') or {}).items():
                try:
                    code, close = SYMBOLS.get(name, name), int(bars[0]['stck_prpr'])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                prices[code] = close
                timeline.append((str(event['time']), 1, code, 'close', close))
    rows = []
    if path is not None and Path(path).exists():
        db = sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True, timeout=5)
        try:
            rows = db.execute('SELECT rowid,symbol,side,quantity,price,time FROM fills WHERE time < ? ORDER BY time, rowid',
                              (f'{day}T99',)).fetchall()
        finally:
            db.close()
    timeline += [(str(t), 0, sym, side, (qty, price, fill_id(rowid, sym, qty, price, t))) for rowid, sym, side, qty, price, t in rows]
    mirror = root is not None and record is not None and bool(rows)
    stops, flatten = {}, None
    if mirror:
        from nh_live_follow import manual_state, session_clock, stop_percent_for_positions
        if stop_pct is not None and prices:  # a day without recorded closes cannot stop (and skips the journal read)
            basis = stop_percent_for_positions(Path(path), root, dict.fromkeys({r[1] for r in rows}, 1), stop_pct)
            stops = {code: entry['pct'] for code, entry in basis.items()}
        flatten = session_clock(root)['flatten']
        hand = manual_state(root, record)
        timeline += [(hand['since'][code], 2, code, 'hand', offset) for code, offset in hand['offsets'].items() if code in hand['since']]
        if (now or datetime.now(KST)).astimezone(KST).strftime('%Y-%m-%dT%H:%M') >= f'{day}T{flatten}':
            timeline.append((f'{day}T{flatten}:00+09:00', 3, None, 'flatten', None))
    timeline.sort(key=lambda r: (r[0], r[1]))

    book, live, trades, skipped, realized, stopped = {}, {}, [], {}, Decimal(0), {}
    offsets, last, halted = {}, {}, None

    def target_of(symbol):
        pos = book.get(symbol)
        paper = mirror_target({symbol: pos}).get(symbol, 0) if pos and pos['qty'] else 0
        return 0 if halted or symbol in stopped else max(0, paper + offsets.get(symbol, 0))

    def sell(stamp, symbol, qty, price, reason):
        nonlocal realized
        held = live[symbol]
        pnl = (price - held['entry']) * qty
        held['qty'] -= qty
        if not held['qty']:
            held['opened_at'] = None
        if stamp.startswith(day):
            realized += pnl
            trades.append({'time': stamp, 'symbol': symbol, 'side': 'SELL', 'qty': qty, 'price': int(price), 'reason': reason,
                           'pnl': int(pnl)})

    def follow(stamp, symbol, price, sell_reason, buy_reason='모의 원장 매수'):
        """Trade `symbol` to its target at `price`: buys within the price cap and capital (a skipped share stays skipped)."""
        held = live.setdefault(symbol, {'qty': 0, 'entry': Decimal(0), 'opened_at': None})
        target, bought = target_of(symbol), 0
        while held['qty'] < target:
            exposure = sum(prices.get(c, h['entry']) * h['qty'] for c, h in live.items())
            reason = (f'주문가격 {price_cap:,}원 초과' if price > price_cap else
                      f'운용금 {capital:,}원 초과' if exposure + price > capital else None)
            if reason:
                if stamp.startswith(day):
                    skipped[reason] = skipped.get(reason, 0) + 1
                break
            if not held['qty']:
                held['opened_at'] = stamp
            held['entry'] = (held['entry'] * held['qty'] + price) / (held['qty'] + 1)
            held['qty'] += 1
            bought += 1
        if bought and stamp.startswith(day):
            trades.append({'time': stamp, 'symbol': symbol, 'side': 'BUY', 'qty': bought, 'price': int(price), 'reason': buy_reason})
        if held['qty'] > target:
            sell(stamp, symbol, held['qty'] - target, price, sell_reason)

    def price_of(symbol):
        held = live.get(symbol) or {}
        return Decimal(str(last[symbol])) if symbol in last else held['entry'] if held.get('qty') else None

    for stamp, _, symbol, kind, value in timeline:
        if kind == 'close':  # the live stop from the live average, then the daily loss latch
            last[symbol] = value
            held, pct = live.get(symbol), stops.get(symbol, stop_pct)
            if held and held['qty'] and pct and value <= held['entry'] * (1 - Decimal(str(pct)) / 100):
                stopped[symbol] = stamp[:10]
                sell(stamp, symbol, held['qty'], Decimal(value), f'실계좌 손절 −{pct:g}%')
            unrealized = sum((Decimal(last[c]) - h['entry']) * h['qty'] for c, h in live.items() if h['qty'] and c in last)
            if halted is None and max(-realized, 0) + max(-unrealized, 0) >= loss_cap:
                halted = stamp
                for code in sorted(c for c, h in live.items() if h['qty']):
                    follow(stamp, code, price_of(code), f'일일 손실 {loss_cap:,}원')
        elif kind == 'hand':
            offsets[symbol] = value
            if price_of(symbol) is not None:
                follow(stamp, symbol, price_of(symbol), '수동 매도', '수동 매수')
        elif kind == 'flatten':  # hand buys leave at the shared flatten time
            for code in sorted(c for c, o in offsets.items() if o > 0):
                offsets.pop(code)
                if price_of(code) is not None:
                    follow(stamp, code, price_of(code), '장 마감 정리')
        else:  # a ledger fill
            qty, raw, fid = value
            price = Decimal(str(raw))
            pos = book.setdefault(symbol, {'qty': 0, 'tranches': 0})
            held_before = pos['qty']
            units = ((shares_for(int(price), qty, fid, (pos['tranches'] if held_before else 0) + 1) if shares_for else 1)
                     if kind == 'BUY' else 0)
            apply_mirror_units(pos, kind, qty, units)
            if kind == 'BUY':
                if not held_before:
                    pos['tranches'] = 0
                    if offsets.get(symbol, 0) < 0:
                        offsets.pop(symbol)  # a hand cut ends with its position
                pos.update(qty=held_before + qty, tranches=pos['tranches'] + 1)
                if stopped.get(symbol) and stopped[symbol] != stamp[:10]:
                    stopped.pop(symbol)  # the ledger bought it again on a later day: the mirror follows again
                if halted and stamp.startswith(day):
                    skipped['일일 손실 중단'] = skipped.get('일일 손실 중단', 0) + 1
            else:
                pos['qty'] = held_before - qty
                if not pos['qty'] and offsets.get(symbol, 0) < 0:
                    offsets.pop(symbol)  # a hand cut ends with its position
            last[symbol] = raw
            follow(stamp, symbol, price, '모의 원장 매도')

    def carried_from_prior_day(stamp):
        try:
            opened = datetime.fromisoformat(stamp)
            return opened.astimezone(KST).date().isoformat() < day if opened.tzinfo is not None else None
        except (TypeError, ValueError, OverflowError):
            return None

    open_positions = {c: {'qty': h['qty'], 'entry': float(h['entry']), 'last': prices.get(c), 'profile': None,
                          'overnight': carried_from_prior_day(h['opened_at']),
                          'unrealized': int((Decimal(prices[c]) - h['entry']) * h['qty']) if c in prices else None}
                      for c, h in live.items() if h['qty']}
    return {'day': day, 'mode': 'observation_only_no_orders', 'trades': trades, 'open': open_positions, 'carry': {},
            'realized': int(realized), 'skipped_entries': skipped, 'loss_halt': halted,
            'basis': ('engine ledger replay with the mirror\'s stops, hand trades and daily loss latch'
                      if mirror else 'engine ledger replay using the fixed account stop and the daily loss latch on recorded closes')
                     + '; fees, taxes, spreads, chase skips, the opening rule and live fills not modelled'}


def effective_rules(record):
    """The account's rules; with follow_paper the paper runner's saved strategy settings replace the account's exit
    numbers, so tuning on paper carries over. The stop is the tightest of the paper stop, the account's own stop and the
    approved live 2%."""
    from accounts import live_rules
    rules = live_rules(record)
    if rules['follow_paper']:
        from strategy_settings import read_settings
        from src.config.paths import get_runtime_root
        paper = read_settings(get_runtime_root() / 'nhplug' / 'shared-etf-paper')[0]
        rules = {**rules, 'sell_confidence': paper['sell_confidence'], 'stop_loss_pct': min(paper['stop_loss_pct'], rules['stop_loss_pct'], 2.0),
                 'take_profit_pct': min(paper['take_profit_all_pct'], 10.0), 'regime_exits': True, 'overnight': True, 'flatten': True}
    return rules


def account_shadow(record, events, day, now=None):
    """The dashboard's virtual run for one live account: the live mirror's calculation for its engine (ledger_shadow with
    the account's capital, price cap, sizing pins, stop and daily loss limit, and the mirror's own state)."""
    from nh_live_follow import paper_source, tranche_shares
    from src.config.paths import get_runtime_root
    budget, root = record['budget'], get_runtime_root() / 'nhplug'
    source = paper_source(root, record.get('engine'))
    return ledger_shadow(source, day, events, budget['capital_krw'], budget['order_price_max_krw'],
                         tranche_shares(record, root=root, source=source), effective_rules(record)['stop_loss_pct'],
                         root=root, record=record, now=now)


def main():
    """Print today's (or --day's) virtual run of every Jev live account, keyed by its accounts.json key."""
    from accounts import load
    from src.config.paths import get_runtime_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', default=datetime.now(KST).strftime('%Y-%m-%d'), help='YYYY-MM-DD (Asia/Seoul)')
    parser.add_argument('--events', type=Path, default=get_runtime_root() / 'nhplug' / 'shared-etf-paper' / 'events.jsonl')
    args = parser.parse_args()
    with args.events.open(encoding='utf-8') as stream:
        events = [json.loads(line) for line in stream if line.strip()]
    runs = {key: account_shadow(record, events, args.day) for key, record in load().items()
            if record.get('kind') == 'live' and record.get('engine') == 'jev' and not record.get('invalid')}
    print(json.dumps(runs, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
