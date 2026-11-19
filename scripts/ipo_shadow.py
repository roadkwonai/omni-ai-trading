"""신규주 그림자 기록 (user 2026-11-19, after the 에이엘로봇 11-18 review): for every recent listing still inside the
new-listing window (ipo_days sessions), what the runner's new-listing rule and two candidate changes would have traded
that day on 1-minute bars. Research only: the runner's rules do not change and nothing is ordered.

    PYTHONPATH=agent:scripts .venv/bin/python scripts/ipo_shadow.py --day 2026-11-19

Variants (one table row each per listing and day, market-research.sqlite3 ipo_shadow):
  current   the runner today: price >= base (the offer price on the listing day, the previous close later), >= today's
            open, within ipo_near_high_pct of the day's high, the last minute up on 5 minutes, volume up; then the
            new-listing exit (stop ipo_stop_pct, half at ipo_half_pct, the rest on a ipo_trail_pct trail or back at
            the entry price, ipo_time_stop_min, the flatten time); at most ipo_daily_max entries a day
  vwap_base A: the same with the day's VWAP as the base (11-18: 에이엘로봇 traded under its offer price for 291 of 310
            minutes while it rose 35% from its low)
  vwap_dip  C: A plus a re-entry after a profitable exit when the price is back above VWAP, rising on rising volume
            (not near the high), up to DIP_MAX_ENTRIES a day
The base of `current` is the one the runner itself read (events.jsonl ipo_seen, from 11-20); without it there is no
`current` row: no other source gives the KRX close the runner compares with (Yahoo's 1-minute bars end at 14:59, Yahoo
has no daily bars for days-old listings, Toss daily candles include the NXT after-market). Bars are Yahoo
1-minute bars (kept about a week), a replay of the rules, not fills: the runner checks about once a minute on NH quotes.
Each listing is replayed on its own: the runner's day-wide ipo_daily_max and ipo_max_positions across listings are not
applied, so the variants are compared listing by listing. A rerun replaces only the listings it could replay again.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
VARIANTS = {'current': '지금 규칙', 'vwap_base': 'A. 기준가를 VWAP으로', 'vwap_dip': 'C. A + 익절 뒤 VWAP 위 눌림 재매수'}
DIP_MAX_ENTRIES = 4
SCHEMA = ('CREATE TABLE IF NOT EXISTS ipo_shadow (key TEXT PRIMARY KEY, day TEXT, code TEXT, name TEXT, session INTEGER, '
          'variant TEXT, base REAL, trades TEXT, n INTEGER, sum_pct REAL, wins INTEGER, updated TEXT)')


def rules(root=ROOT, day=None):
    """The runner's new-listing numbers as saved now (strategy settings, ipo_start, the session clock) on `day`'s
    exchange clock (수능 day: an hour later)."""
    import session_times
    from strategy_settings import read_settings
    from tunables import setting
    s = read_settings(Path(root))[0]
    day = day or session_times.today()
    clock = session_times.load(Path(root), day)
    return {'start': session_times.shifted(setting('ipo_start'), day), 'cutoff': clock['cutoff'], 'flatten': clock['flatten'],
            'near_high': float(s['ipo_near_high_pct']), 'stop': float(s['ipo_stop_pct']), 'half': float(s['ipo_half_pct']),
            'trail': float(s['ipo_trail_pct']), 'time_stop': int(s['ipo_time_stop_min']), 'daily_max': int(s['ipo_daily_max']),
            'volume': float(s['entry_volume_ratio']), 'rising': bool(s['rising_check']), 'days': int(s['ipo_days'])}


def simulate(bars, base, variant, r):
    """[{buy, buy_price, sell, sell_price, why, pct}] of one day. bars: [(HH:MM, open, high, low, close, volume)] from
    09:00; a check at minute i sees bars before i (the runner's completed bars) and buys at bar i's open; exits read
    each bar's close, as the runner reads a price about once a minute."""
    trades, pos, entries, won_last = [], None, 0, False
    most = DIP_MAX_ENTRIES if variant == 'vwap_dip' else r['daily_max']
    for i, (now, opened_i, _, _, close_i, _) in enumerate(bars):
        if pos:
            pos['peak'] = max(pos['peak'], close_i)
            ret = close_i / pos['price'] - 1
            if not pos['half'] and ret >= r['half'] / 100:
                pos.update(half=True, realized=0.5 * ret, left=0.5)
            why = ('stop' if ret <= -r['stop'] / 100 else 'breakeven' if pos['half'] and close_i <= pos['price'] else
                   'trail' if pos['half'] and close_i <= pos['peak'] * (1 - r['trail'] / 100) else
                   'time_stop' if r['time_stop'] and i - pos['i'] >= r['time_stop'] else 'flatten' if now >= r['flatten'] else None)
            if why:
                pct = round((pos['realized'] + pos['left'] * ret) * 100, 2)
                trades.append({'buy': pos['at'], 'buy_price': pos['price'], 'sell': now, 'sell_price': close_i, 'why': why, 'pct': pct})
                won_last, pos = pct > 0, None
            continue
        if not (r['start'] <= now < r['cutoff']) or entries >= most or i < 10:
            continue
        price, day_open, high = bars[i - 1][4], bars[0][1], max(b[2] for b in bars[:i])
        volume = sum(b[5] for b in bars[:i])
        vwap = sum(b[4] * b[5] for b in bars[:i]) / volume if volume else price
        rising = not r['rising'] or bars[i - 1][4] > bars[i - 6][4]
        flow = sum(b[5] for b in bars[i - 5:i]) >= r['volume'] * sum(b[5] for b in bars[i - 10:i - 5])
        floor = vwap if variant in ('vwap_base', 'vwap_dip') else base
        breakout = price >= floor and price >= day_open and price >= high * (1 - r['near_high'] / 100) and rising and flow
        dip = variant == 'vwap_dip' and entries > 0 and won_last and price >= vwap and rising and flow
        if breakout or dip:
            pos = {'i': i, 'at': now, 'price': opened_i, 'peak': opened_i, 'half': False, 'realized': 0.0, 'left': 1.0}
            entries += 1
    return trades


def yahoo_days(code, kosdaq=(), get=None):
    """{YYYY-MM-DD: [(HH:MM, open, high, low, close, volume)]} of Yahoo 1-minute bars (about a week), one request per code
    (oversold_cases.yahoo_symbols: a KOSDAQ code on .KQ first)."""
    import oversold_cases as oc
    get = get or oc._get
    for symbol in oc.yahoo_symbols(code, kosdaq):
        try:
            raw = get(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=5d')['chart']['result'][0]
        except Exception:  # noqa: BLE001 - 404 on the other market, no data: the next symbol, else nothing
            continue
        q, out = raw['indicators']['quote'][0], {}
        for k, ts in enumerate(raw.get('timestamp') or []):
            if None in (q['open'][k], q['high'][k], q['low'][k], q['close'][k]):
                continue
            at = datetime.fromtimestamp(ts, KST)
            out.setdefault(at.date().isoformat(), []).append((at.strftime('%H:%M'), q['open'][k], q['high'][k], q['low'][k],
                                                              q['close'][k], q['volume'][k] or 0))
        if out:
            return out
    return {}


def seen_bases(root, day):
    """{code: base} the runner read for each listing that day (its first ipo_seen event)."""
    from sqlite_ro import day_rows
    out = {}
    for e in day_rows(Path(root) / 'events.jsonl', day)[0] or []:
        if e.get('event') == 'ipo_seen' and type(e.get('base')) in (int, float) and e['base'] > 0:
            out.setdefault(str(e.get('symbol')), float(e['base']))
    return out


def update(root=ROOT, day=None, db=None, get=None, listings=None):
    """Recompute the day's shadow rows for every listing inside the window; returns counts."""
    import new_listings
    from nh_paper_isolated import sessions_held
    root = Path(root)
    day = day or datetime.now(KST).strftime('%Y-%m-%d')
    today = date.fromisoformat(day)
    r = rules(root, today)
    listings = listings if listings is not None else new_listings.recent(root, datetime.now(KST))
    from oversold_cases import kosdaq_codes
    kosdaq = kosdaq_codes() if get is None else ()  # a real run only: tests pass their own get
    bases, rows, now = seen_bases(root, day), [], datetime.now(KST).isoformat(timespec='seconds')
    for item in listings:
        listed = datetime.strptime(item['listed'], '%Y%m%d').date()
        session = sessions_held(listed, today)
        if listed > today or session >= r['days']:  # the saved ipo_days (the module default is the runner's until it loads)
            continue
        by_day = yahoo_days(item['code'], kosdaq, get)
        bars = by_day.get(day)
        if not bars:
            continue
        base = bases.get(item['code'])
        for variant in VARIANTS:
            if variant == 'current' and base is None:
                continue  # the runner's base (offer price, then the KRX previous close) is known only from its reading
            trades = simulate(bars, base, variant, r)
            rows.append((f"{day}:{item['code']}:{variant}", day, item['code'], item.get('name', ''), session, variant, base,
                         json.dumps(trades, ensure_ascii=False), len(trades), round(sum(t['pct'] for t in trades), 2),
                         sum(t['pct'] > 0 for t in trades), now))
    path = Path(db or root / 'market-research.sqlite3')
    with sqlite3.connect(path, timeout=5) as conn:
        conn.execute(SCHEMA)
        for code in {row[2] for row in rows}:  # only what this run replayed again: a rerun without bars keeps the record
            conn.execute('DELETE FROM ipo_shadow WHERE day = ? AND code = ?', (day, code))
        conn.executemany('INSERT INTO ipo_shadow VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    path.chmod(0o600)
    return {'day': day, 'rows': len(rows), 'listings': len({row[2] for row in rows})}


def summary(db, day, days=10):
    """{variant: {'label', 'listing_days', 'trades', 'sum_pct', 'win_rate'}} over the last `days` recorded days up to `day`."""
    path = Path(db)
    if not path.exists():
        return {}
    from sqlite_ro import ro_connect
    with ro_connect(path) as conn:
        try:
            dates = [d for (d,) in conn.execute('SELECT DISTINCT day FROM ipo_shadow WHERE day <= ? ORDER BY day DESC LIMIT ?', (day, days))]
        except sqlite3.OperationalError:  # before the first record
            return {}
        if not dates:
            return {}
        rows = conn.execute(f"SELECT variant, COUNT(*), SUM(n), SUM(sum_pct), SUM(wins) FROM ipo_shadow WHERE day IN ({','.join('?' * len(dates))}) "
                            'GROUP BY variant', dates).fetchall()
    return {v: {'label': VARIANTS.get(v, v), 'days': f'{dates[-1]}~{dates[0]}', 'listing_days': k, 'trades': n or 0,
                'sum_pct': round(s or 0, 2), 'win_rate': round(w / n, 2) if n else None} for v, k, n, s, w in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day', help='YYYY-MM-DD (Asia/Seoul); default today')
    parser.add_argument('--summary', action='store_true', help='print the last 10 recorded days by variant')
    args = parser.parse_args()
    day = args.day or datetime.now(KST).strftime('%Y-%m-%d')
    if args.summary:
        print(json.dumps(summary(ROOT / 'market-research.sqlite3', day), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(update(day=day), ensure_ascii=False))


if __name__ == '__main__':
    main()
