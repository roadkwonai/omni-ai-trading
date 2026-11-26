"""Read Jev candidate decisions and collect later quotes in an isolated research database."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS, is_code
from jev_program_paper import session_bounds
from sqlite_ro import ro_connect
from src.tools.private_db import private_file

KST = ZoneInfo('Asia/Seoul')
SIGNAL_MAX_AGE = timedelta(seconds=90)
FOLLOW_FOR = timedelta(minutes=15)
MIN_CONFIDENCE = 0.50
SCHEMA = """
CREATE TABLE IF NOT EXISTS signals(
 day TEXT NOT NULL, cycle_id TEXT NOT NULL, symbol TEXT NOT NULL, digest TEXT NOT NULL,
 response_at TEXT NOT NULL, recorded_at TEXT NOT NULL, confidence REAL NOT NULL, model TEXT NOT NULL,
 PRIMARY KEY(day, cycle_id)
);
CREATE TABLE IF NOT EXISTS signal_rejections(
 event_id TEXT NOT NULL, recorded_at TEXT NOT NULL, cycle_id TEXT, symbol TEXT,
 reason TEXT NOT NULL, detected_at TEXT NOT NULL, PRIMARY KEY(event_id, reason)
);
CREATE TABLE IF NOT EXISTS observer_cursors(
 day TEXT NOT NULL, source_id TEXT NOT NULL, last_rowid INTEGER NOT NULL,
 PRIMARY KEY(day,source_id)
);
CREATE TABLE IF NOT EXISTS quote_attempts(
 day TEXT NOT NULL, symbol TEXT NOT NULL, minute TEXT NOT NULL, started_at TEXT NOT NULL,
 completed_at TEXT, status TEXT NOT NULL, error_type TEXT, PRIMARY KEY(day, symbol, minute)
);
CREATE TABLE IF NOT EXISTS quotes(
 day TEXT NOT NULL, symbol TEXT NOT NULL, minute TEXT NOT NULL, source_at TEXT NOT NULL,
 received_at TEXT NOT NULL, bid INTEGER NOT NULL, ask INTEGER NOT NULL, PRIMARY KEY(day, symbol, minute)
);
"""


def stamp(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.utcoffset() is None:
            return None
        return value.astimezone(KST)
    except (ValueError, TypeError, OverflowError):
        return None


def source_signals(ledger, research, now, *, rejections=None, cursor=None):
    query = ("SELECT rowid,time,data FROM events_v2 WHERE event='jev_shadow' AND rowid>? "
             "AND time>=? AND time<? ORDER BY rowid")
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with ro_connect(ledger, timeout=1) as events, ro_connect(research, timeout=1) as inputs:
        for rowid, recorded, raw in events.execute(query, ((cursor or [0])[0], day_start.date().isoformat(),
                                                            (day_start.date() + timedelta(days=1)).isoformat())):
            recorded_at = stamp(recorded)
            if recorded_at is not None and recorded_at > now:
                break
            if cursor is not None:
                cursor[0] = rowid
            record = {}
            def reject(reason):
                if rejections is not None:
                    identity = hashlib.sha256((str(recorded) + '\n' + str(raw)).encode('utf-8')).hexdigest()
                    fields = record if isinstance(record, dict) else {}
                    cycle = fields.get('cycle_id')
                    rejections.append((identity, str(recorded), cycle if isinstance(cycle, str) else None,
                                       str(fields.get('jev_choice') or ''), reason, now.isoformat()))
            try:
                record = json.loads(raw)
                cycle, digest = record.get('cycle_id'), record.get('jev_input_digest')
                response_at, recorded_at = stamp(record.get('jev_response_time')), stamp(recorded)
                confidence = record.get('jev_confidence')
                choice = record.get('jev_choice')
                if choice == 'WAIT':
                    continue
                symbol = SYMBOLS.get(choice, choice) if isinstance(choice, str) else None
                model = record.get('jev_model')
                if (not isinstance(cycle, str) or not cycle or not isinstance(digest, str)
                        or not re.fullmatch(r'[0-9a-f]{64}', digest) or not is_code(symbol)
                        or type(confidence) not in (int, float) or not math.isfinite(confidence)
                        or not 0 <= confidence <= 1 or not isinstance(model, str) or not model):
                    reject('event_invalid')
                    continue
                if confidence < MIN_CONFIDENCE:
                    reject('confidence_below_threshold')
                    continue
                if (response_at is None or recorded_at is None or response_at.date() != now.date()
                        or not response_at <= recorded_at <= now):
                    reject('event_time_mismatch')
                    continue
                if now - response_at > SIGNAL_MAX_AGE:
                    reject('expired_before_observation')
                    continue
                item = inputs.execute("SELECT time,inputs,response FROM model_inputs WHERE digest=? AND engine='jev' AND question='candidate'", (digest,)).fetchone()
                if not item:
                    reject('input_missing')
                    continue
                input_at, data, answer = stamp(item[0]), json.loads(item[1]), json.loads(item[2])
                if input_at is None or not response_at <= input_at <= recorded_at:
                    reject('input_time_mismatch')
                    continue
                if (answer.get('type') != 'choice' or answer.get('choice') != choice
                        or answer.get('confidence') != confidence or choice not in (data.get('criteria') or {})
                        or data.get('model', model) != model):
                    reject('input_content_mismatch')
                    continue
                yield (now.date().isoformat(), cycle, symbol, digest, response_at.isoformat(), recorded_at.isoformat(),
                       float(confidence), model)
            except (ValueError, TypeError, KeyError, AttributeError):
                reject('malformed_record')
                continue


def parse_quote(response, symbol, now, *, after):
    if not isinstance(response, dict) or response.get('status') != 'ok':
        raise ValueError('quote unavailable')
    data = ((response.get('data') or {}).get('Output_0') or {})
    text = data.get('hoga_bsop_hour')
    if isinstance(text, str) and len(text) == 6 and text.isascii() and text.isdigit():
        text = f'{text[:2]}:{text[2:4]}:{text[4:]}'
    if data.get('iem_cd') != symbol or not isinstance(text, str):
        raise ValueError('quote identity or time unavailable')
    try:
        source = datetime.fromisoformat(f'{now.date().isoformat()}T{text}').replace(tzinfo=KST)
    except ValueError:
        raise ValueError('invalid quote time') from None
    bid, ask = data.get('bidp'), data.get('askp')
    if (not after < source <= now or now - source > timedelta(seconds=5)
            or type(bid) is not int or type(ask) is not int or not 0 < bid <= ask):
        raise ValueError('stale or invalid quote')
    return source, bid, ask


def observe(ledger, research, destination, now, reader, *, trading_day, clock=None, watch_symbols=()):
    now = stamp(now)
    if now is None:
        raise ValueError('timezone-aware current time required')
    ledger, research, destination = (Path(path).expanduser().resolve() for path in (ledger, research, destination))
    import os

    runtime = (Path.home() / '.vibe-trading').resolve()
    configured = Path(os.environ.get('VIBE_TRADING_HOME') or runtime).expanduser().resolve()
    if (destination in (ledger, research) or destination == runtime or runtime in destination.parents
            or destination == configured or configured in destination.parents):
        raise ValueError('isolated destination required')
    start, end = session_bounds(now.date())
    if trading_day is not True or now.weekday() >= 5 or not start <= now < end:
        return {'status': 'closed_or_unknown', 'signals_added': 0, 'quotes_added': 0, 'quote_failures': 0}
    if any(not is_code(code) for code in watch_symbols):
        raise ValueError('invalid watched symbol')
    clock = clock or (lambda: datetime.now(KST))
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_file(destination)
    with closing(sqlite3.connect(destination, timeout=1)) as db:
        db.executescript(SCHEMA)
        added = rejected = 0
        try:
            db.execute('BEGIN IMMEDIATE')
            stat = ledger.stat()
            source_id = f'{ledger}:{stat.st_dev}:{stat.st_ino}'
            day = now.date().isoformat()
            current = db.execute('SELECT last_rowid FROM observer_cursors WHERE day=? AND source_id=?',
                                 (day, source_id)).fetchone()
            cursor = [current[0] if current else 0]
            rejections = []
            signals = list(source_signals(ledger, research, now, rejections=rejections, cursor=cursor))
            for row in rejections:
                rejected += db.execute('INSERT OR IGNORE INTO signal_rejections VALUES(?,?,?,?,?,?)', row).rowcount
            for row in signals:
                old = db.execute('SELECT symbol,digest,response_at,recorded_at,confidence,model FROM signals WHERE day=? AND cycle_id=?',
                                 row[:2]).fetchone()
                if old is not None and old != row[2:]:
                    raise ValueError('conflicting signal identity')
                added += db.execute('INSERT OR IGNORE INTO signals VALUES(?,?,?,?,?,?,?,?)', row).rowcount
            db.execute('INSERT INTO observer_cursors VALUES(?,?,?) ON CONFLICT(day,source_id) DO UPDATE SET last_rowid=excluded.last_rowid',
                       (day, source_id, cursor[0]))
            db.commit()
        except Exception:
            db.rollback()
            raise
        tracked = {symbol: stamp(response_at) for symbol, response_at in db.execute(
            'SELECT symbol,MIN(response_at) FROM signals WHERE day=? AND response_at>=? GROUP BY symbol',
            (now.date().isoformat(), (now - FOLLOW_FOR).isoformat()))}
        for symbol in watch_symbols:
            if symbol not in tracked:
                tracked[symbol] = None
        quotes_added = failures = 0
        minute = now.replace(second=0, microsecond=0).isoformat()
        for symbol, signal_time in tracked.items():
            with db:
                claimed = db.execute('INSERT OR IGNORE INTO quote_attempts VALUES(?,?,?,?,NULL,?,NULL)',
                                     (now.date().isoformat(), symbol, minute, now.isoformat(), 'fetching')).rowcount
            if not claimed:
                continue
            try:
                response = reader.call_tool('get_stock_price', {'stock_code': symbol})
                received = stamp(clock())
                if received is None or received.date() != now.date() or not now <= received < end:
                    raise ValueError('invalid received time')
                source, bid, ask = parse_quote(response, symbol, received, after=signal_time or now - timedelta(minutes=1))
                with db:
                    db.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?)',
                               (now.date().isoformat(), symbol, minute, source.isoformat(), received.isoformat(), bid, ask))
                    db.execute("UPDATE quote_attempts SET completed_at=?,status='ok' WHERE day=? AND symbol=? AND minute=?",
                               (received.isoformat(), now.date().isoformat(), symbol, minute))
                quotes_added += 1
            except Exception as exc:
                received = stamp(clock())
                with db:
                    db.execute("UPDATE quote_attempts SET completed_at=?,status='invalid_quote',error_type=? WHERE day=? AND symbol=? AND minute=?",
                               (received.isoformat() if received else None, type(exc).__name__,
                                now.date().isoformat(), symbol, minute))
                failures += 1
        return {'status': 'ok' if not failures else 'partial', 'signals_added': added,
                'rejections_added': rejected, 'quotes_added': quotes_added, 'quote_failures': failures}
