"""Retrospective paper-price observations, not profit or model training."""
from __future__ import annotations
import argparse
import json
import os
import re
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from trading_ledger import DATABASE, ROOT
from etf_universe import SYMBOLS as ETF_SYMBOLS, is_code
from src.tools.private_db import private_file

OUTPUT = ROOT / 'paper-observations.sqlite3'
KST = ZoneInfo('Asia/Seoul')
SYMBOLS = tuple(ETF_SYMBOLS) + ('SAMSUNG', 'NAVER', 'KAKAO')
HORIZON = timedelta(minutes=5)
MAX_LAG = timedelta(minutes=2)
BAR_MARGIN = timedelta(minutes=2)


def universe_for(groups):
    names = set(groups) if isinstance(groups, dict) else set()
    if set(ETF_SYMBOLS) <= names and all(n in ETF_SYMBOLS or is_code(n) for n in names):
        return 'etf7'  # scanner stocks ride along under their codes; only ETF series are learned
    if names == {'K200', 'INV'}:
        return 'legacy2'
    if names == {'K200', 'INV', 'LEV', 'SAMSUNG', 'NAVER', 'KAKAO'}:
        return 'legacy6'
    return 'legacy_other'


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError('timezone missing')
        return result.astimezone(KST)
    except (ValueError, TypeError) as exc:
        raise ValueError('invalid event time') from exc


def bars(event):
    observed = timestamp(event['time'])
    output = []
    groups = event.get('market_bars')
    if not isinstance(groups, dict):
        return output
    for symbol in SYMBOLS:
        rows = groups.get(symbol, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            try:
                clock = str(row['bsop_time'])
                if len(clock) != 6 or not clock.isascii() or not clock.isdigit():
                    continue
                bar = observed.replace(hour=int(clock[:2]), minute=int(clock[2:4]), second=int(clock[4:]), microsecond=0)
                close = Decimal(str(row['stck_prpr']))
                if not close.is_finite() or close <= 0 or bar > observed - BAR_MARGIN:
                    continue
                output.append((symbol, bar, observed, close))
            except (KeyError, ValueError, InvalidOperation, TypeError):
                continue
    return output


def evaluate(ledger, output):
    ledger, output = Path(ledger), Path(output)
    if not ledger.is_file():
        raise FileNotFoundError('ledger not initialized')
    if ledger.resolve() == output.resolve():
        raise ValueError('output must differ from source ledger')
    # An observation reads only bars of its own day, so every day before the previous recorded one is final: the watch
    # re-reads two days a minute, not the whole ledger. One day earlier in SQL in case a time carries another offset.
    since = None
    if output.is_file():
        with sqlite3.connect(output) as db:
            try:
                days = [d for (d,) in db.execute('SELECT DISTINCT substr(decision_time, 1, 10) FROM price_observations ORDER BY 1 DESC LIMIT 2')]
            except sqlite3.OperationalError:  # no table yet
                days = []
        since = days[1] if len(days) == 2 else None
    query = "SELECT source, offset, event, time, data FROM events_v2 WHERE event IN ('decision','market_context')"
    floor = (datetime.fromisoformat(since) - timedelta(days=1)).date().isoformat() if since else ''
    with sqlite3.connect(f'{ledger.resolve().as_uri()}?mode=ro', uri=True) as db:
        source_rows = db.execute(query + ' AND time >= ? ORDER BY time, source, offset', (floor,)).fetchall()
    events = []
    observations = {}
    for source, offset, kind, ledger_time, raw in source_rows:
        event = json.loads(raw)
        at = timestamp(event['time'])
        if since and at.date().isoformat() < since:
            continue
        groups = event.get('market_bars')
        present = set(groups) & set(SYMBOLS) if isinstance(groups, dict) else set()
        universe = universe_for(groups)
        if kind == 'market_context':
            if (event.get('event') != kind or event.get('context') not in ('entry', 'holding')
                    or event.get('universe') != 'etf7' or universe != 'etf7'
                    or at != timestamp(ledger_time)
                    or not isinstance(event.get('cycle_id'), str)
                    or not re.fullmatch(r'[0-9a-f]{32}', event['cycle_id'])
                    or (event.get('context') == 'holding' and event.get('symbol') not in ETF_SYMBOLS.values())
                    or event.get('choice') is not None):
                continue
            universe = 'etf7_jev_only'
        event_bars = bars(event)
        if kind == 'decision' or event.get('context') == 'entry':
            events.append((source, offset, at, event.get('choice') if kind == 'decision' else None,
                           event_bars, present, universe))
        for symbol, bar, observed, close in event_bars:
            symbol_observations = observations.setdefault((universe, symbol), {})
            # A changed value for the same source bar is ambiguous, not evidence.
            old = symbol_observations.get(bar)
            if old is None:
                symbol_observations[bar] = (observed, close)
            elif old[1] != close:
                symbol_observations[bar] = (min(old[0], observed), None)
            elif observed < old[0]:
                symbol_observations[bar] = (observed, close)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(output)  # src.tools.private_db: never os.open an existing database
    ordered = {key: sorted(series) for key, series in observations.items()}  # bisect the 5-7 minute window, not every day's bars
    with sqlite3.connect(output) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS price_observations (
            source TEXT NOT NULL, offset INTEGER NOT NULL, symbol TEXT NOT NULL,
            decision_time TEXT NOT NULL, choice TEXT, baseline_time TEXT, baseline_close TEXT,
            future_time TEXT, future_seen_at TEXT, future_close TEXT, change_pct TEXT,
            status TEXT NOT NULL, PRIMARY KEY(source, offset, symbol))''')
        if 'universe' not in {row[1] for row in db.execute('PRAGMA table_info(price_observations)')}:
            db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
        for source, offset, at, choice, event_bars, present, universe in events:
            # Older versions created unavailable symbols as insufficient; retain and relabel those rows.
            missing = set(SYMBOLS) - present
            for symbol in missing:
                db.execute("UPDATE price_observations SET status='not_recorded',universe=? WHERE source=? AND offset=? AND symbol=?",
                           (universe, source, offset, symbol))
            for symbol in sorted(present):
                baseline = [(bar, close) for name, bar, seen, close in event_bars
                            if name == symbol and seen == at and bar <= at - BAR_MARGIN]
                start = max(baseline, default=None, key=lambda pair: pair[0])
                series, times = observations.get((universe, symbol), {}), ordered.get((universe, symbol), [])
                window = times[bisect_left(times, at + HORIZON):bisect_right(times, at + HORIZON + MAX_LAG)]
                later = [(bar, *series[bar]) for bar in window if series[bar][0] > at and series[bar][0] >= bar + BAR_MARGIN
                         and bar.date() == at.date() and series[bar][1] is not None]
                end = min(later, default=None, key=lambda item: item[0])
                baseline_values = {close for bar, close in baseline if start and bar == start[0]}
                valid = (start is not None and len(baseline_values) == 1 and end is not None
                         and at - start[0] <= timedelta(minutes=4))
                status = 'observed' if valid else 'insufficient'
                change = str(((end[2] / start[1]) - 1) * 100) if valid else None
                values = (source, offset, symbol, at.isoformat(), choice,
                          start[0].isoformat() if start else None, str(start[1]) if start else None,
                          end[0].isoformat() if end else None, end[1].isoformat() if end else None,
                          str(end[2]) if end else None, change, status, universe)
                db.execute('''INSERT INTO price_observations (source,offset,symbol,decision_time,choice,baseline_time,baseline_close,
                    future_time,future_seen_at,future_close,change_pct,status,universe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,offset,symbol) DO UPDATE SET
                    baseline_time=excluded.baseline_time, baseline_close=excluded.baseline_close,
                    future_time=excluded.future_time, future_seen_at=excluded.future_seen_at,
                    future_close=excluded.future_close, change_pct=excluded.change_pct,status=excluded.status,
                    universe=excluded.universe''', values)
        counts = {'observed': 0, 'insufficient': 0}  # over every stored event, as the full read counted them
        counts.update(db.execute("SELECT status, COUNT(*) FROM price_observations WHERE status IN ('observed', 'insufficient') GROUP BY status"))
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('evaluate', 'summary'))
    args = parser.parse_args()
    if args.action == 'evaluate':
        print(json.dumps(evaluate(DATABASE, OUTPUT), ensure_ascii=False))
    else:
        if not OUTPUT.is_file():
            raise SystemExit('observations not initialized')
        with sqlite3.connect(f'{OUTPUT.resolve().as_uri()}?mode=ro', uri=True) as db:
            print(json.dumps(dict(db.execute('SELECT status, COUNT(*) FROM price_observations GROUP BY status')), ensure_ascii=False))

if __name__ == '__main__':
    main()
