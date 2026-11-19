"""Position paths and decision settlements for the shared paper account (research records; never orders).

position_paths: each position from first buy to flat, with the best (MFE) and worst (MAE) price reached while held
and what the price did after the exit (30 minutes later, the day's high afterwards, the last recorded bar).
decision_outcomes: every Jev pick / Jev holding view / vibe-trading score settled 5, 15 and 30 minutes later and at
the last recorded bar, next to KODEX 200 over the same window.

Prices are recorded completed 1-minute bars from the runner's own events (it records until 15:00, also after the
entry cutoff), not fills or the official close. Rows are recomputed from the same data each run, so re-running never double counts.
Vibe scores use their published `available_at` and only bars observed by the measurement; older records without that
timestamp are marked time_unverified and never settled. Legacy rows are left intact for non-destructive migration.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS
from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
BENCHMARK = '069500'
HORIZONS = (5, 15, 30)


def day_bars(ledger, day):
    """{code: [(minute, open, high, low, close, observed_at)]} from recorded market_bars."""
    series = {}
    with ro_connect(Path(ledger)) as db:
        rows = db.execute("SELECT time, data FROM events_v2 WHERE time >= ? AND time < ? AND data LIKE '%market_bars%'",
                          (day, (datetime.fromisoformat(day) + timedelta(days=1)).strftime('%Y-%m-%d')))
        for event_time, data in rows:
            try:
                received = datetime.fromisoformat(event_time)
                if received.tzinfo is None or received.utcoffset() is None:
                    received = None
            except (TypeError, ValueError):
                received = None
            for key, bars in (json.loads(data).get('market_bars') or {}).items():
                code = SYMBOLS.get(key, key)
                for bar in bars or []:
                    try:
                        minute = datetime.fromisoformat(f"{day}T{bar['bsop_time'][:2]}:{bar['bsop_time'][2:4]}:00+09:00")
                        values = tuple(float(bar[k]) for k in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr'))
                        observed = received if received is not None and received >= minute + timedelta(minutes=1) else None
                        previous = series.setdefault(code, {}).get(minute)
                        if previous and previous[:4] == values and previous[4] is not None:
                            observed = min(previous[4], observed) if observed is not None else previous[4]
                        series[code][minute] = (*values, observed)
                    except (KeyError, TypeError, ValueError):
                        continue
    return {code: [(m, *v) for m, v in sorted(bars.items())] for code, bars in series.items()}


def close_at(bars, when, *, observed_only=False, max_age=None):
    """Last completed close; observed_only also requires receipt by `when`."""
    index = bisect_right([m for m, *_ in bars], when - timedelta(minutes=1)) - 1
    while index >= 0:
        bar = bars[index]
        if not observed_only or (len(bar) > 5 and bar[5] is not None and bar[5] <= when):
            return bar[4] if max_age is None or when - (bar[0] + timedelta(minutes=1)) <= max_age else None
        index -= 1
    return None


def close_after(bars, when, tolerance=timedelta(minutes=2), *, observed_only=False):
    """First completed close near `when`; observed_only requires receipt within the tolerance."""
    index = bisect_left([m for m, *_ in bars], when - timedelta(minutes=1))
    while index < len(bars) and bars[index][0] + timedelta(minutes=1) - when <= tolerance:
        bar = bars[index]
        if not observed_only or (len(bar) > 5 and bar[5] is not None and bar[5] <= when + tolerance):
            return bar[4]
        index += 1
    return None


def pct(new, old):
    return None if new is None or not old else round((new / old - 1) * 100, 3)


def positions(fills):
    """Episodes per symbol from journal fills [(symbol, side, qty, price, time)], oldest first."""
    open_, episodes = {}, []
    for symbol, side, qty, price, at in fills:
        at = datetime.fromisoformat(at)
        book = open_.get(symbol)
        if side == 'BUY':
            if book is None:
                book = open_[symbol] = {'symbol': symbol, 'opened': at, 'qty': 0, 'cost': 0.0, 'spent': 0.0, 'bought': 0, 'buys': 0,
                                        'sold': 0, 'proceeds': 0.0, 'sells': 0, 'realized': 0.0}
            book['qty'] += qty; book['cost'] += qty * price; book['spent'] += qty * price; book['bought'] += qty; book['buys'] += 1
        elif book is not None:
            average = book['cost'] / book['qty']
            book['realized'] += (price - average) * qty
            book['cost'] -= average * qty; book['qty'] -= qty
            book['sold'] += qty; book['proceeds'] += qty * price; book['sells'] += 1
            if book['qty'] <= 0:
                book['closed'], book['exit_price'] = at, price
                episodes.append(open_.pop(symbol))
    return episodes + list(open_.values())


def path_rows(episodes, bars):
    rows = []
    for book in episodes:
        series = bars.get(book['symbol']) or []
        entry = book['spent'] / book['bought']  # average buy price of the episode
        end = book.get('closed') or datetime.max.replace(tzinfo=KST)
        start = book['opened'].replace(second=0, microsecond=0)
        held = [b for b in series if start <= b[0] < end]  # a bar stamped hh:mm covers hh:mm to hh:mm+1
        after = [b for b in series if b[0] >= end] if book.get('closed') else []
        exit_price = book.get('exit_price')
        rows.append({
            'key': f"{book['symbol']}:{book['opened'].isoformat()}", 'symbol': book['symbol'],
            'opened': book['opened'].isoformat(), 'closed': book['closed'].isoformat() if book.get('closed') else None,
            'status': 'closed' if book.get('closed') else 'open', 'buys': book['buys'], 'sells': book['sells'],
            'bought_qty': book['bought'], 'avg_entry': round(entry, 2), 'realized_krw': round(book['realized']),
            'return_pct': pct(book['proceeds'] / book['sold'], entry) if book['sold'] and book.get('closed') else None,
            'mfe_pct': pct(max(b[2] for b in held), entry) if held else None,
            'mae_pct': pct(min(b[3] for b in held), entry) if held else None,
            'after_30m_pct': pct(close_after(series, end + timedelta(minutes=30)), exit_price) if book.get('closed') else None,
            'after_high_pct': pct(max(b[2] for b in after), exit_price) if after else None,
            'after_last_pct': pct(after[-1][4], exit_price) if after else None,
            'bars_held': len(held)})
    return rows


def decisions(ledger, day, advice=()):
    """(key, time, engine, kind, symbol, choice, value, direction, input_digest) for the day's model views."""
    out = []
    with ro_connect(Path(ledger)) as db:
        rows = db.execute("SELECT time, event, data FROM events_v2 WHERE time >= ? AND time < ? AND event IN ('jev_shadow', 'jev_holding_decision')",
                          (day, (datetime.fromisoformat(day) + timedelta(days=1)).strftime('%Y-%m-%d')))
        for at, event, data in rows:
            data = json.loads(data)
            if event == 'jev_shadow' and data.get('jev_choice') not in (None, 'WAIT'):
                symbol = SYMBOLS.get(data['jev_choice'], data['jev_choice'])
                out.append((f'jev:{at}', at, 'jev', 'entry', symbol, data['jev_choice'], data.get('jev_confidence'), 1,
                            data.get('jev_input_digest')))
            elif event == 'jev_holding_decision' and data.get('symbol') and data.get('choice') in ('HOLD', 'SELL'):
                out.append((f"jev_holding:{at}:{data['symbol']}", at, 'jev', 'holding', data['symbol'], data['choice'],
                            data.get('confidence'), -1 if data['choice'] == 'SELL' else 1, data.get('input_digest')))
    for item in advice:
        if not str(item.get('time', '')).startswith(day):
            continue
        try:
            started = datetime.fromisoformat(item['time'])
            if started.tzinfo is None or started.utcoffset() is None:
                continue
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        try:
            available = datetime.fromisoformat(item['available_at'])
            valid_time = (available.tzinfo is not None and available.utcoffset() is not None
                          and started <= available and available.astimezone(KST).date().isoformat() == day)
        except (KeyError, ValueError, TypeError, OverflowError):
            valid_time = False
        for symbol, value in (item.get('scores') or {}).items():
            score = value.get('score') if isinstance(value, dict) else None
            if type(score) in (int, float) and score != 0:
                key = f"vibe_{'available' if valid_time else 'unverified'}:{item['time']}:{symbol}"
                at = available.isoformat() if valid_time else item['time']
                out.append((key, at, 'vibe', 'score', symbol, None, score,
                            1 if score > 0 else -1, item.get('input_digest')))
    return out


def outcome_rows(views, bars):
    bench = bars.get(BENCHMARK) or []
    rows = []
    for key, at, engine, kind, symbol, choice, value, direction, digest in views:
        when, series = datetime.fromisoformat(at), bars.get(symbol) or []
        unverified = engine == 'vibe' and key.startswith('vibe_unverified:')
        base = None if unverified else close_at(series, when, observed_only=engine == 'vibe',
                                                 max_age=timedelta(minutes=2) if engine == 'vibe' else None)
        bench_base = None if unverified else close_at(bench, when, observed_only=engine == 'vibe',
                                                       max_age=timedelta(minutes=2) if engine == 'vibe' else None)
        row = {'key': key, 'time': at, 'engine': engine, 'kind': kind, 'symbol': symbol, 'choice': choice, 'value': value,
               'direction': direction, 'input_digest': digest, 'price': base}
        for h in HORIZONS:
            row[f'r{h}'] = pct(close_after(series, when + timedelta(minutes=h), observed_only=engine == 'vibe'), base)
            row[f'b{h}'] = pct(close_after(bench, when + timedelta(minutes=h), observed_only=engine == 'vibe'), bench_base)
        row['r_last'] = pct(series[-1][4], base) if series and base else None
        row['b_last'] = pct(bench[-1][4], bench_base) if bench and bench_base else None
        row['status'] = 'time_unverified' if unverified else 'settled' if row['r30'] is not None else 'no_price' if base is None else 'pending'
        rows.append(row)
    return rows


def update(root=ROOT, day=None, research=None, today=None):
    """Recompute the day's rows from the ledger, journal fills and stored vibe advice; returns row counts."""
    root = Path(root)
    today = today or datetime.now(KST).strftime('%Y-%m-%d')
    day = day or today
    research = Path(research or root / 'market-research.sqlite3')
    bars = day_bars(root / 'trading-ledger.sqlite3', day)
    with ro_connect(root / 'orders.sqlite3') as db:
        fills = db.execute('SELECT symbol, side, quantity, price, time FROM fills ORDER BY time').fetchall()
    # every position held on `day` that ended then (or is open today); one opened earlier reads the bars of every held day,
    # and a rerun of an old day leaves a position that ended later as it is
    held = [b for b in positions(fills) if b['opened'].date().isoformat() <= day == (b['closed'].date().isoformat() if b.get('closed') else today)]
    first = min((b['opened'].date() for b in held), default=datetime.fromisoformat(day).date())
    path_bars = {}
    for part in [day_bars(root / 'trading-ledger.sqlite3', (first + timedelta(days=n)).isoformat())
                 for n in range((datetime.fromisoformat(day).date() - first).days)] + [bars]:
        for code, rows in part.items():
            path_bars.setdefault(code, []).extend(rows)
    paths = path_rows(held, path_bars)
    from research_store import history
    advice = history('vibe_advice', 200, research)
    outcomes = outcome_rows(decisions(root / 'trading-ledger.sqlite3', day, advice), bars)
    with sqlite3.connect(research, timeout=5) as db:
        for table, rows in (('position_paths', paths), ('decision_outcomes', outcomes)):
            if not rows:
                continue
            columns = list(rows[0])
            db.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'key' else '') for c in columns)})")
            db.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                           [tuple(r[c] for c in columns) for r in rows])
        try:  # an earlier day gets no more bars, and the watch only recomputes today: its pending outcomes are final
            db.execute("UPDATE decision_outcomes SET status = 'insufficient' WHERE status = 'pending' AND time < ?", (today,))
        except sqlite3.OperationalError:  # no table yet
            pass
    research.chmod(0o600)
    return {'day': day, 'positions': len(paths), 'decisions': len(outcomes),
            'settled': sum(r['status'] == 'settled' for r in outcomes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', help='YYYY-MM-DD (Asia/Seoul); default today')
    args = parser.parse_args()
    print(json.dumps(update(day=args.day), ensure_ascii=False))


if __name__ == '__main__':
    main()
