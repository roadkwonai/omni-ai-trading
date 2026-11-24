"""Blocked-candidate outcomes (user 2026-11-17 "사후 추적 추가"; research records, never orders).

What the shared paper runner did NOT buy, settled after the close so the filters can be judged by data:
- strength: each `strength_shadow` pick (the watchlist-strength name the runner would have taken when Jev's pick was
  blocked), with the reason it was blocked;
- ipo: each recent new listing inside the new-listing window (ipo_days) on a day the runner looked at listings and
  bought none, measured from the new-listing start time (ipo_start), with the day's most common block reason.

Prices are Yahoo 5-minute bars (oversold_cases.yahoo_symbols), not the runner's NH bars or fills. Yahoo stamps a bar at
its start, so the base is the close of the last bar that ended by the time; r30 is the first close at or after 30
minutes later, r_last the close of the bar ending by 15:00, mfe/mae the best and worst high/low of the bars in between. A code Yahoo does not carry (new alphanumeric codes) is skipped. Stored in
market-research.sqlite3 table blocked_outcomes (recomputed per day, so reruns never double count).

  PYTHONPATH=agent:scripts .venv/bin/python scripts/blocked_outcomes.py [--day YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
CLOSE = time(15, 0)


def _events(root, day):
    from sqlite_ro import day_rows
    return day_rows(Path(root) / 'events.jsonl', day)[0] or []


def candidates(root, day, events, settings):
    """[(kind, symbol, time iso, blocked, value)] of the day's blocked strength picks and untraded new listings."""
    rows = []
    seen = set()
    for e in events:  # one row per symbol and reason a day: the first time it was blocked
        if e.get('event') == 'strength_shadow' and e.get('symbol') and (e['symbol'], e.get('blocked')) not in seen:
            seen.add((e['symbol'], e.get('blocked')))
            rows.append(('strength', e['symbol'], e['time'], e.get('blocked') or 'unknown', e.get('confidence')))
    reasons = Counter(str(e['ipo_reason']) for e in events if e.get('event') == 'v2_entry_gate' and e.get('ipo_reason'))
    bought = {e.get('symbol') for e in events if e.get('event') == 'new_listing'}
    if reasons:
        try:
            listings = json.loads((Path(root) / 'new-listings.json').read_text(encoding='utf-8')).get('rows') or []
        except (OSError, ValueError, AttributeError):
            listings = []
        from nh_paper_isolated import sessions_held
        start = settings.get('ipo_start', '09:20')
        when = f'{day}T{start}:00+09:00'
        today = datetime.fromisoformat(day).date()
        for item in listings:
            try:
                listed = datetime.strptime(item['listed'], '%Y%m%d').date()
            except (KeyError, TypeError, ValueError):
                continue
            if item.get('code') in bought or listed > today or sessions_held(listed, today) >= int(settings.get('ipo_days', 2)):
                continue
            rows.append(('ipo', item['code'], when, reasons.most_common(1)[0][0], sessions_held(listed, today)))
    return rows


def yahoo_bars(code, day, fetch=None, kosdaq=()):
    """[(minute, open, high, low, close)] of `day` for a KRX code from Yahoo 5-minute bars (oversold_cases.yahoo_symbols;
    kosdaq: oversold_cases.kosdaq_codes(), so a KOSDAQ code is asked on .KQ first)."""
    from oversold_cases import yahoo_symbols
    if fetch is None:
        from oversold_research import fetch
    for symbol in yahoo_symbols(code, kosdaq):
        bars = [b for b in fetch(symbol, '5m', '5d') if b[0].date().isoformat() == day]
        if bars:
            return bars
    return []


BAR = timedelta(minutes=5)  # Yahoo stamps a bar at its start: its close is known BAR later


def settle(when, bars):
    """{'price', 'r30', 'r_last', 'mfe', 'mae'} from 5-minute bars after `when` (None without a base)."""
    at = datetime.fromisoformat(when)
    before = [b for b in bars if b[0] + BAR <= at]
    base = before[-1][4] if before else None
    later = [b for b in bars if at <= b[0] and (b[0] + BAR).astimezone(KST).time() <= CLOSE]
    if not base or not later:
        return {'price': base, 'r30': None, 'r_last': None, 'mfe': None, 'mae': None}
    pct = lambda v: round((v / base - 1) * 100, 3)
    after30 = [b for b in later if b[0] + BAR >= at + timedelta(minutes=30)]
    return {'price': base, 'r30': pct(after30[0][4]) if after30 else None, 'r_last': pct(later[-1][4]),
            'mfe': pct(max(b[2] for b in later)), 'mae': pct(min(b[3] for b in later))}


def update(root=ROOT, day=None, research=None, fetch=None):
    """Recompute the day's blocked-candidate outcomes; returns counts."""
    from strategy_settings import read_settings
    from tunables import setting
    root = Path(root)
    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    settings = {**read_settings(root)[0], 'ipo_start': setting('ipo_start')}
    rows, cache = [], {}
    from oversold_cases import kosdaq_codes
    kosdaq = kosdaq_codes() if fetch is None else ()  # a real run only: tests pass their own fetch
    for kind, symbol, when, blocked, value in candidates(root, day, _events(root, day), settings):
        if symbol not in cache:
            try:
                cache[symbol] = yahoo_bars(symbol, day, fetch, kosdaq)
            except Exception:
                cache[symbol] = []
        if not cache[symbol]:
            continue
        rows.append({'key': f'{kind}:{day}:{symbol}:{blocked}', 'day': day, 'kind': kind, 'symbol': symbol, 'time': when,
                     'blocked': blocked, 'value': value, **settle(when, cache[symbol])})
    research = Path(research or root / 'market-research.sqlite3')
    with sqlite3.connect(research, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS blocked_outcomes (key TEXT PRIMARY KEY, day, kind, symbol, time, blocked, value, '
                   'price, r30, r_last, mfe, mae)')
        db.execute('DELETE FROM blocked_outcomes WHERE day = ?', (day,))
        db.executemany('INSERT INTO blocked_outcomes VALUES (:key, :day, :kind, :symbol, :time, :blocked, :value, :price, '
                       ':r30, :r_last, :mfe, :mae)', rows)
    research.chmod(0o600)
    return {'day': day, 'rows': len(rows), 'skipped_no_price': sum(1 for v in cache.values() if not v)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', help='YYYY-MM-DD (Asia/Seoul); default today')
    args = parser.parse_args()
    print(json.dumps(update(day=args.day), ensure_ascii=False))


if __name__ == '__main__':
    main()
