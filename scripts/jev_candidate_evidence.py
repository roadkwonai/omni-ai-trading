"""Isolated, point-in-time one-minute trade-flow evidence. No network access."""

from datetime import date, datetime, timedelta
from contextlib import closing
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from jev_program_paper import program_points, session_bounds
from sqlite_ro import ro_connect


KST = ZoneInfo('Asia/Seoul')
MINUTE = timedelta(minutes=1)
WINDOWS = (1, 3, 5)


def _time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return value.astimezone(KST)


def _symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9A-Za-z]{6}', value):
        raise ValueError('invalid stock code')
    return value


def minute_snapshot(symbol, minute, ticks, *, complete, received_at):
    """Build one KRX minute, with complete indicating continuous feed coverage."""
    _symbol(symbol)
    minute, received = _time(minute), _time(received_at)
    start, end = session_bounds(minute.date())
    if (minute.second or minute.microsecond or not start <= minute < end
            or received < minute + MINUTE or type(complete) is not bool):
        raise ValueError('invalid minute coverage')
    seen = {}
    totals = {'buy_qty': 0, 'sell_qty': 0, 'buy_krw': 0, 'sell_krw': 0}
    for tick in ticks:
        source, arrived = _time(tick.get('source_at')), _time(tick.get('received_at'))
        identifier, price, quantity = tick.get('id'), tick.get('price'), tick.get('signed_qty')
        if (tick.get('venue') != 'KRX' or not isinstance(identifier, str) or not identifier
                or not minute <= source < minute + MINUTE or not source <= arrived <= received
                or type(price) is not int or price <= 0 or type(quantity) is not int or quantity == 0):
            raise ValueError('invalid KRX trade')
        value = (source, arrived, price, quantity)
        if identifier in seen:
            if seen[identifier] != value:
                raise ValueError('conflicting trade identity')
            continue
        seen[identifier] = value
        side = 'buy' if quantity > 0 else 'sell'
        totals[f'{side}_qty'] += abs(quantity)
        totals[f'{side}_krw'] += abs(quantity) * price
    return {'day': minute.date().isoformat(), 'symbol': symbol, 'venue': 'KRX',
            'minute': minute.isoformat(), 'received_at': received.isoformat(), 'complete': complete, **totals}


_COLUMNS = ('day', 'symbol', 'venue', 'minute', 'received_at', 'complete',
            'buy_qty', 'sell_qty', 'buy_krw', 'sell_krw')


def store_minutes(path, observations):
    """Store a batch transactionally. Repeating the same snapshot changes nothing."""
    values = []
    for row in observations:
        minute = _time(datetime.fromisoformat(row['minute']))
        received = _time(datetime.fromisoformat(row['received_at']))
        _symbol(row['symbol'])
        start, end = session_bounds(minute.date())
        if (row['day'] != minute.date().isoformat() or row['venue'] != 'KRX'
                or minute.second or minute.microsecond or not start <= minute < end or received < minute + MINUTE
                or type(row['complete']) is not bool
                or any(type(row[key]) is not int or row[key] < 0 for key in _COLUMNS[-4:])):
            raise ValueError('invalid minute snapshot')
        values.append((row['day'], row['symbol'], row['venue'], minute.isoformat(), received.isoformat(),
                       row['complete'], *(row[key] for key in _COLUMNS[-4:])))
    if not values:
        return 0
    with closing(sqlite3.connect(path, timeout=1)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS candidate_minutes (
            day TEXT NOT NULL, symbol TEXT NOT NULL, venue TEXT NOT NULL, minute TEXT NOT NULL,
            received_at TEXT NOT NULL, complete INTEGER NOT NULL, buy_qty INTEGER NOT NULL,
            sell_qty INTEGER NOT NULL, buy_krw INTEGER NOT NULL, sell_krw INTEGER NOT NULL,
            PRIMARY KEY(day, symbol, venue, minute, received_at)) WITHOUT ROWID''')
        before = db.total_changes
        for value in values:
            existing = db.execute('SELECT complete,buy_qty,sell_qty,buy_krw,sell_krw FROM candidate_minutes '
                                  'WHERE day=? AND symbol=? AND venue=? AND minute=? AND received_at=?', value[:5]).fetchone()
            if existing is not None and existing != value[5:]:
                raise ValueError('conflicting minute snapshot')
            db.execute('INSERT OR IGNORE INTO candidate_minutes VALUES (?,?,?,?,?,?,?,?,?,?)', value)
        return db.total_changes - before


def flow_at(path, session_day, symbol, at):
    """Read latest snapshots received by at, for the last completed 1, 3 and 5 minutes."""
    at = _time(at)
    _symbol(symbol)
    if date.fromisoformat(session_day) != at.date():
        raise ValueError('session day mismatch')
    end = at.replace(second=0, microsecond=0)
    start = end - max(WINDOWS) * MINUTE
    try:
        with ro_connect(path, timeout=1) as db:
            rows = db.execute('''SELECT m.minute, m.received_at, m.complete, m.buy_qty, m.sell_qty, m.buy_krw, m.sell_krw
                FROM candidate_minutes m WHERE m.day=? AND m.symbol=? AND m.venue='KRX' AND m.minute>=? AND m.minute<?
                AND m.received_at=(SELECT MAX(v.received_at) FROM candidate_minutes v
                    WHERE v.day=m.day AND v.symbol=m.symbol AND v.venue=m.venue AND v.minute=m.minute AND v.received_at<=?)
                ORDER BY m.minute''',
                              (session_day, symbol, start.isoformat(), end.isoformat(), at.isoformat())).fetchall()
    except sqlite3.OperationalError as exc:
        if not Path(path).exists() or 'no such table' in str(exc):
            rows = []
        else:
            raise
    latest = {row[0]: row for row in rows}
    windows = {}
    for window in WINDOWS:
        buckets = [latest.get((end - i * MINUTE).isoformat()) for i in range(window, 0, -1)]
        if not all(row is not None and row[2] for row in buckets):
            windows[window] = {'status': 'coverage_missing'}
            continue
        windows[window] = {'status': 'ready', **{key: sum(row[index] for row in buckets)
                            for index, key in enumerate(('buy_qty', 'sell_qty', 'buy_krw', 'sell_krw'), 3)}}
    return {'session_day': session_day, 'symbol': symbol, 'venue': 'KRX', 'as_of': at.isoformat(), 'windows': windows}


def store_program(path, symbol, session_day, rows, received_at):
    """Store a received KRX cumulative-share capture without requesting the broker."""
    _symbol(symbol)
    day, received = date.fromisoformat(session_day), _time(received_at)
    if received.date() != day:
        raise ValueError('session day mismatch')
    points = program_points(rows, day)
    if any(point.at > received for point in points):
        raise ValueError('program point arrives before source time')
    if not points:
        return 0
    with closing(sqlite3.connect(path, timeout=1)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS candidate_program (
            day TEXT NOT NULL, symbol TEXT NOT NULL, venue TEXT NOT NULL, source_at TEXT NOT NULL,
            received_at TEXT NOT NULL, buy_qty INTEGER NOT NULL, sell_qty INTEGER NOT NULL,
            PRIMARY KEY(day, symbol, venue, source_at, received_at)) WITHOUT ROWID''')
        before = db.total_changes
        for point in points:
            key = (session_day, symbol, 'KRX', point.at.isoformat(), received.isoformat())
            values = (point.bought, point.sold)
            existing = db.execute('SELECT buy_qty,sell_qty FROM candidate_program '
                                  'WHERE day=? AND symbol=? AND venue=? AND source_at=? AND received_at=?', key).fetchone()
            if existing is not None and existing != values:
                raise ValueError('conflicting program snapshot')
            db.execute('INSERT OR IGNORE INTO candidate_program VALUES (?,?,?,?,?,?,?)', (*key, *values))
        return db.total_changes - before


def program_at(path, session_day, symbol, at):
    """Return a five-minute cumulative-share change using only received observations."""
    at = _time(at)
    _symbol(symbol)
    if date.fromisoformat(session_day) != at.date():
        raise ValueError('session day mismatch')
    end = at.replace(second=0, microsecond=0)
    missing = {'status': 'program_missing_or_stale'}
    try:
        with ro_connect(path, timeout=1) as db:
            rows = db.execute('''SELECT m.source_at,m.buy_qty,m.sell_qty FROM candidate_program m
                WHERE m.day=? AND m.symbol=? AND m.venue='KRX' AND m.source_at>=? AND m.source_at<=?
                AND m.received_at=(SELECT MAX(v.received_at) FROM candidate_program v
                    WHERE v.day=m.day AND v.symbol=m.symbol AND v.venue=m.venue AND v.source_at=m.source_at
                    AND v.received_at<=?) ORDER BY m.source_at''',
                (session_day, symbol, (end - 6 * MINUTE).isoformat(), end.isoformat(), at.isoformat())).fetchall()
    except sqlite3.OperationalError as exc:
        if not Path(path).exists() or 'no such table' in str(exc):
            return missing
        raise
    if not rows:
        return missing
    last = rows[-1]
    last_at = datetime.fromisoformat(last[0])
    candidates = [row for row in rows if datetime.fromisoformat(row[0]) <= end - 5 * MINUTE]
    if end - last_at > MINUTE or not candidates:
        return missing
    first = candidates[-1]
    first_at = datetime.fromisoformat(first[0])
    if end - 5 * MINUTE - first_at > MINUTE:
        return missing
    if abs((last_at - first_at) - 5 * MINUTE) > timedelta(seconds=30):
        return {'status': 'program_window_misaligned'}
    relevant = [row for row in rows if first[0] <= row[0] <= last[0]]
    if any(after[1] < before[1] or after[2] < before[2] for before, after in zip(relevant, relevant[1:])):
        return {'status': 'program_cumulative_reset'}
    buy, sell = last[1] - first[1], last[2] - first[2]
    return {'status': 'ready', 'program_from': first[0], 'program_as_of': last[0],
            'recent_buy_shares': buy, 'recent_sell_shares': sell, 'recent_net_shares': buy - sell}
