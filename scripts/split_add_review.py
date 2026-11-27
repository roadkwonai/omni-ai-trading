"""Read-only comparison of dip thresholds against observed paper-account quotes."""
import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean


def review(root, start, end):
    quotes, reasons = defaultdict(list), Counter()
    with (root / 'events.jsonl').open() as stream:
        for line in stream:
            try:
                row = json.loads(line)
                at = datetime.fromisoformat(row['time'])
                symbol = row['symbol']
                if at.tzinfo is None or not start <= at.date().isoformat() <= end:
                    continue
                if row['event'] == 'split_review':
                    reasons[row.get('reason', 'unknown')] += 1
                if row['event'] == 'holding_quote' and all(type(row.get(k)) is int and row[k] > 0 for k in ('bid', 'ask')):
                    quotes[symbol].append((at, row['bid'], row['ask']))
            except (ValueError, KeyError, TypeError):
                continue
    with sqlite3.connect((root / 'orders.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        fills = db.execute('SELECT symbol, side, quantity, price, time FROM fills ORDER BY time, rowid').fetchall()
    positions, opened, observed = defaultdict(int), {}, []
    for symbol, side, qty, price, stamp in fills:
        at = datetime.fromisoformat(stamp)
        if side == 'BUY' and not positions[symbol]:
            opened[symbol] = (at, price)
        elif symbol in opened:
            entry, anchor = opened.pop(symbol)
            if start <= entry.date().isoformat() <= end:
                tape = sorted(q for q in quotes[symbol] if entry + timedelta(minutes=5) <= q[0] < at
                              and q[0].date() == entry.date())
                if tape:
                    observed.append((symbol, anchor, tape))
        positions[symbol] += qty if side == 'BUY' else -qty

    results = {}
    for step in (0.3, 0.5, 0.7, 1.0):
        hits, returns = [], []
        for symbol, anchor, tape in observed:
            hit = next((q for q in tape if q[2] <= anchor * (1 - step / 100)), None)
            if hit is None:
                continue
            at, _, ask = hit
            hits.append({'symbol': symbol, 'time': at.isoformat(), 'ask': ask})
            target = at + timedelta(minutes=30)
            mark = next((q for q in tape if target <= q[0] <= target + timedelta(minutes=3)), None)
            if mark:
                returns.append(100 * (mark[1] / ask - 1))
        results[str(step)] = {'hits': len(hits), 'observed_30m': len(returns),
                              'mean_30m_pct_before_cost': round(mean(returns), 3) if returns else None,
                              'observations': hits}
    return {'positions_with_quotes': len(observed), 'thresholds': results, 'review_reasons': dict(reasons)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--from-day', required=True)
    parser.add_argument('--through-day', required=True)
    args = parser.parse_args()
    days = (args.from_day, args.through_day)
    if not all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) for day in days) or days[0] > days[1]:
        parser.error('expected an ordered YYYY-MM-DD date range')
    print(json.dumps(review(args.root, *days), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
