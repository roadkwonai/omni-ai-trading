"""수익 중 약화 그림자 기록 (user 2026-11-25 "기록부터"; research records, never orders).

Would selling a held stock the moment its tape weakens, while it is in profit, beat the runner's own exit? After the close
each look the runner took at a holding (`holding_quote`) gets what the tape showed in the minutes that had ended by then:
volume against its best 3 minutes of the last 15, the gap to the 5-minute average, buying against selling volume, KODEX 200
over 3 minutes, 체결강도 (Kiwoom ka10046) and, from holding-book.jsonl, the waiting bid/ask quantity. `actual_pct` is the
average the rest of that position was sold at the same day (None when it was added to or carried). ETFs are left out.
Stored in market-research.sqlite3 table fade_looks, recomputed per day; `summary` compares rules, before costs.

  PYTHONPATH=agent:scripts .venv/bin/python scripts/fade_shadow.py [--day YYYY-MM-DD | --summary]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlite_ro import day_rows, ro_connect

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
FIELDS = ('profit_pct', 'vol_ratio', 'ma5_gap_pct', 'pressure', 'index_3m_pct', 'strength_1m', 'strength_5m', 'book_ratio', 'actual_pct')
FLOORS = (0.3, 0.5, 0.8)
WEAK = {'volume': lambda r: r['vol_ratio'] is not None and r['vol_ratio'] < 0.5,
        'trend': lambda r: r['ma5_gap_pct'] is not None and r['ma5_gap_pct'] < 0,
        'pressure': lambda r: r['pressure'] is not None and r['pressure'] < 0,
        'index': lambda r: r['index_3m_pct'] is not None and r['index_3m_pct'] < 0,
        'strength': lambda r: r['strength_1m'] is not None and r['strength_5m'] is not None and r['strength_1m'] < r['strength_5m'],
        'book': lambda r: r['book_ratio'] is not None and r['book_ratio'] < 1}
RULES = {**WEAK, 'strength_under_100': lambda r: r['strength_1m'] is not None and r['strength_1m'] < 100,
         'three_of_six': lambda r: sum(f(r) for f in WEAK.values()) >= 3}


def tape(bars, at):
    """The signals in the minutes that had ended by `at` (Kiwoom stamps a bar with its start); {} without 8 of them."""
    done = [b for b in bars if b[0] + timedelta(minutes=1) <= at]
    if len(done) < 8:
        return {}
    v3 = [sum(b[5] for b in done[i - 2:i + 1]) / 3 for i in range(max(2, len(done) - 15), len(done))]
    last, volume = done[-3:], sum(b[5] for b in done[-3:])
    ma5 = sum(b[4] for b in done[-5:]) / 5
    return {'vol_ratio': round(v3[-1] / max(v3), 3) if max(v3) else None, 'ma5_gap_pct': round((done[-1][4] / ma5 - 1) * 100, 3),
            'pressure': round(sum(((b[4] > b[1]) - (b[4] < b[1])) * b[5] for b in last) / volume, 3) if volume else None}


def move(bars, at, minutes=3):
    done = [b for b in bars if b[0] + timedelta(minutes=1) <= at][-(minutes + 1):]
    return round((done[-1][4] / done[0][4] - 1) * 100, 3) if len(done) == minutes + 1 else None


def rest_of_position(fills, at, qty):
    """(how many times the position was flat before `at`, average price the remaining `qty` was sold at that day or None)."""
    held = flats = 0
    left, value = qty, 0.0
    for side, n, price, when in fills:
        if when <= at:
            held += n if side == 'BUY' else -n
            flats += side == 'SELL' and held == 0
            continue
        if left <= 0:
            break
        if side == 'BUY':
            return flats, None
        value, left = value + min(n, left) * price, left - n
    return flats, value / qty if left <= 0 else None


def strengths(code, day):
    """{'HHMM': (1-minute, 5-minute 체결강도)} of today from Kiwoom (the answer holds the current session only)."""
    import kiwoom_data as k
    if day != datetime.now(KST).date().isoformat():
        return {}
    rows = k.call('ka10046', {'stk_cd': code}, 'cntr_str_tm', max_pages=12)
    return {str(r.get('cntr_tm'))[:4]: (k.num(r.get('cntr_str')), k.num(r.get('cntr_str_5min'))) for r in rows}


def update(root=ROOT, day=None, db=None, bars=None, strength=strengths):
    from etf_universe import SYMBOLS
    root, day = Path(root), day or datetime.now(KST).date().isoformat()
    if bars is None:
        import kiwoom_data
        bars = lambda code, date: kiwoom_data.day_bars(code, date)['bars']  # noqa: E731
    looks = [e for e in day_rows(root / 'events.jsonl', day)[0] or [] if e.get('event') == 'holding_quote'
             and e.get('symbol') not in SYMBOLS.values() and e.get('bid') and e.get('avg') and e.get('qty')]
    book = {}
    for e in day_rows(root / 'holding-book.jsonl', day)[0] or []:
        book.setdefault(e.get('symbol'), []).append(e)
    date, rows, seen, failed = datetime.fromisoformat(day).date(), [], {}, 0
    index = bars('069500', date) if looks else []
    for e in looks:
        code, at = e['symbol'], datetime.fromisoformat(e['time'])
        if code not in seen:
            try:
                seen[code] = bars(code, date)
            except Exception:  # noqa: BLE001 - its looks are kept without the signals
                seen[code], failed = [], failed + 1
            try:
                seen[code, 's'] = strength(code, day)
            except Exception:  # noqa: BLE001
                seen[code, 's'], failed = {}, failed + 1
            with ro_connect(root / 'orders.sqlite3') as orders:
                seen[code, 'f'] = [(s, q, p, datetime.fromisoformat(t)) for s, q, p, t in orders.execute(
                    'SELECT side, quantity, price, time FROM fills WHERE symbol = ? ORDER BY time, rowid', (code,))]
        flats, sold = rest_of_position(seen[code, 'f'], at, e['qty'])
        one, five = seen[code, 's'].get(f'{at - timedelta(minutes=1):%H%M}', (None, None))
        b = next((v for v in book.get(code, []) if abs((datetime.fromisoformat(v['time']) - at).total_seconds()) < 2), {})
        row = {'profit_pct': round((e['bid'] / e['avg'] - 1) * 100, 3), **tape(seen[code], at), 'index_3m_pct': move(index, at),
               'strength_1m': one, 'strength_5m': five, 'actual_pct': None if sold is None else round((sold / e['avg'] - 1) * 100, 3),
               'book_ratio': round(b['bid_qty'] / b['ask_qty'], 3) if b.get('bid_qty') is not None and b.get('ask_qty') else None}
        rows.append((f"{day}:{code}:{flats}", day, code, e['time'], *(row.get(f) for f in FIELDS)))
    path = Path(db or root / 'market-research.sqlite3')
    with sqlite3.connect(path, timeout=5) as conn:
        conn.execute(f"CREATE TABLE IF NOT EXISTS fade_looks (position TEXT, day TEXT, code TEXT, time TEXT, {', '.join(f'{f} REAL' for f in FIELDS)}, PRIMARY KEY (position, time))")
        conn.execute('DELETE FROM fade_looks WHERE day = ?', (day,))
        conn.executemany(f"INSERT OR REPLACE INTO fade_looks VALUES ({','.join('?' * (4 + len(FIELDS)))})", rows)
    conn.close()
    path.chmod(0o600)
    return {'day': day, 'looks': len(rows), 'stocks': len({r[2] for r in rows}), 'failed_reads': failed}


def summary(db, day, days=10):
    """Per rule and profit floor: positions whose first weak look came in profit, and that look's bid against the actual
    sale (`vs_actual_pct`, before costs); `late` is the next look's bid, about a minute later, as the live account sells."""
    with ro_connect(db) as conn:
        dates = [r[0] for r in conn.execute('SELECT DISTINCT day FROM fade_looks WHERE day <= ? ORDER BY 1 DESC LIMIT ?', (day, days))]
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute('SELECT * FROM fade_looks WHERE day BETWEEN ? AND ? ORDER BY time', (dates[-1], day))] if dates else []
    positions = {}
    for r in rows:
        positions.setdefault(r['position'], []).append(r)
    settled = {k: v for k, v in positions.items() if any(r['actual_pct'] is not None for r in v)}
    out = {'days': len(dates), 'positions': len(settled), 'rules': {}}
    for floor in FLOORS:
        for name, weak in {'any': lambda r: True, **RULES}.items():
            gains, late = [], []
            for looks in settled.values():
                hit = next((i for i, r in enumerate(looks) if r['profit_pct'] >= floor and weak(r)), None)
                if hit is None or looks[hit]['actual_pct'] is None:
                    continue
                gains.append(looks[hit]['profit_pct'] - looks[hit]['actual_pct'])
                late.append(looks[min(hit + 1, len(looks) - 1)]['profit_pct'] - looks[hit]['actual_pct'])
            if gains:
                out['rules'][f'{name} >= {floor}%'] = {'n': len(gains), 'vs_actual_pct': round(sum(gains) / len(gains), 2),
                                                      'late_vs_actual_pct': round(sum(late) / len(late), 2),
                                                      'better': sum(g > 0 for g in gains), 'worse': sum(g < 0 for g in gains)}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--day')
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    day = args.day or datetime.now(KST).date().isoformat()
    print(json.dumps(summary(ROOT / 'market-research.sqlite3', day) if args.summary else update(day=day), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
