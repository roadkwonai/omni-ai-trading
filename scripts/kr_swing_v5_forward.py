"""One guarded v5 paper step; refuses incomplete NH pages and uncertain orders."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.loaders.yahoo_loader import DataLoader
from src import file_lock
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.tools.mcp import MCPServerAdapter
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.kr_swing_v5.signal_engine import SignalEngine
from src.tools.private_db import private_file

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / 'strategies/kr_swing_v5/protocol.json'
KST = ZoneInfo('Asia/Seoul')
CAPITAL = 30_000_000


def read_response(adapter, tool, args):
    result = adapter.call_tool(tool, args)
    if result.get('status') != 'ok' or not isinstance(result.get('data'), dict):
        kind = result.get('error_type')
        safe_types = {'TimeoutError', 'ConnectionError', 'McpError', 'ToolError', 'NHOrderBlocked'}
        raise ValueError('broker response unavailable' + (f' ({kind})' if kind in safe_types else ''))
    return result['data']


def fresh_quote(data, symbol, now):
    if data.get('rsp_cd') != '00000' or not isinstance(data.get('Output_0'), dict):
        raise ValueError('quote unavailable')
    quote = data['Output_0']
    if quote.get('iem_cd') != symbol or quote.get('market_status') != '1':
        raise ValueError('quote symbol or session invalid')
    try:
        local = now.astimezone(KST)
        observed = datetime.strptime(local.strftime('%Y%m%d') + str(quote['hoga_bsop_hour']), '%Y%m%d%H:%M:%S').replace(tzinfo=KST)
        if not -5 <= (local - observed).total_seconds() <= 120:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('quote time unavailable or stale') from None
    return quote


def read_paginated(adapter, operation, fields, row_key):
    """Read all NH pages; unknown/repeated continuation never implies completion."""
    rows, seen, cursor, flag, first = [], set(), None, None, None
    for _ in range(20):
        params = {'operationId': operation, 'input': fields, 'want_pagination': True}
        if cursor is not None:
            params.update(cts=cursor, cts_flag=flag)
        data = read_response(adapter, 'call_api', params)
        if data.get('rsp_cd') not in {'00000', 'XA102'}:
            raise ValueError('broker pagination request failed')
        page = data.get(row_key)
        meta = data.get('_nh_pagination')
        if not isinstance(page, list) or not isinstance(meta, dict):
            raise ValueError('broker page or continuation metadata unavailable')
        if first is None:
            first = dict(data)
        rows.extend(page)
        if meta.get('complete') is True:
            first[row_key] = rows
            first['_pagination_complete'] = True
            return first
        if meta.get('complete') is not False:
            raise ValueError('broker pagination completeness unavailable')
        cursor, flag = meta.get('cts'), meta.get('cts_flag')
        if not isinstance(cursor, str) or not cursor or not isinstance(flag, str) or not flag or (cursor, flag) in seen:
            raise ValueError('broker continuation invalid or repeated')
        seen.add((cursor, flag))
    raise ValueError('broker pagination page limit exceeded')


def complete_rows(data, name):
    # shortcut: upstream MCP drops continuation headers; upgrade once it proves complete pagination.
    if data.get('_pagination_complete') is not True:
        raise ValueError(f'{name} pagination completeness unavailable; no order permitted')
    rows = data.get('Output_1' if name == 'balance' else 'Output_0')
    if not isinstance(rows, list):
        raise ValueError(f'{name} rows unavailable')
    return rows


def recover_order_number(db, broker, account, intent):
    """Recover only a guard acknowledgement tied to the exact persisted order."""
    from src.tools.nhplug_order_guard import NHOrderGuard

    day, symbol, side, qty, _ = intent
    row = db.execute('SELECT price FROM intents WHERE day=? AND symbol=? AND side=? AND qty=? AND order_number IS NULL',
                     (day,symbol,side,qty)).fetchone()
    try:
        guard = NHOrderGuard(broker.server_config.args)
        if guard.mode != 'paper' or guard.account != account or not guard.path.is_file() or row is None or side not in {'BUY','SELL'}:
            raise ValueError
        fields = {'act_no':account, 'iem_cd':symbol.split('.')[0], 'orr_qty':qty, 'orr_pr':row[0],
                  'nmn_pr_tp_cd':'01', 'orr_cnd_dit_cd':'00', 'ssl_nmn_pr_dit_cd':'00',
                  'rmt_mkt_cd':'KRX', 'sor_mkt_sli_yn':'N'}
        operation = 'krstockOrderCashBuy' if side == 'BUY' else 'krstockOrderCashSell'
        fingerprint = hashlib.sha256(json.dumps([guard.scope,day,operation,fields],sort_keys=True,allow_nan=False).encode()).hexdigest()
        with sqlite3.connect(f'file:{guard.path}?mode=ro', uri=True) as gdb:
            matches = gdb.execute('SELECT order_number FROM intents WHERE fingerprint=? AND scope=? AND day=? AND operation=? AND symbol=? AND quantity=? AND state IN (\'acknowledged\',\'terminal\')',
                                  (fingerprint,guard.scope,day,operation,fields['iem_cd'],qty)).fetchall()
        if len(matches) != 1 or type(matches[0][0]) is not int or matches[0][0] <= 0:
            raise ValueError
        number = matches[0][0]
    except (AttributeError, IndexError, OSError, sqlite3.Error, TypeError, ValueError):
        raise ValueError('submission outcome unknown; manual reconciliation required') from None
    db.execute('UPDATE intents SET status=?,order_number=? WHERE day=? AND symbol=? AND side=? AND order_number IS NULL',
               ('acknowledged',number,day,symbol,side))
    db.commit()
    return number


def reconcile_order(db, broker, account, intent, *, _after_cancel=False, _attempt=0, wait_before_cancel=0):
    day, symbol, side, qty, number = intent
    if not number:
        number = recover_order_number(db, broker, account, intent)
        intent = (day,symbol,side,qty,number)
    result = read_paginated(broker, 'krstockInquiryDailyOrderExecution',
        {'act_no':account, 'orr_dt':day, 'orr_mkt_cd':'00', 'ost_cns_dit':'0'}, 'Output_0')
    rows = complete_rows(result, 'orders')
    matches = [r for r in rows if r.get('itg_orr_no') == number and r.get('iem_cd') == symbol.split('.')[0]]
    if len(matches) != 1:
        raise ValueError('order absent or ambiguous; reconcile without resubmitting')
    row = matches[0]
    if row.get('sby_dit_cd_nm') not in ({'현금매수'} if side == 'BUY' else {'현금매도'}):
        raise ValueError('order side differs; manual reconciliation required')
    filled, remaining, cancelled = (row.get(k) for k in ('tot_cns_qty','ny_cns_qty','can_qty'))
    if any(type(x) is not int or x < 0 for x in (filled,remaining,cancelled)) or filled + remaining + cancelled != qty:
        raise ValueError('order pending or ambiguous; reconcile without resubmitting')
    if remaining:
        if not _after_cancel and wait_before_cancel > 0:
            time.sleep(2)
            return reconcile_order(db, broker, account, intent, wait_before_cancel=wait_before_cancel - 1)
        if _after_cancel:
            if _attempt >= 3:
                raise ValueError('cancel not yet terminal; reconcile without resubmitting')
            time.sleep(2)
            return reconcile_order(db, broker, account, intent, _after_cancel=True, _attempt=_attempt + 1)
        # Persist before transmission; uncertain cancellations must never be retried.
        try:
            db.execute('INSERT INTO cancel_attempts VALUES (?,?,?)', (day,number,symbol))
            db.commit()
        except sqlite3.IntegrityError:
            raise ValueError('cancel outcome unresolved; reconcile without resubmitting') from None
        response = broker.call_tool('call_api', {'operationId':'krstockOrderCancel', 'input':{
            'act_no':account, 'iem_cd':symbol.split('.')[0], 'org_mkt_orr_no':number,
            'all_pat_dit_cd':'1'}})
        if response.get('status') != 'ok':
            raise ValueError('cancel outcome uncertain; reconcile without resubmitting')
        # Only the original order's terminal execution record proves completion.
        return reconcile_order(db, broker, account, intent, _after_cancel=True)
    if filled:
        average = row.get('cns_avg_uit_pr')
        if not isinstance(average, (int,float)) or isinstance(average,bool) or not math.isfinite(average) or average <= 0:
            raise ValueError('fill price invalid; reconcile without resubmitting')
        existing = db.execute('SELECT day,symbol,side,quantity,price FROM fills WHERE day=? AND order_number=?', (day,number)).fetchone()
        if existing is not None and existing != (day,symbol,side,filled,average):
            raise ValueError('order number conflict; manual reconciliation required')
        db.execute('INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?)', (day,symbol,side,filled,average,number))
    db.execute('UPDATE intents SET status=? WHERE day=? AND symbol=? AND side=?', ('terminal',day,symbol,side))
    db.commit()
    return filled


def validate_bars(frames, codes, now, *, holidays=None):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('timezone-aware time required')
    local = now.astimezone(KST)
    expected = local.date() - timedelta(days=1)
    if holidays is None:
        from kr_swing_v5_daily_data import krx_holidays
        holidays = krx_holidays({local.year, local.year - 1})
    while expected.weekday() >= 5 or expected in holidays:
        expected -= timedelta(days=1)
    index = None
    for code in codes:
        frame = frames.get(code)
        if frame is None or len(frame) < 120 or not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError('daily history incomplete')
        if frame.index[-1].date() != expected or frame.index[-1].date() >= local.date():
            raise ValueError('last completed daily bar is stale or unverified')
        if index is not None and not index.equals(frame.index):
            raise ValueError('daily history calendars differ')
        index = frame.index
        if any(c not in frame for c in ('open', 'high', 'low', 'close', 'volume')):
            raise ValueError('daily OHLCV missing')
        x = frame[['open', 'high', 'low', 'close', 'volume']].tail(120).astype(float)
        if not x.map(math.isfinite).all().all() or (x[['open','high','low','close']] <= 0).any().any() or (x['volume'] < 0).any():
            raise ValueError('daily OHLCV invalid')
        if (x['high'] < x[['open','low','close']].max(axis=1)).any() or (x['low'] > x[['open','high','close']].min(axis=1)).any():
            raise ValueError('daily OHLCV inconsistent')
    return expected.isoformat()


def connect_state(path, account):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    private_file(path)  # src.tools.private_db: never os.open an existing database
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS observations (day TEXT PRIMARY KEY, observed_at TEXT NOT NULL, signals TEXT NOT NULL, closes TEXT NOT NULL, state TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intents (day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, price INTEGER NOT NULL, status TEXT NOT NULL, order_number INTEGER, PRIMARY KEY(day,symbol,side))')
    db.execute('CREATE TABLE IF NOT EXISTS fills (day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, order_number INTEGER NOT NULL, PRIMARY KEY(day,order_number))')
    db.execute('CREATE TABLE IF NOT EXISTS cancel_attempts (day TEXT NOT NULL, order_number INTEGER NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY(day,order_number))')
    db.execute('CREATE TABLE IF NOT EXISTS marks (day TEXT PRIMARY KEY, observed_at TEXT NOT NULL, cash_gross REAL NOT NULL, holdings_value REAL NOT NULL, equity_gross REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS cost_estimates (day TEXT PRIMARY KEY, assumption TEXT NOT NULL, total REAL NOT NULL, equity_estimated REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS data_prefix (symbol TEXT PRIMARY KEY, signal_day TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS data_prefix_archive (symbol TEXT NOT NULL, signal_day TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(symbol,signal_day))')
    expected = {'account_hash': hashlib.sha256(account.encode()).hexdigest(),
                'protocol_hash': hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()}
    for name, value in expected.items():
        old = db.execute('SELECT value FROM meta WHERE name=?', (name,)).fetchone()
        if old and old[0] != value:
            db.close()
            raise ValueError('account or strategy differs from existing journal')
        db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', (name, value))
    db.commit()
    # Migrate the old global unique order number transactionally; NH numbers reset daily.
    indexes = list(db.execute('PRAGMA index_list(fills)'))
    unique_indexes = [tuple(row[0] for row in db.execute('SELECT name FROM pragma_index_info(?)', (index[1],)))
                      for index in indexes if index[2]]
    if unique_indexes == [('order_number',)]:
        columns = [row[1] for row in db.execute('PRAGMA table_info(fills)')]
        triggers = db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='fills'").fetchone()[0]
        if columns != ['day', 'symbol', 'side', 'quantity', 'price', 'order_number'] or len(indexes) != 1 or triggers:
            db.close()
            raise ValueError('unexpected fills schema; manual review required')
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute('ALTER TABLE fills RENAME TO fills_legacy')
            db.execute('CREATE TABLE fills (day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, order_number INTEGER NOT NULL, PRIMARY KEY(day,order_number))')
            db.execute('INSERT INTO fills SELECT day,symbol,side,quantity,price,order_number FROM fills_legacy')
            db.execute('DROP TABLE fills_legacy')
            db.commit()
        except Exception:
            db.rollback()
            db.close()
            raise
    elif unique_indexes != [('day', 'order_number')]:
        db.close()
        raise ValueError('unexpected fills uniqueness; manual review required')
    return db


def record_mark(db, now, cash, holdings_value):
    """Record an intraday estimate on its valuation date, not the signal date."""
    valuation_day = now.astimezone(KST).date().isoformat()
    db.execute('INSERT OR REPLACE INTO marks VALUES (?,?,?,?,?)',
               (valuation_day, now.isoformat(), cash, holdings_value, cash + holdings_value))
    estimated = sum(qty * price * (.002 if side == 'BUY' else .004)
                    for side, qty, price in db.execute('SELECT side,quantity,price FROM fills'))
    db.execute('INSERT OR REPLACE INTO cost_estimates VALUES (?,?,?,?)',
               (valuation_day, 'estimated only: buy 0.20%, sell 0.40%; actual fees unavailable',
                estimated, cash + holdings_value - estimated))
    db.commit()
    return estimated


def refresh_mark(db, broker, readonly, account, codes, now):
    """Reconcile terminal fills against a fresh complete balance before marking."""
    balance = read_paginated(broker, 'krstockInquiryBalance', {
        'act_no': account, 'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9',
        'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1',
    }, 'Output_1')
    owned = {code: 0 for code in codes}
    cash = float(CAPITAL)
    for symbol, side, quantity, price in db.execute('SELECT symbol,side,quantity,price FROM fills'):
        if symbol not in owned or side not in {'BUY', 'SELL'}:
            raise ValueError('unrecognized journal position')
        owned[symbol] += quantity if side == 'BUY' else -quantity
        cash += quantity * price * (1 if side == 'SELL' else -1)
    actual = {code: 0 for code in codes}
    for row in complete_rows(balance, 'balance'):
        if not isinstance(row, dict) or 'rsdl_qty' not in row:
            raise ValueError('invalid account position')
        qty = float(row['rsdl_qty'])
        if not math.isfinite(qty) or qty < 0 or not qty.is_integer():
            raise ValueError('invalid share quantity')
        if qty:
            matches = [code for code in codes if code.split('.')[0] == str(row.get('iem_cd') or '')]
            if len(matches) != 1:
                raise ValueError('unowned account position')
            actual[matches[0]] += int(qty)
    if owned != actual:
        raise ValueError('broker and owned positions differ')
    if not math.isfinite(cash) or cash < 0:
        raise ValueError('strategy cash ledger invalid')
    holdings_value = 0.0
    for symbol, qty in owned.items():
        if qty:
            quote = fresh_quote(read_response(readonly, 'get_stock_price', {'stock_code': symbol.split('.')[0]}), symbol.split('.')[0], now)
            price = quote.get('stck_prpr')
            if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
                raise ValueError('position mark invalid')
            holdings_value += qty * price
    return record_mark(db, now, cash, holdings_value)


def record_intent(db, session, symbol, side, qty, price):
    """Invalidate the current mark in the same commit as the pending intent."""
    day = datetime.strptime(session, '%Y%m%d').date().isoformat()
    with db:
        db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,NULL)',
                   (session, symbol, side, qty, price, 'uncertain'))
        db.execute('DELETE FROM marks WHERE day=?', (day,))
        db.execute('DELETE FROM cost_estimates WHERE day=?', (day,))


def save_or_check_history(db, frames, day):
    """Reject revisions to any previously frozen 120-bar signal input."""
    cols = ['open','high','low','close','volume']
    for symbol, frame in frames.items():
        old = db.execute('SELECT signal_day,payload,sha256 FROM data_prefix WHERE symbol=?', (symbol,)).fetchone()
        if old:
            if hashlib.sha256(old[1].encode()).hexdigest() != old[2]:
                raise ValueError('stored market history hash mismatch')
            prior = json.loads(old[1])
            current = []
            for row in prior:
                date = pd.Timestamp(row[0])
                if date not in frame.index:
                    raise ValueError('previously observed market history revised; reconciliation required')
                values = [float(frame.loc[date, c]) for c in cols]
                if any(not math.isclose(values[i], row[i+1], rel_tol=1e-6, abs_tol=0) for i in range(4)) or values[4] != row[5]:
                    raise ValueError('previously observed market history revised; reconciliation required')
                current.append(values[3])
            def decision_components(values):
                close = values[-1]
                ma60, ma120 = sum(values[-60:]) / 60, sum(values[-120:]) / 120
                return close > ma60, close >= ma60, ma60 > ma120
            if decision_components([row[4] for row in prior]) != decision_components(current):
                raise ValueError('previous signal changed; reconciliation required')
            if day < old[0]:
                raise ValueError('signal date moved backwards')
            db.execute('INSERT OR IGNORE INTO data_prefix_archive VALUES (?,?,?,?)', (symbol,*old))
            if day == old[0]:
                continue
        data = [[stamp.date().isoformat(), *[float(row[c]) for c in cols]] for stamp,row in frame.loc[:day, cols].tail(120).iterrows()]
        if len(data) != 120:
            raise ValueError('signal history incomplete')
        payload = json.dumps(data, separators=(',', ':'), allow_nan=False)
        db.execute('INSERT OR REPLACE INTO data_prefix VALUES (?,?,?,?)', (symbol,day,payload,hashlib.sha256(payload.encode()).hexdigest()))
    db.commit()


def check_recorded_signal(db, frames):
    """Refuse revised source history that changes an already recorded decision."""
    old = db.execute('SELECT day,signals FROM observations ORDER BY day DESC LIMIT 1').fetchone()
    if not old:
        return
    day, encoded = old
    expected = json.loads(encoded)
    prior = {symbol: frame.loc[:day] for symbol, frame in frames.items()}
    if any(frame.empty or frame.index[-1].date().isoformat() != day for frame in prior.values()):
        raise ValueError('recorded signal history unavailable')
    current = {symbol: float(signal.iloc[-1]) for symbol, signal in SignalEngine().generate(prior).items()}
    if current != expected:
        raise ValueError('recorded strategy signal changed; reconciliation required')


def collect(now, broker, readonly, account, loader):
    codes = json.loads(PROTOCOL.read_text(encoding='utf-8'))['universe']
    accounts = read_response(readonly, 'list_accounts', {})
    if accounts.get('rsp_cd') != '00000' or not any(str(r.get('acct_no')) == account and str(r.get('acct_type')) == '03' for r in accounts.get('Output_0', []) if isinstance(r, dict)):
        raise ValueError('paper account identity unavailable')
    balance = read_paginated(broker, 'krstockInquiryBalance', {'act_no': account, 'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9', 'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}, 'Output_1')
    orders = read_paginated(broker, 'krstockInquiryDailyOrderExecution', {'act_no': account, 'orr_dt': now.astimezone(KST).strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
    start = (now - timedelta(days=550)).strftime('%Y-%m-%d')
    end = (now.astimezone(KST).date() - timedelta(days=1)).isoformat()
    frames = loader.fetch(codes, start, end, interval='1D')
    day = validate_bars(frames, codes, now)
    signals = {c: float(s.iloc[-1]) for c, s in SignalEngine().generate(frames).items()}
    closes = {c: float(frames[c]['close'].iloc[-1]) for c in codes}
    return day, signals, closes, balance, orders, frames


class SnapshotLoader:
    """Use the same hash-verified completed-session snapshot as the benchmark."""

    def __init__(self, path):
        from kr_swing_v5_daily_data import verify_snapshot
        self.frames = verify_snapshot(path.resolve(strict=True))

    def fetch(self, codes, start_date, end_date, **kwargs):
        return {code: self.frames[code].loc[start_date:end_date].copy() for code in codes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--data', type=Path, help='Hash-verified v5 daily snapshot shared with the benchmark')
    args = parser.parse_args()
    configs = load_agent_config().mcp_servers
    paper = configs['nhplug_paper']
    options = paper.args
    if options.count('--mode') != 1 or options[options.index('--mode')+1] != 'paper' or options.count('--account') != 1:
        raise ValueError('paper account configuration invalid')
    account = options[options.index('--account')+1]
    readonly = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])
    broker = MCPServerAdapter('nhplug_paper', paper)
    now = datetime.now(KST)
    directory = get_runtime_root() / 'nhplug/strategies/kr_swing_v5'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    with (directory / 'runner.lock').open('a', encoding='utf-8') as lock:
        file_lock.lock(lock, blocking=False)
        db = connect_state(directory / 'forward.sqlite3', account)
        try:
            loader = SnapshotLoader(args.data) if args.data else DataLoader()
            day, signals, closes, balance, orders, frames = collect(now, broker, readonly, account, loader)
            # Freeze and validate source bars before any order can be considered.
            check_recorded_signal(db, frames)
            save_or_check_history(db, frames, day)
            # Only transport-proven completion may establish ownership of account state.
            positions = complete_rows(balance, 'balance')
            day_orders = complete_rows(orders, 'orders')
            if any(not isinstance(r, dict) or 'ny_cns_qty' not in r for r in day_orders):
                raise ValueError('invalid order list')
            pending = db.execute("SELECT day,symbol,side,qty,order_number FROM intents WHERE status != 'terminal'").fetchall()
            for intent in pending:
                reconcile_order(db, broker, account, intent)
            if pending:
                balance = read_paginated(broker, 'krstockInquiryBalance', {'act_no': account, 'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9', 'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}, 'Output_1')
                positions = complete_rows(balance, 'balance')
                orders = read_paginated(broker, 'krstockInquiryDailyOrderExecution', {'act_no': account, 'orr_dt': now.astimezone(KST).strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
                day_orders = complete_rows(orders, 'orders')
            if any(float(r['ny_cns_qty']) != 0 for r in day_orders):
                raise ValueError('open order requires broker reconciliation')
            owned = {c: 0 for c in signals}
            for symbol, side, qty in db.execute('SELECT symbol,side,quantity FROM fills'):
                if symbol not in owned:
                    raise ValueError('unrecognized journal position')
                owned[symbol] += qty if side == 'BUY' else -qty
            actual = {}
            for row in positions:
                if not isinstance(row, dict) or 'rsdl_qty' not in row:
                    raise ValueError('invalid account position')
                qty = float(row['rsdl_qty'])
                if not math.isfinite(qty) or qty < 0 or not qty.is_integer():
                    raise ValueError('invalid share quantity')
                code = str(row.get('iem_cd') or '')
                if qty:
                    matches = [s for s in signals if s.split('.')[0] == code]
                    if len(matches) != 1:
                        raise ValueError('unowned account position')
                    actual[matches[0]] = actual.get(matches[0], 0) + int(qty)
            if any(owned[s] != actual.get(s, 0) for s in owned):
                raise ValueError('broker and owned positions differ')
            cash = float(CAPITAL)
            for side, qty, price in db.execute('SELECT side,quantity,price FROM fills'):
                cash += qty * price * (1 if side == 'SELL' else -1)
            if not math.isfinite(cash) or cash < 0:
                raise ValueError('strategy cash ledger invalid')
            holdings_value = 0.0
            for symbol, qty in owned.items():
                if qty:
                    q = fresh_quote(read_response(readonly, 'get_stock_price', {'stock_code':symbol.split('.')[0]}), symbol.split('.')[0], datetime.now(KST))
                    price = q.get('stck_prpr')
                    if q.get('iem_cd') != symbol.split('.')[0] or type(price) not in (int,float) or not math.isfinite(price) or price <= 0:
                        raise ValueError('position mark invalid')
                    holdings_value += qty * price
            estimated = record_mark(db, now, cash, holdings_value)
            if args.initialize:
                db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)',
                           (day, now.isoformat(), json.dumps(signals), json.dumps(closes), 'observed'))
                db.commit()
                print(json.dumps({'status':'observed','day':day,'signals':signals,'initial_capital':CAPITAL}))
                return
            if not db.execute('SELECT 1 FROM observations').fetchone():
                raise ValueError('initial observation required')
            db.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)', (day,now.isoformat(),json.dumps(signals),json.dumps(closes),'observed'))
            db.commit()
            candidates = [(c, 'SELL' if owned[c] else 'BUY') for c in signals
                          if (owned[c] and signals[c] == 0) or (not owned[c] and signals[c] > 0)]
            if not args.execute or not candidates:
                print(json.dumps({'status':'ready' if candidates else 'wait','day':day,'candidates':candidates}))
                return
            policy = json.loads((get_runtime_root() / 'nhplug/swing-policy.json').read_text(encoding='utf-8'))
            if any(policy.get(key) is not True for key in ('enabled','krx_session_check','auto_loss_stop')):
                raise ValueError('required NH paper safety policy is not enabled')
            symbol, side = candidates[0]
            quote = fresh_quote(read_response(readonly, 'get_stock_price', {'stock_code': symbol.split('.')[0]}), symbol.split('.')[0], datetime.now(KST))
            price = quote.get('askp' if side == 'BUY' else 'bidp')
            if quote.get('iem_cd') != symbol.split('.')[0] or type(price) is not int or price <= 0:
                raise ValueError('limit quote invalid')
            qty = min(int(750_000 // price), int(3_000_000 // price), int(max(cash - estimated, 0) // (price * 1.002))) if side == 'BUY' else owned[symbol]
            if side == 'SELL':
                sellable = read_response(broker, 'call_api', {'operationId':'krstockInquirySellableQuantity', 'input':{'act_no':account,'iem_cd':symbol.split('.')[0],'cfd_lon_cd':'00'}})
                result = sellable.get('Output_0')
                if not isinstance(result, dict) or result.get('iem_cd') != symbol.split('.')[0] or type(result.get('sll_pbl_qty')) not in (int,float) or result['sll_pbl_qty'] < qty:
                    raise ValueError('sellable quantity not confirmed')
            if qty <= 0:
                raise ValueError('quantity is zero')
            session = now.astimezone(KST).strftime('%Y%m%d')
            fields = {'act_no':account, 'iem_cd':symbol.split('.')[0], 'orr_qty':qty, 'orr_pr':price,
                      'nmn_pr_tp_cd':'01', 'orr_cnd_dit_cd':'00', 'ssl_nmn_pr_dit_cd':'00',
                      'rmt_mkt_cd':'KRX', 'sor_mkt_sli_yn':'N'}
            # An intent is committed before transmission. A crash never authorizes replay.
            record_intent(db, session, symbol, side, qty, price)
            result = broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy' if side == 'BUY' else 'krstockOrderCashSell', 'input':fields})
            body = result.get('data') if result.get('status') == 'ok' else None
            number = body.get('Output_0', {}).get('mkt_orr_no') if isinstance(body, dict) else None
            if type(number) is not int or number <= 0:
                raise ValueError('submission uncertain; reconcile without resubmitting')
            db.execute('UPDATE intents SET status=?,order_number=? WHERE day=? AND symbol=? AND side=?', ('acknowledged',number,session,symbol,side))
            db.commit()
            filled = reconcile_order(db, broker, account, (session,symbol,side,qty,number), wait_before_cancel=3)
            refresh_mark(db, broker, readonly, account, signals, datetime.now(KST))
            print(json.dumps({'status':'terminal','symbol':symbol,'side':side,'filled':filled,'fees':'unavailable'}))
        finally:
            db.close()


if __name__ == '__main__':
    main()
