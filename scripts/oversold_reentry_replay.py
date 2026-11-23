"""Read-only v2 reentry sensitivity on stored daily cases and supplied 5-minute OHLC.

OHLC is a price proxy. No broker orders, quote reconstruction or Jev veto is simulated.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from oversold_cases import DB, load, symbol_of
from oversold_replay import COST, gate_v2, market_days
from sqlite_ro import ro_connect
from strategy_settings import read_settings

BAR = timedelta(minutes=5)


def replay_after_stop(bars, first_price, sessions, settings):
    """Incremental return on original stopped capital and reentry/stop counts, or None on incomplete data."""
    by_day = defaultdict(list)
    for candle in bars:
        by_day[candle[0].date().isoformat()].append(candle)
    chunks = [sorted(by_day[day]) for day in sessions]
    flatten = int(settings['flatten_hhmm'])
    for chunk in chunks:
        times = [at for at, *_ in chunk]
        end = times[-1] + BAR if times else None
        if (not times or times[0].hour != 9 or times[0].minute > 5
                or end.hour * 100 + end.minute < flatten
                or any(not timedelta(0) < b - a <= BAR * 2 for a, b in zip(times, times[1:]))):
            return None
    stop = settings['oversold_v2_stop_pct'] / 100
    initial = next((i for i, candle in enumerate(chunks[0]) if candle[3] <= first_price * (1 - stop)), None)
    if initial is None:
        return None

    low = low_time = pending = None
    added = stopped = profit = 0
    entry = 0.0
    remaining, retry_qty = 0.0, 1.0
    half = settings['oversold_v2_half_tp_pct'] / 100
    target = settings['oversold_v2_tp_pct'] / 100
    hold = int(settings['oversold_v2_hold_days'])
    transactions_cost = COST['stock'] / 100

    def proceeds(closing, amount):
        return amount * (closing - entry - entry * transactions_cost)

    for day_no, chunk in enumerate(chunks, 1):
        low = low_time = pending = None
        # The daily replay fixed the first stop to D+1. Track only bars completed after that bar.
        for at, o, high, bar_low, c in (chunk[initial + 1:] if day_no == 1 else chunk):
            clock = at.hour * 100 + at.minute
            cutoff = min(int(settings['oversold_v2_until_hhmm']), 1400 if day_no == hold - 1 else 2359)
            entry_open = day_no < hold and 910 <= clock < cutoff
            if pending == at and entry_open:
                entry, remaining, added = o, retry_qty, added + 1
                low = low_time = pending = None
            if remaining:
                if day_no == hold and clock >= flatten:
                    profit += proceeds(o, remaining)
                    return profit / first_price * 100, added, stopped
                floor = entry * (1 - stop)
                if bar_low <= floor:
                    profit += proceeds(min(o, floor), remaining)
                    retry_qty, remaining, stopped = remaining, 0.0, stopped + 1
                    low = low_time = None
                else:
                    if remaining == retry_qty and high >= entry * (1 + half):
                        remaining /= 2
                        profit += proceeds(max(o, entry * (1 + half)), remaining)
                    if high >= entry * (1 + target):
                        profit += proceeds(max(o, entry * (1 + target)), remaining)
                        return profit / first_price * 100, added, stopped
                continue
            if day_no >= hold or clock >= cutoff or added >= int(settings['oversold_v2_reentry_max']):
                continue
            closed_at = at + BAR
            if low is None or bar_low < low:
                low, low_time = bar_low, closed_at
            if (entry_open and closed_at - low_time >= timedelta(minutes=settings['oversold_v2_reentry_hold_min'])
                    and c >= low * (1 + settings['oversold_v2_reentry_bounce_pct'] / 100)):
                pending = closed_at
    if remaining:
        # Some feeds end at the last bar immediately before the configured flatten.
        at, _, _, _, close = chunks[-1][-1]
        closed_at = at + BAR
        if closed_at.hour * 100 + closed_at.minute != flatten:
            return None
        profit += proceeds(close, remaining)
    return profit / first_price * 100, added, stopped


def compare(candles, earliest, settings):
    cases = load()
    markets = market_days(cases)
    covered, totals, eligible = [], defaultdict(list), 0
    with ro_connect(DB) as db:
        for case in cases:
            result = (case.get('replay') or {}).get('v2_plan') or {}
            if (case['day'] < earliest or case['kind'] != 'stock' or result.get('exit') != 'stop'
                    or result.get('days') != 1 or not gate_v2(case, markets, settings)):
                continue
            eligible += 1
            symbol = symbol_of(case['code'])
            hold = int(settings['oversold_v2_hold_days'])
            daily = db.execute('SELECT day,c FROM series WHERE symbol=? AND day>=? ORDER BY day LIMIT ?',
                               (symbol, case['day'], hold + 1)).fetchall()
            if len(daily) != hold + 1 or daily[0][0] != case['day']:
                continue
            bars = [(datetime.fromisoformat(at), *values) for at, *values in candles.get(symbol, [])]
            simulated = replay_after_stop(bars, daily[0][1], [d for d, _ in daily[1:]], settings)
            if simulated is None:
                continue
            delta, bought, stopped = simulated
            base = result['ret']
            totals[case['day']].append((base, base + delta))
            covered.append({'day': case['day'], 'code': case['code'], 'name': case['name'], 'without': base,
                            'with': round(base + delta, 2), 'reentries': bought, 'repeat_stops': stopped})
    daily = {day: [mean(r[i] for r in results) for i in (0, 1)] for day, results in sorted(totals.items())}
    return {'eligible': eligible, 'covered': len(covered),
            'daily': {day: [round(value, 2) for value in returns] for day, returns in daily.items()},
            'daily_average': [round(mean(r[i] for r in daily.values()), 2) for i in (0, 1)] if daily else None,
            'reentries': sum(r['reentries'] for r in covered), 'repeat_stops': sum(r['repeat_stops'] for r in covered),
            'cases': covered}


if __name__ == '__main__':
    from src.config.paths import get_runtime_root

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candles', type=Path, help='JSON mapping Yahoo symbols to timestamp/open/high/low/close arrays')
    parser.add_argument('--since', required=True)
    args = parser.parse_args()
    config, _ = read_settings(get_runtime_root() / 'nhplug' / 'shared-etf-paper')
    print(json.dumps(compare(json.loads(args.candles.read_text()), args.since, config), ensure_ascii=False))
