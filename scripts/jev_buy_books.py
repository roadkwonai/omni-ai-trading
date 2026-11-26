"""Two isolated, transactionally updated virtual books for Jev research.

Quotes are observed data, fills assume full execution at the best quote. Costs are
an explicit combined fee, tax and slippage assumption, in basis points per side.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from etf_universe import is_code

KST = ZoneInfo('Asia/Seoul')
ARMS = ('baseline', 'experience')
INITIAL_KRW = 1_000_000
TRANCHE_KRW = 500_000
COST_BPS = 15
SCALE = 10_000
MAX_SIGNAL_AGE = timedelta(seconds=60)
MAX_QUOTE_AGE = timedelta(seconds=5)
MAX_MARK_AGE = timedelta(seconds=30)
MARKER = 'jev_buy_books_v1'
IDENTIFIER = re.compile(r'[\w.:-]{1,200}\Z')


def _time(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(KST)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('invalid timestamp') from None


def _path(path):
    original = Path(path).expanduser()
    if original.is_symlink():
        raise ValueError('symlink database is not allowed')
    path = original.resolve()
    runtime = (Path.home() / '.vibe-trading' / 'nhplug').resolve()
    configured = os.environ.get('VIBE_TRADING_HOME')
    roots = [runtime]
    if configured:
        roots.append((Path(configured).expanduser() / 'nhplug').resolve())
    if any(path == root or root in path.parents for root in roots):
        raise ValueError('operational database path is not allowed')
    return path


def _connect(path):
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(descriptor)
    db = sqlite3.connect(path, timeout=5, isolation_level=None)
    try:
        db.execute('BEGIN IMMEDIATE')
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if names and 'meta' not in names:
            raise ValueError('database is not an isolated research book')
        if 'meta' in names:
            marker = db.execute("SELECT value FROM meta WHERE name='purpose'").fetchone()
            if marker != (MARKER,):
                raise ValueError('database is not an isolated research book')
        else:
            db.execute('CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT INTO meta VALUES (?,?)', ('purpose', MARKER))
            db.execute('CREATE TABLE accounts (arm TEXT PRIMARY KEY, cash_units INTEGER NOT NULL)')
            db.executemany('INSERT INTO accounts VALUES (?,?)', [(arm, INITIAL_KRW * SCALE) for arm in ARMS])
            db.execute('''CREATE TABLE observations (event_id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                symbol TEXT NOT NULL, source_at TEXT NOT NULL, received_at TEXT NOT NULL, bid INTEGER NOT NULL, ask INTEGER NOT NULL)''')
            db.execute('CREATE INDEX observations_symbol_time ON observations(symbol,received_at)')
            db.execute('''CREATE TABLE signals (arm TEXT NOT NULL, signal_id TEXT NOT NULL, symbol TEXT NOT NULL,
                action TEXT NOT NULL, decided_at TEXT NOT NULL, PRIMARY KEY(arm,signal_id))''')
            db.execute('''CREATE TABLE positions (arm TEXT NOT NULL, symbol TEXT NOT NULL, qty INTEGER NOT NULL,
                entry INTEGER NOT NULL, signal_id TEXT NOT NULL, opened_at TEXT NOT NULL, PRIMARY KEY(arm,symbol))''')
            db.execute('''CREATE TABLE fills (arm TEXT NOT NULL, event_id TEXT NOT NULL, symbol TEXT NOT NULL,
                side TEXT NOT NULL, qty INTEGER NOT NULL, price INTEGER NOT NULL, cost_units INTEGER NOT NULL,
                at TEXT NOT NULL, signal_id TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(arm,event_id))''')
            db.execute('''CREATE TABLE marks (arm TEXT NOT NULL, event_id TEXT NOT NULL, at TEXT NOT NULL,
                value_units INTEGER, PRIMARY KEY(arm,event_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS comparison_cases (
            signal_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS signal_outcomes (
            arm TEXT NOT NULL, signal_id TEXT NOT NULL, outcome TEXT NOT NULL, PRIMARY KEY(arm,signal_id))''')
        return db
    except Exception:
        db.rollback()
        db.close()
        raise


def _decision(decision, source, symbol):
    if not isinstance(decision, dict) or set(decision) != {'action', 'signal_id', 'decided_at'}:
        raise ValueError('invalid decision')
    action, signal = decision['action'], decision['signal_id']
    decided = _time(decision['decided_at'])
    if (action not in ('BUY', 'WAIT') or not isinstance(signal, str) or not IDENTIFIER.fullmatch(signal)
            or decided.date() != source.date() or not timedelta(0) < source - decided <= MAX_SIGNAL_AGE):
        raise ValueError('decision or quote is stale')
    return action, signal, symbol, decided.isoformat()


def _mark(db, arm, now):
    cash = db.execute('SELECT cash_units FROM accounts WHERE arm=?', (arm,)).fetchone()[0]
    for symbol, qty in db.execute('SELECT symbol,qty FROM positions WHERE arm=?', (arm,)):
        quote = db.execute('SELECT source_at,bid FROM observations WHERE symbol=? AND received_at<=? '
                           'ORDER BY received_at DESC LIMIT 1', (symbol, now.isoformat())).fetchone()
        if not quote or not timedelta(0) <= now - _time(quote[0]) <= MAX_MARK_AGE:
            return None
        cash += qty * quote[1] * (SCALE - COST_BPS)
    return cash


def apply_quote(path, *, event_id, symbol, source_at, received_at, bid, ask, decisions=None, final=False):
    return _apply_quote(path, event_id=event_id, symbol=symbol, source_at=source_at, received_at=received_at,
                        bid=bid, ask=ask, decisions=decisions, final=final, bootstrap=False)


def apply_bootstrap_quote(path, *, event_id, symbol, source_at, received_at, bid, ask, decision):
    """Record an unpaired baseline fill while the experience arm is unavailable."""
    if not isinstance(decision, dict) or decision.get('action') != 'BUY':
        raise ValueError('bootstrap requires a baseline BUY')
    return _apply_quote(path, event_id=event_id, symbol=symbol, source_at=source_at, received_at=received_at,
                        bid=bid, ask=ask, decisions={'baseline': decision}, final=False, bootstrap=True)


def _apply_quote(path, *, event_id, symbol, source_at, received_at, bid, ask, decisions, final, bootstrap):
    source, received = _time(source_at), _time(received_at)
    if (not isinstance(event_id, str) or not IDENTIFIER.fullmatch(event_id) or not is_code(symbol)
            or type(bid) is not int or type(ask) is not int or not 0 < bid <= ask
            or source.date() != received.date() or not timedelta(0) <= received - source <= MAX_QUOTE_AGE
            or type(final) is not bool):
        raise ValueError('invalid quote')
    parsed = None
    if decisions is not None:
        required = {'baseline'} if bootstrap else set(ARMS)
        if not isinstance(decisions, dict) or set(decisions) != required:
            raise ValueError('invalid decision arms')
        parsed = {arm: _decision(decisions[arm], source, symbol) for arm in required}
    payload = [symbol, source.isoformat(), received.isoformat(), bid, ask, parsed, final]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with closing(_connect(path)) as db:
        try:
            existing = db.execute('SELECT digest FROM observations WHERE event_id=?', (event_id,)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError('conflicting event identity')
                db.commit()
                return 'duplicate'
            latest = db.execute('SELECT MAX(received_at) FROM observations').fetchone()[0]
            if latest and received <= _time(latest):
                raise ValueError('out-of-order quote')
            db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?)',
                       (event_id, digest, symbol, source.isoformat(), received.isoformat(), bid, ask))
            if parsed:
                signal_id = parsed['baseline'][1]
                status = 'unavailable' if bootstrap else 'paired'
                existing_case = db.execute('SELECT status FROM comparison_cases WHERE signal_id=?', (signal_id,)).fetchone()
                if existing_case and existing_case[0] != status:
                    raise ValueError('conflicting comparison identity')
                if not existing_case:
                    db.execute('INSERT INTO comparison_cases VALUES (?,?,?)', (signal_id, event_id, status))
            for arm in ARMS:
                position = db.execute('SELECT qty,entry,signal_id,opened_at FROM positions WHERE arm=? AND symbol=?',
                                      (arm, symbol)).fetchone()
                signal = parsed.get(arm) if parsed else None
                if signal:
                    action, signal_id, _, decided = signal
                    old = db.execute('SELECT symbol,action,decided_at FROM signals WHERE arm=? AND signal_id=?',
                                     (arm, signal_id)).fetchone()
                    if old and old != (symbol, action, decided):
                        raise ValueError('conflicting signal identity')
                    if not old:
                        db.execute('INSERT INTO signals VALUES (?,?,?,?,?)', (arm, signal_id, symbol, action, decided))
                        outcome = 'wait'
                        if action == 'BUY':
                            if position:
                                outcome = 'already_held'
                            elif final:
                                outcome = 'session_closed'
                            else:
                                cash = db.execute('SELECT cash_units FROM accounts WHERE arm=?', (arm,)).fetchone()[0]
                                held = db.execute('SELECT COUNT(*) FROM positions WHERE arm=?', (arm,)).fetchone()[0]
                                qty = min(cash, TRANCHE_KRW * SCALE) // (ask * (SCALE + COST_BPS)) if not held else 0
                                outcome = 'position_limit' if held else 'amount_limit'
                                if qty:
                                    outcome = 'filled'
                                    cost = qty * ask * COST_BPS
                                    db.execute('UPDATE accounts SET cash_units=cash_units-? WHERE arm=?',
                                               (qty * ask * SCALE + cost, arm))
                                    db.execute('INSERT INTO positions VALUES (?,?,?,?,?,?)',
                                               (arm, symbol, qty, ask, signal_id, received.isoformat()))
                                    db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?)',
                                               (arm, event_id, symbol, 'BUY', qty, ask, cost, received.isoformat(), signal_id, 'entry'))
                        db.execute('INSERT INTO signal_outcomes VALUES (?,?,?)', (arm, signal_id, outcome))
                if position:
                    qty, entry, signal_id, _ = position
                    reason = ('stop_loss' if bid * 100 <= entry * 99 else
                              'take_profit' if bid * 100 >= entry * 102 else 'session_close' if final else None)
                    if reason:
                        cost = qty * bid * COST_BPS
                        db.execute('UPDATE accounts SET cash_units=cash_units+? WHERE arm=?',
                                   (qty * bid * SCALE - cost, arm))
                        db.execute('DELETE FROM positions WHERE arm=? AND symbol=?', (arm, symbol))
                        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?,?)',
                                   (arm, event_id, symbol, 'SELL', qty, bid, cost, received.isoformat(), signal_id, reason))
                db.execute('INSERT INTO marks VALUES (?,?,?,?)', (arm, event_id, received.isoformat(), _mark(db, arm, received)))
            db.commit()
            return 'applied'
        except Exception:
            db.rollback()
            raise


def report(path):
    with closing(_connect(path)) as db:
        try:
            result = {'cost_bps_per_side': COST_BPS, 'cost_model': 'combined_fee_tax_slippage_assumption',
                      'comparison_cases': dict(db.execute('SELECT status,COUNT(*) FROM comparison_cases GROUP BY status'))}
            for arm in ARMS:
                holdings = {s: {'qty': q, 'entry': p, 'opened_at': t} for s, q, p, t in db.execute(
                    'SELECT symbol,qty,entry,opened_at FROM positions WHERE arm=?', (arm,))}
                fills = db.execute('SELECT side,qty,price,cost_units,reason FROM fills WHERE arm=? ORDER BY at,rowid', (arm,)).fetchall()
                closed = db.execute('''SELECT b.qty,b.price,s.price,b.cost_units,s.cost_units FROM fills b
                    JOIN fills s ON s.arm=b.arm AND s.symbol=b.symbol AND s.signal_id=b.signal_id
                    WHERE b.arm=? AND b.side='BUY' AND s.side='SELL' ''', (arm,)).fetchall()
                realized = sum((sell - buy) * qty - (buy_cost + sell_cost) / SCALE
                               for qty, buy, sell, buy_cost, sell_cost in closed)
                peak, max_loss, missing = INITIAL_KRW * SCALE, 0, 0
                max_pct = 0.0
                for (value,) in db.execute('SELECT value_units FROM marks WHERE arm=? ORDER BY at,rowid', (arm,)):
                    if value is None:
                        missing += 1
                        continue
                    peak = max(peak, value)
                    max_loss = max(max_loss, peak - value)
                    max_pct = max(max_pct, 100 * (peak - value) / peak)
                latest = db.execute('SELECT value_units FROM marks WHERE arm=? ORDER BY at DESC,rowid DESC LIMIT 1',
                                    (arm,)).fetchone()
                result[arm] = {'cash_krw': db.execute('SELECT cash_units FROM accounts WHERE arm=?', (arm,)).fetchone()[0] / SCALE,
                               'holdings': holdings, 'fills': len(fills), 'realized_krw': realized,
                               'latest_equity_krw': latest[0] / SCALE if latest and latest[0] is not None else None,
                               'sampled_max_drawdown_krw': max_loss / SCALE, 'sampled_max_drawdown_pct': max_pct,
                               'missing_equity_marks': missing, 'drawdown_complete': bool(latest) and missing == 0,
                               'exit_reasons': dict(Counter(reason for side, _, _, _, reason in fills if side == 'SELL')),
                               'signal_outcomes': dict(db.execute('SELECT outcome,COUNT(*) FROM signal_outcomes WHERE arm=? GROUP BY outcome',
                                                                  (arm,)))}
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def export_samples(path):
    """Return closed position outcomes for later, separately versioned research."""
    with closing(_connect(path)) as db:
        try:
            rows = db.execute('''SELECT b.arm,b.symbol,b.signal_id,b.at,s.at,b.qty,b.price,s.price,b.cost_units,s.cost_units,c.status
                FROM fills b JOIN fills s ON s.arm=b.arm AND s.symbol=b.symbol AND s.signal_id=b.signal_id
                LEFT JOIN comparison_cases c ON c.signal_id=b.signal_id
                WHERE b.side='BUY' AND s.side='SELL' ORDER BY s.at''').fetchall()
            result = [{'arm': arm, 'symbol': symbol, 'signal_id': signal, 'opened_at': opened, 'closed_at': closed,
                       'qty': qty, 'net_return_pct': round(100 * ((sell - buy) * qty - (buy_cost + sell_cost) / SCALE)
                                                         / (qty * buy), 6),
                       'cost_bps_per_side': COST_BPS, 'label_type': 'virtual_fill', 'comparison': comparison or 'unknown'}
                      for arm, symbol, signal, opened, closed, qty, buy, sell, buy_cost, sell_cost, comparison in rows]
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
