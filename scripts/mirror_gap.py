"""모의–실계좌 차이 (user 2026-11-25): what each paper trade became on a mirrored live account, and where the difference came from.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/mirror_gap.py --account live-1 [--days 10]

Read-only and computed from the kept records each time: the paper ledger (orders.sqlite3), the live fills (live-fills.sqlite3,
whose times are when the mirror sent the order) and the mirror's log (cancelled orders, the loss latch). A live fill is
compared with the last paper fill of the same symbol, side and day before it (or one within two minutes after). Costs are estimates (paper_costs' rates).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug'
EARLY_SECONDS = 120  # a live sell this much before the paper's: the live account's own stop
PAIR_SECONDS = 600  # a live order later than this after the paper fill is not that fill's


def fills(nhplug, key):
    """(paper, live) fills, oldest first: {time, symbol, side, qty, price}."""
    import live_fills
    try:
        with ro_connect(Path(nhplug) / 'shared-etf-paper' / 'orders.sqlite3') as db:
            paper = [{'time': datetime.fromisoformat(t), 'symbol': s, 'side': side.upper(), 'qty': q, 'price': float(p)}
                     for t, s, side, q, p in db.execute('SELECT time,symbol,side,quantity,price FROM fills ORDER BY rowid')]
    except sqlite3.Error:
        paper = []
    live = [{**r, 'time': datetime.fromisoformat(r['time']), 'side': r['side'].upper()} for r in live_fills.rows(nhplug, key)]
    return paper, live


def cost(rows):
    from etf_universe import SYMBOLS
    from paper_costs import COMMISSION_PCT, SELL_TAX_PCT
    return sum(r['qty'] * r['price'] * (COMMISSION_PCT + (SELL_TAX_PCT if r['side'] == 'SELL' and r['symbol'] not in SYMBOLS.values() else 0)) / 100
               for r in rows)


def realized(rows):
    """{(day, symbol): realized P&L} at the moving average, before costs; a sale of shares bought before the record is left out."""
    held, out = defaultdict(lambda: [0, 0.0]), defaultdict(float)
    for r in rows:
        pos = held[r['symbol']]
        if r['side'] == 'BUY':
            pos[1] = (pos[0] * pos[1] + r['qty'] * r['price']) / (pos[0] + r['qty'])
            pos[0] += r['qty']
            continue
        covered = min(pos[0], r['qty'])
        if covered:
            out[r['time'].date().isoformat(), r['symbol']] += (r['price'] - pos[1]) * covered
            pos[0] -= covered
    return out


def mirror_log(nhplug, key):
    """{day: {'cancelled': n, 'halted': first time}} of one account's executing mirror."""
    out = defaultdict(lambda: {'cancelled': 0, 'halted': None})
    try:
        with (Path(nhplug) / 'live-follow.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                if '"CANCEL_PENDING"' not in line and '"halted": true' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('account') != key or event.get('mode') != 'execute':
                    continue
                day = out[str(event.get('time'))[:10]]
                day['cancelled'] += event.get('action') == 'CANCEL_PENDING'
                if event.get('halted') is True:
                    day['halted'] = day['halted'] or str(event.get('time'))[11:16]
    except OSError:
        pass
    return out


def gap(nhplug=ROOT, key='live-1', days=10):
    """[{day, rows, ...totals}] of the last `days` days either side traded, newest first."""
    paper, live = fills(nhplug, key)
    if not live:
        return []
    first = live[0]['time'].date().isoformat()
    paper_pnl, live_pnl, log = realized(paper), realized(live), mirror_log(nhplug, key)
    found = sorted({r['time'].date().isoformat() for r in [*paper, *live] if r['time'].date().isoformat() >= first}, reverse=True)[:days]
    out = []
    for day in found:
        p, l = ([r for r in side if r['time'].date().isoformat() == day] for side in (paper, live))
        slip, delays = defaultdict(lambda: {'BUY': 0.0, 'SELL': 0.0}), {'BUY': [], 'SELL': []}
        for r in l:
            late = lambda x: (r['time'] - x['time']).total_seconds()
            same = [x for x in p if (x['symbol'], x['side']) == (r['symbol'], r['side']) and -EARLY_SECONDS <= late(x) <= PAIR_SECONDS]
            before = [x for x in same if late(x) >= 0] or sorted(same, key=late, reverse=True)[:1]  # its own stop may come first
            if before:
                sign = 1 if r['side'] == 'BUY' else -1
                slip[r['symbol']][r['side']] += (before[-1]['price'] - r['price']) * r['qty'] * sign  # + = better than the paper
                if late(before[-1]) >= 0:
                    delays[r['side']].append(late(before[-1]))
        rows = []
        for symbol in dict.fromkeys(r['symbol'] for r in [*p, *l]):
            rows.append({'symbol': symbol, 'paper_pnl': round(paper_pnl.get((day, symbol), 0)), 'live_pnl': round(live_pnl.get((day, symbol), 0)),
                         'buy_slip': round(slip[symbol]['BUY']), 'sell_slip': round(slip[symbol]['SELL']),
                         'followed': any(r['symbol'] == symbol and r['side'] == 'BUY' for r in l) or not any(r['symbol'] == symbol and r['side'] == 'BUY' for r in p)})
        total = lambda name: sum(r[name] for r in rows)
        out.append({'day': day, 'rows': rows, 'paper_pnl': total('paper_pnl'), 'live_pnl': total('live_pnl'),
                    'buy_slip': total('buy_slip'), 'sell_slip': total('sell_slip'),
                    'not_followed_pnl': sum(r['paper_pnl'] for r in rows if not r['followed']),
                    'paper_cost': round(cost(p)), 'live_cost': round(cost(l)),
                    'buy_delay_s': round(median(delays['BUY'])) if delays['BUY'] else None,
                    'sell_delay_s': round(median(delays['SELL'])) if delays['SELL'] else None,
                    'orders_paired': len(delays['BUY']) + len(delays['SELL']), **log.get(day, {'cancelled': 0, 'halted': None})})
    return out


def summary(nhplug=ROOT, key='live-1', days=10):
    """{today-like newest day, totals of the days}: what the daily improvement report reads."""
    found = gap(nhplug, key, days)
    if not found:
        return {}
    add = lambda name: sum(d[name] for d in found)
    return {'newest': found[0], 'days': len(found), **{n: add(n) for n in ('paper_pnl', 'live_pnl', 'buy_slip', 'sell_slip', 'not_followed_pnl',
                                                                           'paper_cost', 'live_cost', 'cancelled')},
            'halted_days': [d['day'] for d in found if d['halted']]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--account', default='live-1')
    parser.add_argument('--days', type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(gap(key=args.account, days=args.days), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
