"""Coordinate isolated Jev BUY observations, paired judgments and virtual books."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from jev_experience import build, publish, read
from src.tools.private_db import private_file

KST = ZoneInfo('Asia/Seoul')
SIGNAL_LIFETIME = timedelta(seconds=90)


def advance(path, signal, now, judge):
    """Claim one cycle before an external request; unknown outcomes are never resent."""
    if now.utcoffset() is None:
        raise ValueError('timezone required')
    private_file(Path(path))
    with closing(sqlite3.connect(path, timeout=2)) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS requests (
            day TEXT NOT NULL, cycle_id TEXT NOT NULL, symbol TEXT NOT NULL, digest TEXT NOT NULL,
            status TEXT NOT NULL, started_at TEXT NOT NULL, result TEXT, error_type TEXT,
            PRIMARY KEY(day, cycle_id))''')
        identity = (signal['day'], signal['cycle_id'], signal['symbol'], signal['digest'])
        with db:
            inserted = db.execute('INSERT OR IGNORE INTO requests VALUES(?,?,?,?,?,?,NULL,NULL)',
                                  (*identity, 'request_unknown', now.isoformat())).rowcount
        if not inserted:
            row = db.execute('SELECT symbol,digest,status,result,error_type FROM requests WHERE day=? AND cycle_id=?',
                             identity[:2]).fetchone()
            if row[:2] != identity[2:]:
                raise ValueError('cycle identity changed')
            return {'status': row[2], 'result': json.loads(row[3]) if row[3] else None, 'error_type': row[4]}
        at = datetime.fromisoformat(signal['response_at'])
        if at.utcoffset() is None or not timedelta(0) <= now - at < SIGNAL_LIFETIME:
            with db:
                db.execute("UPDATE requests SET status='expired' WHERE day=? AND cycle_id=?", identity[:2])
            return {'status': 'expired'}
        try:
            result = judge(signal)
            payload = json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except Exception as exc:
            with db:
                db.execute('UPDATE requests SET error_type=? WHERE day=? AND cycle_id=?',
                           (type(exc).__name__, *identity[:2]))
            return {'status': 'request_unknown', 'error_type': type(exc).__name__}
        with db:
            db.execute("UPDATE requests SET status='complete',result=? WHERE day=? AND cycle_id=?", (payload, *identity[:2]))
        return {'status': 'complete', 'result': result}


def refresh_experience(root, now, samples, *, market='KOSDAQ', route='watchlist_strength'):
    """Publish a fixed daily experience using only previously completed samples."""
    if now.utcoffset() is None:
        raise ValueError('timezone required')
    now = now.astimezone(KST)
    folder = Path(root) / now.date().isoformat()
    current = folder / 'current.json'
    if not current.exists():
        prior = [s for s in samples if s.get('day', '') < now.date().isoformat()
                 and datetime.fromisoformat(s['outcome_available_at']) <= now]
        artifact = build(prior, published_at=now, eligible_from=now,
                         expires_at=now.replace(hour=23, minute=59, second=59, microsecond=0))
        publish(artifact, folder)
    return read(folder, at=now, purpose='candidate', market=market, route=route)


def record_status(path, signal, status):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('UPDATE requests SET status=? WHERE day=? AND cycle_id=?',
                   (status, signal['day'], signal['cycle_id']))


def record_incident(path, signal, now, stage, error_type):
    private_file(Path(path))
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY, day TEXT NOT NULL, cycle_id TEXT NOT NULL,
            time TEXT NOT NULL, stage TEXT NOT NULL, error_type TEXT NOT NULL,
            UNIQUE(day,cycle_id,stage,error_type))''')
        db.execute('INSERT OR IGNORE INTO incidents(day,cycle_id,time,stage,error_type) VALUES(?,?,?,?,?)',
                   (signal['day'], signal['cycle_id'], now.isoformat(), stage, error_type))


def experiment_frame(ledger, research, signal):
    from jev_learning_replay import candidate_market
    from position_paths import day_bars_from_connection
    from sqlite_ro import ro_connect

    at = datetime.fromisoformat(signal['response_at'])
    with ro_connect(research) as db:
        row = db.execute("SELECT time,inputs FROM model_inputs WHERE digest=? AND engine='jev' AND question='candidate'",
                         (signal['digest'],)).fetchone()
    if not row or datetime.fromisoformat(row[0]) > datetime.fromisoformat(signal['recorded_at']):
        raise ValueError('source input unavailable')
    capture = json.loads(row[1])
    symbol = signal['symbol']
    market = candidate_market(symbol, {}, capture)
    if not market:
        from etf_universe import SYMBOLS
        market = 'KOSDAQ' if symbol in (SYMBOLS['KQ150'], SYMBOLS['KQ150LEV'], SYMBOLS['KQ150INV']) else (
            'KOSPI' if symbol in (SYMBOLS['K200'], SYMBOLS['LEV'], SYMBOLS['INV']) else None)
    if not market:
        raise ValueError('market unavailable')
    with ro_connect(ledger) as db:
        bars = day_bars_from_connection(db, signal['day'])
    series = {symbol: [(bar[0], bar[4]) for bar in bars.get(symbol, [])
                       if bar[5] is not None and bar[5] <= at][-45:]}
    if len(series[symbol]) < 3:
        raise ValueError('completed chart unavailable')
    return {'symbol': symbol, 'market': market, 'model': signal['model'], 'decision_at': signal['response_at'],
            'input_digest': signal['digest'], 'capture': capture, 'series': series}


def collect_tick(ledger, research, root, now, reader, samples, credentials, *, trading_day, clock=None, judge=None):
    """One scheduled isolated pass. Broker interaction is restricted to quote reads."""
    from jev_buy_books import apply_bootstrap_quote, apply_quote, export_samples, report
    from jev_buy_judgments import compare, isolated_credentials
    from jev_buy_observer import observe, parse_quote
    from jev_learning_forward import ask
    from src import file_lock

    root = Path(root).expanduser().resolve()
    runtime = (Path.home() / '.vibe-trading').resolve()
    configured = Path(os.environ.get('VIBE_TRADING_HOME') or runtime).expanduser().resolve()
    if any(root == p or p in root.parents for p in (runtime, configured)):
        raise ValueError('isolated research directory required')
    now = now.astimezone(KST)
    clock = clock or (lambda: datetime.now(KST))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'research.lock').open('a') as lock:
        try:
            file_lock.lock(lock, blocking=False)
        except BlockingIOError:
            return {'status': 'already_running'}
        try:
            books = root / 'books.sqlite3'
            bootstrap_books = root / 'bootstrap-books.sqlite3'
            observed = root / 'observations.sqlite3'
            state = root / 'requests.sqlite3'
            current_books = (report(books), report(bootstrap_books))
            held = sorted({symbol for book in current_books for arm in ('baseline', 'experience')
                           for symbol in book[arm]['holdings']})
            observation = observe(ledger, research, observed, now, reader, trading_day=trading_day, clock=clock,
                                  watch_symbols=held)
            if observation['status'] == 'closed_or_unknown':
                return observation
            with closing(sqlite3.connect(observed)) as db:
                db.row_factory = sqlite3.Row
                recent = [dict(row) for row in db.execute('SELECT * FROM signals WHERE day=? AND response_at>=? ORDER BY response_at',
                          (now.date().isoformat(), (now - SIGNAL_LIFETIME).isoformat()))]
                quotes = [dict(row) for row in db.execute('SELECT * FROM quotes WHERE day=? AND minute=? ORDER BY received_at',
                          (now.date().isoformat(), now.replace(second=0, microsecond=0).isoformat()))]
            from session_times import shifted
            final = now.strftime('%H:%M') >= shifted('15:15', now.date())
            for quote in quotes:
                received = datetime.fromisoformat(quote['received_at'])
                if timedelta(0) <= clock() - received <= timedelta(seconds=5):
                    for book in (books, bootstrap_books):
                        apply_quote(book, event_id='monitor:' + quote['symbol'] + ':' + quote['minute'],
                                    symbol=quote['symbol'], source_at=quote['source_at'], received_at=quote['received_at'],
                                    bid=quote['bid'], ask=quote['ask'], final=final)
            previous = list(samples)
            markets = {}
            if state.exists():
                with closing(sqlite3.connect(state)) as db:
                    try:
                        markets = {cycle: json.loads(payload).get('market') for cycle, payload in db.execute(
                            'SELECT cycle_id,result FROM requests WHERE result IS NOT NULL')}
                    except sqlite3.OperationalError:
                        pass
            for sample in export_samples(books) + export_samples(bootstrap_books):
                market = markets.get(sample['signal_id'])
                if sample['arm'] == 'baseline' and market in ('KOSPI', 'KOSDAQ'):
                    previous.append({'sample_id': 'virtual:' + sample['signal_id'], 'position_id': sample['signal_id'],
                                     'symbol': sample['symbol'], 'day': sample['opened_at'][:10], 'purpose': 'candidate',
                                     'market': market, 'route': 'jev', 'label_type': 'virtual_fill',
                                     'cost_basis': 'included', 'return_pct': sample['net_return_pct'],
                                     'outcome_available_at': sample['closed_at']})
            pairs = []
            if not final:
                terminal = set()
                if state.exists():
                    with closing(sqlite3.connect(state)) as db:
                        try:
                            terminal = {(day, cycle) for day, cycle in db.execute(
                                "SELECT day,cycle_id FROM requests WHERE day=? AND status IN ('applied','bootstrap_applied','expired','request_unknown','model_incomplete')",
                                (now.date().isoformat(),))}
                        except sqlite3.OperationalError:
                            pass
                for signal in recent:
                    if (signal['day'], signal['cycle_id']) in terminal:
                        continue
                    try:
                        frame = experiment_frame(ledger, research, signal)
                        experience = (refresh_experience(root / 'experience', clock(), previous, market=frame['market'], route='jev')
                                      or refresh_experience(root / 'experience', clock(), previous, market=frame['market']))
                        if not experience:
                            record_incident(state, signal, clock(), 'experience', 'experience_unavailable')
                            bootstrap = advance(state, signal, clock(), lambda _: {'market': frame['market'], 'bootstrap': True})
                            if bootstrap['status'] not in ('complete', 'quote_pending', 'quote_attempted'):
                                pairs.append({'cycle_id': signal['cycle_id'], 'status': bootstrap['status']})
                                continue
                            if (bootstrap.get('result') or {}).get('bootstrap') is not True:
                                record_incident(state, signal, clock(), 'experience', 'comparison_mode_changed')
                                continue
                            with closing(sqlite3.connect(bootstrap_books)) as db:
                                applied = db.execute('SELECT 1 FROM observations WHERE event_id=?',
                                                     ('bootstrap:' + signal['cycle_id'],)).fetchone()
                            if applied:
                                record_status(state, signal, 'bootstrap_applied')
                                continue
                            response_at = datetime.fromisoformat(signal['response_at'])
                            if not timedelta(0) <= clock() - response_at < timedelta(seconds=60):
                                record_status(state, signal, 'expired')
                                continue
                            record_status(state, signal, 'quote_attempted')
                            try:
                                raw = reader.call_tool('get_stock_price', {'stock_code': signal['symbol']})
                                received = clock()
                                source, bid, ask_price = parse_quote(raw, signal['symbol'], received, after=response_at)
                                apply_bootstrap_quote(bootstrap_books, event_id='bootstrap:' + signal['cycle_id'],
                                    symbol=signal['symbol'], source_at=source.isoformat(), received_at=received.isoformat(),
                                    bid=bid, ask=ask_price, decision={'action': 'BUY', 'signal_id': signal['cycle_id'],
                                                                      'decided_at': signal['response_at']})
                            except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                                record_status(state, signal, 'quote_pending')
                                raise
                            record_status(state, signal, 'bootstrap_applied')
                            pairs.append({'cycle_id': signal['cycle_id'], 'status': 'experience_unavailable_baseline_only'})
                            continue
                        isolated = isolated_credentials(credentials, independent_quota=True)
                        def evaluate(_signal):
                            result = compare(frame, experience, isolated_key=isolated, production_key=credentials['JEV_API_KEY'],
                                             independent_quota=True, reverse=ord(signal['cycle_id'][-1]) % 2 == 1,
                                             judge=judge or ask, clock=clock)
                            return {**result, 'market': frame['market'],
                                    'experience_routes': sorted({lesson['route'] for lesson in experience['lessons']})}
                        paired = advance(state, signal, clock(), evaluate)
                        pairs.append({'cycle_id': signal['cycle_id'], 'status': paired['status']})
                        result = paired.get('result') or {}
                        if paired['status'] == 'complete' and (result.get('failed_arm') or set(result.get('arms', {})) != {'baseline', 'experience'}):
                            record_status(state, signal, 'model_incomplete')
                            record_incident(state, signal, clock(), 'model', result.get('error_type') or 'incomplete_response')
                            continue
                        if paired['status'] not in ('complete', 'quote_attempted', 'quote_pending') or result.get('failed_arm') or set(result.get('arms', {})) != {'baseline', 'experience'}:
                            continue
                        with closing(sqlite3.connect(books)) as db:
                            applied = db.execute('SELECT 1 FROM observations WHERE event_id=?',
                                                 ('entry:' + signal['cycle_id'],)).fetchone()
                        if applied:
                            record_status(state, signal, 'applied')
                            continue
                        now_after = clock()
                        response_at = datetime.fromisoformat(signal['response_at'])
                        if not timedelta(0) <= now_after - response_at < timedelta(seconds=60):
                            record_status(state, signal, 'expired')
                            continue
                        record_status(state, signal, 'quote_attempted')
                        after = max(datetime.fromisoformat(result['arms'][arm]['decided_at']) for arm in ('baseline', 'experience'))
                        try:
                            raw = reader.call_tool('get_stock_price', {'stock_code': signal['symbol']})
                            received = clock()
                            source, bid, ask_price = parse_quote(raw, signal['symbol'], received, after=after)
                            decisions = {arm: {'action': 'BUY' if data['choice'] == signal['symbol'] and data['confidence'] >= .5 else 'WAIT',
                                               'signal_id': signal['cycle_id'], 'decided_at': data['decided_at']}
                                         for arm, data in result['arms'].items()}
                            apply_quote(books, event_id='entry:' + signal['cycle_id'], symbol=signal['symbol'],
                                        source_at=source.isoformat(), received_at=received.isoformat(), bid=bid,
                                        ask=ask_price, decisions=decisions)
                        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
                            record_status(state, signal, 'quote_pending')
                            raise
                        record_status(state, signal, 'applied')
                    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
                        record_incident(state, signal, clock(), 'cycle', type(exc).__name__)
                        pairs.append({'cycle_id': signal['cycle_id'], 'status': 'incomplete', 'error_type': type(exc).__name__})
            return {'status': observation['status'], 'observation': observation, 'pairs': pairs,
                    'books': report(books), 'bootstrap_books': report(bootstrap_books)}
        finally:
            file_lock.unlock(lock)


def prior_paper_samples(backups, now):
    """Read the most recent closed backup and build cost-qualified samples once per session."""
    from datetime import date
    from jev_entry_study import DATABASES, immutable, snapshot_folder
    from jev_learning_replay import collect

    choices = []
    for item in Path(backups).iterdir():
        try:
            day = date.fromisoformat(item.name)
            if day < now.date():
                choices.append((day, item))
        except ValueError:
            continue
    for day, path in sorted(choices, reverse=True):
        try:
            folder = snapshot_folder(path)
            as_of = datetime.combine(day, datetime.max.time(), KST)
            with immutable(folder / DATABASES[0]) as ledger, immutable(folder / DATABASES[1]) as research, \
                    immutable(folder / DATABASES[2]) as orders:
                result = collect(ledger, research, orders, start=day - timedelta(days=45), end=day,
                                 as_of=as_of, cost_bps=15)
            return result['samples']
        except (ValueError, OSError, sqlite3.Error):
            continue
    return []


def main():
    import argparse
    import time
    from dotenv import dotenv_values
    from jev_entry_live_feed import nh_readonly_reader
    from jev_entry_study import atomic_write
    from local_env import ENV
    from sqlite_ro import day_rows
    from src.config.paths import get_runtime_root

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--research-dir', type=Path, default=Path(__file__).resolve().parents[1] / '.scratch/jev-buy-live')
    parser.add_argument('--independent-quota', action='store_true', required=True)
    parser.add_argument('--watch-seconds', type=int, default=0, choices=range(0, 56))
    args = parser.parse_args()
    now = datetime.now(KST)
    runtime = get_runtime_root() / 'nhplug' / 'shared-etf-paper'
    starts, _ = day_rows(runtime / 'trading-day-start.jsonl', now.date().isoformat())
    trading_day = bool(starts and starts[-1].get('note') == 'trading_day')
    from jev_program_paper import session_bounds
    start, end = session_bounds(now.date())
    if not trading_day or now.weekday() >= 5 or not start <= now < end:
        print(json.dumps({'status': 'closed_or_unknown'}))
        return
    root = args.research_dir.expanduser().resolve()
    configured = get_runtime_root().resolve()
    if root == configured or configured in root.parents:
        parser.error('isolated research directory required')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    samples_path = root / f'paper-samples-{now.date().isoformat()}.json'
    if samples_path.exists():
        samples = json.loads(samples_path.read_text(encoding='utf-8'))
    else:
        samples = prior_paper_samples(Path.home() / '.vibe-trading' / 'backups', now)
        atomic_write(samples_path, samples)
    credentials = dotenv_values(ENV, interpolate=False)
    credentials['JEV_ISOLATED_API_KEY'] = os.getenv('JEV_ISOLATED_API_KEY') or credentials.get('JEV_ISOLATED_API_KEY')
    credentials['JEV_API_KEY'] = credentials.get('JEV_API_KEY') or os.getenv('JEV_API_KEY')
    reader = nh_readonly_reader()
    deadline = time.monotonic() + args.watch_seconds
    while True:
        now = datetime.now(KST)
        result = collect_tick(runtime / 'trading-ledger.sqlite3', runtime / 'market-research.sqlite3', root,
                              now, reader, samples, credentials, trading_day=True)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        left = deadline - time.monotonic()
        if left <= 0:
            break
        time.sleep(min(5, left))


if __name__ == '__main__':
    main()
