"""Select a separate NH paper account using complete broker reads; no writes to broker."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import fcntl
import time
import uuid
import math
import re
import sys
from contextvars import ContextVar
from functools import wraps
from decimal import Decimal
from xml.etree.ElementTree import ParseError
from datetime import datetime, timedelta
from datetime import datetime as datetime_type
from pathlib import Path
from zoneinfo import ZoneInfo

from config_store import write_json
from src.config.loader import load_agent_config
from src.config.paths import get_runtime_root
from src.tools.mcp import MCPServerAdapter
from src.tools.nhplug_order_guard import NHOrderGuard
from nh_etf_paper_runner import headlines, valid_bars, volatility
from kr_swing_v5_forward import fresh_quote
from nh_etf_auto_v2 import decision, jev, holding_decision, exit_signal
from etf_universe import SYMBOLS as ETF7_SYMBOLS
from track_record import summary as track_record
from market_context import load as load_market, summary as market_summary
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')


def cycle_delay(started, interval=60, minimum=10):
    """Pace normal monitoring by cycle start; slow reads still get a minimum rest."""
    return max(minimum, interval - max(0, time.monotonic() - started))


def keep_today(today, *stores):
    """Drop other days from per-process memos keyed by (day, ...) or day: the runner process lives for weeks."""
    for store in stores:
        for key in [k for k in store if (k[0] if isinstance(k, tuple) else k) != today]:
            store.discard(key) if isinstance(store, set) else store.pop(key)


def market_view(root):
    """Fresh regime/flows/themes/global summary from the movers service; None when missing or stale."""
    return market_summary(load_market(Path(root) / 'market-context.json'))
from watchlist import load_candidates as mover_candidates  # Jev-selected watchlist (scripts/watchlist.py)
import market_playbook
import oversold_cases  # 낙폭과대 사례집: the past of today's falls in every Jev decision
from nh_etf_auto_v2 import decision7, jev7, holding_decision7, jev_holding7
from paper_ai_modes import apply_pending_modes, read_modes
from etf_universe import CODE_PATTERN
from tunables import setting
from src.tools.private_db import private_file

KST = ZoneInfo('Asia/Seoul')
SYMBOLS = {'K200': '069500', 'INV': '114800'}

_BUY_SETTINGS = ContextVar('paper_buy_settings', default=None)


def _settings_snapshot(root):
    from tunables import path as tunables_path
    files = (Path(root).parent / 'accounts.json', Path(root).parent / 'shared-etf-paper' / 'strategy-settings.json', tunables_path())
    try:
        values = []
        for p in files:
            try:
                values.append(p.read_bytes())
            except FileNotFoundError:
                values.append(None)
        return tuple(values)
    except OSError:
        return None


def buy_settings_cycle(fn):
    """Keep the decision's settings stable through the final guarded buy check."""
    @wraps(fn)
    def wrapped(db, read, broker, account, root, *args, **kwargs):
        token = _BUY_SETTINGS.set((root, _settings_snapshot(root)))
        try:
            return fn(db, read, broker, account, root, *args, **kwargs)
        finally:
            _BUY_SETTINGS.reset(token)
    return wrapped


def paper_pages(adapter, operation, fields, block, clock=time.monotonic, sleep=time.sleep):
    if (operation, block) not in {('krstockInquiryBalance','Output_1'),
                                  ('krstockInquiryDailyOrderExecution','Output_0')}:
        raise ValueError('only paper balance and order reads may be retried')
    from nh_live_broker import PAGE_GAP  # NH's ~1 request a second per query (11-19 IGW42903 on every second page)
    rows, seen, cursor, flag, first, started = [], set(), None, None, None, None
    accepted = {('00000', '모의투자 마지막 자료입니다'),
                ('XA102', '모의투자 조회가 완료되었습니다'),
                ('00000', '조회가 완료되었습니다'),
                ('00218', '계속 조회시 다음(연속조회) 버튼을 누르시기 바랍니다'),
                ('XA109', '모의투자 조회가 계속 됩니다. 다음 버튼을 누르세요'),  # paper continuation (11/13 13:09 crash)
                ('11512', '데이터가 존재하지 않습니다')}
    for _ in range(20):
        params = {'operationId': operation, 'input': fields, 'want_pagination': True}
        if cursor is not None:
            params.update(cts=cursor, cts_flag=flag)
            sleep(max(0.0, started + PAGE_GAP - clock()))
        for attempt in range(3):
            started = clock()
            response = adapter.call_tool('call_api', params)
            if response.get('status') == 'ok' and isinstance(response.get('data'), dict):
                break
            if attempt < 2:
                sleep(2 ** attempt)
        data = response.get('data')
        if response.get('status') != 'ok' or not isinstance(data, dict):
            raise ValueError('paper broker page unavailable')
        message = data.get('rsp_msg')
        if not isinstance(message, str) or (data.get('rsp_cd'), message.strip().rstrip('.')) not in accepted:
            raise ValueError('paper broker page rejected')
        page, meta = data.get(block), data.get('_nh_pagination')
        if data.get('rsp_cd') == '11512' and page is None:
            page = []
        if not isinstance(page, list) or not isinstance(meta, dict):
            raise ValueError('paper page or continuation unavailable')
        if first is None:
            first = dict(data)
        rows.extend(page)
        if meta.get('complete') is True:
            if data.get('rsp_cd') in ('00218', 'XA109'):
                raise ValueError('contradictory pagination status')
            first[block] = rows
            return first
        if meta.get('complete') is not False:
            raise ValueError('paper pagination completeness unavailable')
        cursor, flag = meta.get('cts'), meta.get('cts_flag')
        if not isinstance(cursor, str) or not cursor or not isinstance(flag, str) or not flag or (cursor, flag) in seen:
            raise ValueError('paper pagination cursor invalid')
        seen.add((cursor, flag))
    raise ValueError('paper pagination limit reached')


def account_hash(account):
    return hashlib.sha256(account.encode('ascii')).hexdigest()


def paper_account(config):
    args = config.args
    if args.count('--mode') != 1 or args[args.index('--mode') + 1:args.index('--mode') + 2] != ['paper']:
        raise ValueError('paper profile required')
    if args.count('--account') != 1:
        raise ValueError('paper account configuration invalid')
    account = args[args.index('--account') + 1]
    if len(account) != 11 or not account.isascii() or not account.isdigit():
        raise ValueError('paper account invalid')
    return account


def isolated_config(template, account):
    paper_account(template)
    if len(account) != 11 or not account.isascii() or not account.isdigit():
        raise ValueError('candidate account invalid')
    args = list(template.args)
    args[args.index('--account') + 1] = account
    return template.model_copy(update={'args': args})


def reserved_hashes(runtime):
    """Read known swing journals, failing closed on malformed ownership metadata."""
    result = set()
    directory = Path(runtime) / 'nhplug/strategies'
    for path in directory.glob('*/forward.json'):
        data = json.loads(path.read_text())
        value = data.get('account_sha256')
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError('strategy account identity unavailable')
        result.add(value)
    for path in directory.glob('*/forward.sqlite3'):
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            row = db.execute("SELECT value FROM meta WHERE name='account_hash'").fetchone()
        if not row or not isinstance(row[0], str) or len(row[0]) != 64:
            raise ValueError('strategy account identity unavailable')
        result.add(row[0])
    return result


def candidate_available(adapter, account, now):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('timezone required')
    balance = paper_pages(adapter, 'krstockInquiryBalance', {'act_no': account, 'bnc_bse_cd': '5',
        'ltg_aot_dit_cd': '9', 'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}, 'Output_1')
    orders = paper_pages(adapter, 'krstockInquiryDailyOrderExecution', {'act_no': account,
        'orr_dt': now.astimezone(KST).strftime('%Y%m%d'), 'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
    holdings = balance['Output_1']
    history = orders['Output_0']
    if not isinstance(balance.get('Output_0'), dict):
        raise ValueError('balance summary unavailable')
    for row in holdings:
        if not isinstance(row, dict) or type(row.get('rsdl_qty')) not in (int, float) or row['rsdl_qty'] != 0:
            return False
    for row in history:
        if not isinstance(row, dict) or type(row.get('ny_cns_qty')) is not int or row['ny_cns_qty'] != 0:
            return False
    return True


def select(configs, runtime, now, adapter_factory=MCPServerAdapter):
    template = configs['nhplug_paper']
    default = paper_account(template)
    # shortcut: matching the portal-verified template code does not prove eligibility; use broker product metadata when available.
    if default[3:5] != '01':
        raise ValueError('verified stock paper account template unavailable')
    reserved = reserved_hashes(runtime) | {account_hash(default)}
    for name, config in configs.items():
        if name != 'nhplug_paper' and name.startswith('nhplug_') and '--mode' in config.args:
            i = config.args.index('--mode')
            if config.args[i + 1:i + 2] == ['paper']:
                reserved.add(account_hash(paper_account(config)))
    readonly = adapter_factory('nhplug_readonly', configs['nhplug_readonly'])
    response = readonly.call_tool('list_accounts', {})
    data = response.get('data')
    if response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000' or not isinstance(data.get('Output_0'), list):
        raise ValueError('account list unavailable')
    candidates = []
    for row in data['Output_0']:
        if not isinstance(row, dict) or row.get('acct_type') != '03':
            continue
        account = row.get('acct_no')
        if not isinstance(account, str) or len(account) != 11 or not account.isascii() or not account.isdigit():
            raise ValueError('paper account identity invalid')
        if account[3:5] != default[3:5]:
            continue
        digest = account_hash(account)
        if digest not in reserved:
            candidates.append((digest, account))
    for digest, account in sorted(set(candidates)):
        config = isolated_config(template, account)
        broker = adapter_factory('nhplug_paper_isolated', config)
        if candidate_available(broker, account, now):
            return digest, config
    raise ValueError('no verified separate empty paper account available')


def selected_config(configs, digest, adapter_factory=MCPServerAdapter):
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('invalid selected account hash')
    template = configs['nhplug_paper']
    default = paper_account(template)
    if default[3:5] != '01':
        raise ValueError('verified stock paper account template unavailable')
    if account_hash(default) == digest:
        raise ValueError('selected account collides with swing account')
    for name, config in configs.items():
        if name.startswith('nhplug_') and name != 'nhplug_paper_isolated' and '--mode' in config.args:
            i = config.args.index('--mode')
            if config.args[i + 1:i + 2] == ['paper'] and account_hash(paper_account(config)) == digest:
                raise ValueError('selected account collides with another configured profile')
    response = adapter_factory('nhplug_readonly', configs['nhplug_readonly']).call_tool('list_accounts', {})
    data = response.get('data')
    if response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000' or not isinstance(data.get('Output_0'), list):
        raise ValueError('account list unavailable')
    accounts = [r['acct_no'] for r in data['Output_0'] if isinstance(r, dict) and r.get('acct_type') == '03'
                and isinstance(r.get('acct_no'), str) and len(r['acct_no']) == 11
                and r['acct_no'].isascii() and r['acct_no'].isdigit() and account_hash(r['acct_no']) == digest]
    if len(accounts) != 1:
        raise ValueError('selected paper account unavailable or ambiguous')
    if accounts[0][3:5] != default[3:5]:
        raise ValueError('selected account does not match stock paper product code')
    return isolated_config(template, accounts[0])


def shared_default_config(configs, runtime, adapter_factory=MCPServerAdapter):
    config = configs['nhplug_paper']
    account = paper_account(config)
    if account[3:5] != '01':
        raise ValueError('verified stock paper account unavailable')
    guard = NHOrderGuard(config.args)
    policy = guard.paper_policy()
    if (not isinstance(policy, dict) or policy.get('enabled') is not True
            or policy.get('auto_loss_stop') is not True or policy.get('krx_session_check') is not True
            or policy.get('_account_independent') is True):
        raise ValueError('shared account policy unavailable')
    try:
        owner = json.loads((Path(runtime) / 'nhplug/paper-account-owners.json').read_text())
        if (not isinstance(owner, dict) or owner.get('version') != 1 or not isinstance(owner.get('accounts'), dict)
                or owner['accounts'].get(guard.scope) != 'nhplug_paper_isolated'):
            raise ValueError('shared account owner unavailable')
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError('shared account owner unavailable') from exc
    response = adapter_factory('nhplug_readonly', configs['nhplug_readonly']).call_tool('list_accounts', {})
    data = response.get('data') if isinstance(response, dict) else None
    if (not isinstance(response, dict) or response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000'
            or not isinstance(data.get('Output_0'), list)):
        raise ValueError('paper account list unavailable')
    matches = [row for row in data['Output_0'] if isinstance(row, dict) and row.get('acct_no') == account]
    if len(matches) != 1 or matches[0].get('acct_type') != '03':
        raise ValueError('shared paper account unavailable or ambiguous')
    return config


def connect_journal(path, digest):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_file(path)  # src.tools.private_db: never os.open an existing database
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, day TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, price INTEGER NOT NULL, status TEXT NOT NULL, order_number INTEGER, created REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS cancels (order_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, created REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS fills (order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, quantity INTEGER NOT NULL, price REAL NOT NULL, time TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS decision_order_links (order_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS sell_order_triggers (order_id TEXT PRIMARY KEY, trigger TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS jev_add_signals (symbol TEXT NOT NULL, response_time TEXT NOT NULL, PRIMARY KEY(symbol,response_time))')
    db.execute('CREATE TABLE IF NOT EXISTS buy_confidence (symbol TEXT NOT NULL, time TEXT NOT NULL, confidence REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS pre_send_aborts (order_id TEXT PRIMARY KEY, time TEXT NOT NULL)')
    row = db.execute("SELECT value FROM meta WHERE name='account_sha256'").fetchone()
    if row is not None and row[0] != digest:
        db.close()
        raise ValueError('journal account mismatch')
    db.execute("INSERT OR IGNORE INTO meta VALUES ('account_sha256',?)", (digest,))
    db.commit()
    return db


def journal_event(root, event, **fields):
    root = Path(root)
    entry = {'time': datetime.now(KST).isoformat(), 'event': event, **fields}
    path = root / 'events.jsonl'
    with path.open('a') as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False) + '\n')
    from trading_ledger import ingest
    marker = root / 'ledger-ingest-error.json'
    try:
        ingest(path, root / 'trading-ledger.sqlite3')
    except Exception as exc:
        # The jsonl line above is the record; the SQLite copy is derived and catches up once the cause is fixed.
        # 11/17 11:46 an unknown event name raised here, stopped the vibe runner and lost a buy; never again.
        note = {'time': entry['time'], 'event': event, 'error_type': type(exc).__name__, 'detail': str(exc)[:200]}
        print(json.dumps({'ledger_ingest_error': note}, ensure_ascii=False), file=sys.stderr, flush=True)
        try:
            write_json(marker, note, indent=None)
        except OSError:
            pass
    else:
        marker.unlink(missing_ok=True)  # caught up: the copy matches the trail again


class PaperOrderNotSent(ValueError):
    """The adapter proved the order did not reach the broker transport."""


SNAPSHOT_MINUTES = 5


def account_snapshot(root, summary, now=None):
    """Append the broker's account totals to account-snapshots.jsonl (first cycle of the day, then every SNAPSHOT_MINUTES).
    NH paper reports no fee or tax per fill, so the day's real cost is measured from these totals (scripts/paper_costs.py)."""
    now = now or datetime.now(KST)
    path = Path(root) / 'account-snapshots.jsonl'
    try:
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size - 600))
            last = json.loads(f.read().decode().strip().splitlines()[-1])['time']
    except (OSError, ValueError, IndexError, KeyError):
        last = ''
    if last[:10] == now.date().isoformat() and (now - datetime.fromisoformat(last)).total_seconds() < SNAPSHOT_MINUTES * 60:
        return
    fields = {'cash': 'orr_pbl_amt1', 'deposit': 'dca', 'deposit_d2': 'nxt2_dd_dca', 'total_assets': 'tot_aet_amt',
              'invested': 'tot_byn_amt', 'evaluation': 'tot_eal_amt'}
    row = {'time': now.isoformat(), **{k: summary.get(v) for k, v in fields.items()}}
    if not all(type(row[k]) in (int, float) for k in fields):
        return  # a partial page proves nothing; skip rather than record a misleading total
    try:
        with path.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        os.chmod(path, 0o600)
    except OSError:
        pass


def guard_note(exc):
    """The guard's own English refusal text (e.g. which limit), kept so a refusal can be told apart in the record."""
    text = str(exc)
    return {'guard_detail': text} if re.fullmatch(r'[A-Za-z][A-Za-z ;,.]{2,119}', text) else {}


class PaperOrderGuardRefused(PaperOrderNotSent):
    """The order guard refused before reserving an intent, so no transport was opened."""


ETF7_MAX_ORDER_QTY = setting('paper_max_order_qty')
PAPER_MAX_TOTAL_QTY = setting('paper_max_total_qty')


def quantity_room(owned):
    return max(0, PAPER_MAX_TOTAL_QTY - sum(owned.values())) if PAPER_MAX_TOTAL_QTY else float('inf')
SHARED_SINGLE_ORDER_KRW = 3_000_000  # mirrors NHOrderGuard's shared-account single-buy limit (accounts.json paper-shared.limits)
# User-approved split plan (2026-11-11): 3 buys at first price, -0.7%, -1.4%; sell half at avg +1%, rest at +2%.
SPLIT_TRANCHES = 3  # tranches a position is planned in (the live mirror sizes by it)
SPLIT_STEP = Decimal('0.007')  # defaults; apply_settings() overrides from strategy-settings.json each cycle
SPLIT_TRANCHE_KRW = 1_000_000
SPLIT_SYMBOL_KRW = 3_000_000
SCALE_MAX_ADDS = 2  # adds after the first buy per position (strategy setting scale_max_adds, user 2026-11-17)
# Engine-sized splits (user 2026-11-17 "jev, vibe 트레이딩 데이터 판단에 의해서 조절해서 분할 매수/매도"):
ADD_CONF_SIZING = True      # an add must beat the last buy (add_reason)
ADD_SIZE_PCT = 50           # ... and buys this % of a tranche (strategy setting add_size_pct)
PARTIAL_SELL_CONF = 0.55    # a Jev SELL at this confidence (under sell_confidence) sells part of the position once
PARTIAL_SELL_PCT = 33       # ... this % of it
SPLIT_TAKE_PROFIT = Decimal('1.01')
# User-approved 2026-11-11 13:5x: Jev entry confidence 0.6 (was 0.7; 0.7 passed 2 of 93 calls that day). Holding SELL stays 0.7.
BAR_PROBLEMS = {'paper signal bars unavailable': 'quote_error', 'bar response invalid': 'bar_response_invalid',
                'bars missing': 'bars_missing', 'insufficient bars': 'insufficient_bars', 'stale bars': 'stale_bars'}
ETF7_JEV_ENTRY_MIN = 0.5
# Watchlist strength entry (user approval 2026-11-12): the stock's own strength stands in for the index direction.
# Jev's watchlist probabilities are shared among the candidates (they sum to 1), so a fixed 0.6 let at most one stock
# qualify; user decision 2026-11-12: any of Jev's top 3 picks with at least 0.15. Defaults; apply_settings() reads the
# dashboard values (strength_top_n 0 turns the strength entry off).
STRENGTH_TOP_N, STRENGTH_JEV_MIN, STRENGTH_CHANGE = 3, 0.15, (2.0, 25.0)  # rank, probability floor; today's % (not near +30%)
ETF7_JEV_SELL_MIN = 0.7
AUTO = True  # the shared paper account's auto switch (accounts.json 'paper-shared')
STOP_LOSS = Decimal('0.98')
TAKE_PROFIT_ALL = Decimal('1.02')
_APPLIED_SETTINGS = None


def apply_settings(root):
    """Load the dashboard thresholds for this cycle (module-level, single-threaded runner); journal a change."""
    global SPLIT_STEP, SPLIT_TAKE_PROFIT, ETF7_JEV_ENTRY_MIN, ETF7_JEV_SELL_MIN, STOP_LOSS, TAKE_PROFIT_ALL, _APPLIED_SETTINGS
    global AUTO, SPLIT_TRANCHE_KRW, SPLIT_SYMBOL_KRW, TREND_HALF_PCT, TREND_TRAIL_PCT, OVERNIGHT_MAX_KRW, SWING_MAX_DAYS
    global STRENGTH_TOP_N, STRENGTH_JEV_MIN, STRENGTH_CHANGE
    global IPO_DAYS, IPO_SIZE_PCT, IPO_NEAR_HIGH_PCT, IPO_STOP_PCT, IPO_HALF_PCT, IPO_TRAIL_PCT
    global WEAK_HALF_PCT, WEAK_ALL_PCT, DIRECTION_SWITCH, ENTRY_VOLUME_RATIO, SHARED_SINGLE_ORDER_KRW, ETF7_MAX_ORDER_QTY, PAPER_MAX_TOTAL_QTY
    global SCALE_MAX_ADDS, ADD_CONF_SIZING, ADD_SIZE_PCT, PARTIAL_SELL_CONF, PARTIAL_SELL_PCT, REENTRY_GUARD, REENTRY_ALLOW, LOSING_STREAK_GUARD, AVERAGING_DOWN_GUARD, OPPOSITE_GUARD, DIRECTION_CONFIRM
    global RISING_CHECK, IPO_ADD_GUARD, IPO_MAX_POSITIONS, ENTRY_CUTOFF, FLATTEN_AT, SESSION_CLOSE, OVERSOLD
    global IPO_SLOT, IPO_DAILY_MAX, IPO_TIME_STOP_MIN, STRENGTH_SLOT, STRENGTH_SLOT_MAX, STRENGTH_SLOT_DAILY, CAREFUL
    from strategy_settings import read_settings
    from accounts import load as load_accounts
    settings, status = read_settings(root)
    if status == 'invalid':
        settings = {**settings, 'scale_signal_add_on': 0, 'scale_step_add_on': 0, 'scale_profit_rebuy_on': 0}
    account = load_accounts(Path(root).parent / 'accounts.json')['paper-shared']
    if account.get('kind') != 'paper':
        from accounts import DEFAULTS
        account = {**DEFAULTS['accounts']['paper-shared'], 'auto': False}
    AUTO = account['auto']
    SPLIT_TRANCHE_KRW, SPLIT_SYMBOL_KRW = account['budget']['first_buy_krw'], account['budget']['per_symbol_krw']
    from accounts import paper_limits
    limits = paper_limits(account)  # the order guard enforces the same values; sizing under them avoids refusals
    SHARED_SINGLE_ORDER_KRW = limits['order_max_krw']
    ETF7_MAX_ORDER_QTY = setting('paper_max_order_qty')
    PAPER_MAX_TOTAL_QTY = setting('paper_max_total_qty')
    pct = lambda key: Decimal(str(settings[key])) / 100
    SPLIT_STEP, SPLIT_TAKE_PROFIT = pct('add_step_pct'), 1 + pct('take_profit_half_pct')
    STOP_LOSS, TAKE_PROFIT_ALL = 1 - pct('stop_loss_pct'), 1 + pct('take_profit_all_pct')
    ETF7_JEV_ENTRY_MIN, ETF7_JEV_SELL_MIN = settings['entry_confidence'], settings['sell_confidence']
    TREND_HALF_PCT, TREND_TRAIL_PCT = settings['trend_half_pct'], settings['trend_trail_pct']
    OVERNIGHT_MAX_KRW = int(settings['overnight_max_manwon'] * 10_000)
    SWING_MAX_DAYS = int(settings['swing_max_days'])
    STRENGTH_TOP_N, STRENGTH_JEV_MIN = int(settings['strength_top_n']), settings['strength_min_prob']
    STRENGTH_CHANGE = (settings['strength_change_min'], settings['strength_change_max'])
    IPO_DAYS, IPO_SIZE_PCT = int(settings['ipo_days']), settings['ipo_size_pct']
    IPO_NEAR_HIGH_PCT, IPO_STOP_PCT = settings['ipo_near_high_pct'], settings['ipo_stop_pct']
    IPO_HALF_PCT, IPO_TRAIL_PCT = settings['ipo_half_pct'], settings['ipo_trail_pct']
    WEAK_HALF_PCT, WEAK_ALL_PCT = settings['weak_half_pct'], settings['weak_all_pct']
    DIRECTION_SWITCH = bool(settings['direction_switch'])
    ENTRY_VOLUME_RATIO = settings['entry_volume_ratio']
    REENTRY_GUARD, LOSING_STREAK_GUARD = bool(settings['reentry_guard']), bool(settings['losing_streak_guard'])
    REENTRY_ALLOW = bool(settings.get('reentry_allow', 1))
    CAREFUL = {'on': bool(settings.get('careful_reentry_on', 1)), 'vwap': float(settings.get('careful_reentry_vwap_pct', 2.0)),
               'range': float(settings.get('careful_reentry_range_pct', 85)), 'conf': float(settings.get('careful_reentry_conf', 0.6)),
               'size': float(settings.get('careful_reentry_size_pct', 50))}
    SCALE_MAX_ADDS = int(settings.get('scale_max_adds', 2))
    ADD_CONF_SIZING = bool(settings.get('add_conf_sizing_on', 1))
    ADD_SIZE_PCT = float(settings.get('add_size_pct', 50))
    PARTIAL_SELL_CONF, PARTIAL_SELL_PCT = settings.get('partial_sell_conf', 0.55), settings.get('partial_sell_pct', 33)
    AVERAGING_DOWN_GUARD, OPPOSITE_GUARD = bool(settings['averaging_down_guard']), bool(settings['opposite_guard'])
    DIRECTION_CONFIRM = bool(settings['direction_confirm'])
    RISING_CHECK, IPO_ADD_GUARD = bool(settings['rising_check']), bool(settings['ipo_add_guard'])
    IPO_MAX_POSITIONS = int(settings['ipo_max_positions'])
    IPO_SLOT, IPO_DAILY_MAX = bool(settings.get('ipo_slot_on', 1)), int(settings.get('ipo_daily_max', 2))
    IPO_TIME_STOP_MIN = int(settings.get('ipo_time_stop_min', 60))
    STRENGTH_SLOT = bool(settings.get('strength_slot_on', 0))
    STRENGTH_SLOT_MAX, STRENGTH_SLOT_DAILY = int(settings.get('strength_slot_max', 2)), int(settings.get('strength_slot_daily', 3))
    OVERSOLD = {k: v for k, v in settings.items() if k.startswith('oversold_')}
    import session_times
    clock = session_times.times(settings)
    ENTRY_CUTOFF, FLATTEN_AT, SESSION_CLOSE = clock['cutoff'], clock['flatten'], clock['close']
    applied = {**settings, 'auto_on': float(AUTO),
               'first_buy_krw': SPLIT_TRANCHE_KRW, 'per_symbol_krw': SPLIT_SYMBOL_KRW,
               'max_order_qty': ETF7_MAX_ORDER_QTY, 'max_total_qty': PAPER_MAX_TOTAL_QTY, 'scale_policy_version': 2, **limits}
    if (applied, status) != _APPLIED_SETTINGS:
        _APPLIED_SETTINGS = (applied, status)
        journal_event(root, 'strategy_settings', settings=applied, status=status)
    return settings


def price_exit(avg, bid):
    """Full exit at the stop or the final take-profit (2%/2% unless the dashboard changed them)."""
    exit_signal(avg, bid)  # keeps its input validation
    base, current = Decimal(str(avg)), Decimal(str(bid))
    return current <= base * STOP_LOSS or current >= base * TAKE_PROFIT_ALL


# Trend profile (strong or rising market, user request 2026-11-11): a wider first take-profit, then the rest rides
# with a trailing stop from its highest bid instead of a fixed +2% exit. Defaults come from strategy-settings.json.
TREND_HALF_PCT, TREND_TRAIL_PCT, OVERNIGHT_MAX_KRW = 2.0, 1.5, 3_000_000
# 'paper broker page unavailable': NH paper read failed 3 times in a row (11/13 14:34 the runner died and missed the 14:40
# flatten); a pending order is re-reconciled next cycle and blocks new orders, so retrying the read is safe.
TRANSIENT_READ_ERRORS = {'quote unavailable', 'invalid bid', 'quote time unavailable or stale', 'paper signal bars unavailable',
                         'paper broker page unavailable'}
TRANSIENT_READ_LIMIT = setting('read_failure_limit')  # consecutive cycles; then the runner stops as before
# New-listing entry (user 2026-11-12); defaults, apply_settings() reads the dashboard values.
IPO_DAYS, IPO_SIZE_PCT, IPO_NEAR_HIGH_PCT, IPO_STOP_PCT, IPO_HALF_PCT, IPO_TRAIL_PCT = 2, 50, 3.0, 3.0, 3.0, 2.0
WEAK_HALF_PCT, WEAK_ALL_PCT, DIRECTION_SWITCH = 1.0, 2.0, True  # weak-market take-profit caps; switch side (settings)
IPO_START = setting('ipo_start')  # let the listing-day open settle
IPO_SLOT, IPO_DAILY_MAX, IPO_TIME_STOP_MIN = True, 2, 60  # new-listing slot (user 2026-11-17); apply_settings() reads them
STRENGTH_SLOT, STRENGTH_SLOT_MAX, STRENGTH_SLOT_DAILY = False, 2, 3  # strength slot (user 2026-11-17, off by default)
IPO_MAX_SCAN = setting('ipo_max_scan')  # newest listings quoted per cycle
SWING_MAX_DAYS = 5  # sessions a carried closing trade may keep riding (strategy setting swing_max_days)
LEVERAGED = {'122630', '233740', '494310'}  # 2x ETFs move about twice as far, so their trend levels are scaled
LEVERAGE_SCALE = 1.5
_OVERNIGHT_LOGGED = set()
_HOLIDAYS = {}  # year -> (holiday set or None after a failed read, time.monotonic() of the read)
ENTRY_CUTOFF = '14:30'  # no new entries or split adds after this (Asia/Seoul); dashboard setting (session_times)
# Every holding exits through the protective path from here. One sell per cycle plus a reconcile cycle is about
# two minutes per order, so the session runs at least 20 minutes past it (SESSION_CLOSE, never past 15:18).
FLATTEN_AT = '14:40'
SESSION_CLOSE = '15:00'
RISING_CHECK, IPO_ADD_GUARD, IPO_MAX_POSITIONS = True, True, 1  # dashboard 제한 규칙 (strategy_settings)
OVERSOLD = {}  # 낙폭과대 반등 settings (oversold_*), set by apply_settings each cycle; empty = route off
_OVERSOLD_LOGGED, _OVERSOLD_ASKED = set(), {}  # journal once per (day, code, stabilized); last answer per (day, code)
OVERSOLD_REASK = timedelta(minutes=5)  # an engine that said WAIT is asked again after this, or at once on a further 1% fall


def refresh_clock(root):
    """ENTRY_CUTOFF, FLATTEN_AT and SESSION_CLOSE from the saved settings (read before every cycle's session check)."""
    global ENTRY_CUTOFF, FLATTEN_AT, SESSION_CLOSE
    import session_times
    clock = session_times.load(root)
    ENTRY_CUTOFF, FLATTEN_AT, SESSION_CLOSE = clock['cutoff'], clock['flatten'], clock['close']


def in_session(local):
    from session_times import SESSION_OPEN
    return local.weekday() < 5 and SESSION_OPEN <= local.strftime('%H:%M') < SESSION_CLOSE


def in_observation_session(local):
    from session_times import MARKET_OPEN
    return local.weekday() < 5 and MARKET_OPEN <= local.strftime('%H:%M') < SESSION_CLOSE


def order_session(local):
    from session_times import order_start
    return in_session(local) and local.strftime('%H:%M') >= order_start(local.date())


def preclose_from():
    """Holding reviews in the last 10 minutes of the session are 'preclose' (14:50 with the 15:00 close; the close moves
    with the flatten setting)."""
    import session_times
    return session_times.clock_text(session_times.minutes(SESSION_CLOSE) - 10)


def session_close_at(local):
    hour, minute = map(int, SESSION_CLOSE.split(':'))
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)
KOSDAQ_CODES = {'229200', '233740', '251340'}


def exit_plans(root):
    """{symbol: plan} fixed when each position was entered; positions without one use the current settings."""
    try:
        data = json.loads((Path(root) / 'exit-plans.json').read_text())
    except (OSError, ValueError):
        return {}
    numeric = lambda v: type(v) in (int, float) and math.isfinite(v) and 0 < v < 50
    return {code: plan for code, plan in (data.items() if isinstance(data, dict) else [])
            if isinstance(plan, dict) and all(numeric(plan.get(k)) for k in ('stop_pct', 'half_pct', 'all_pct'))
            and plan['half_pct'] <= plan['all_pct']  # equal = one full exit at the data target
            and (plan.get('profile') not in ('trend', 'ipo') or all(numeric(plan.get(k)) for k in ('trend_half_pct', 'trend_trail_pct')))}


# Defensive full-exit target (user request 2026-11-11: in falling/sideways markets take the whole profit at a
# level the data supports): a share of the symbol's median rise from the open over the last 20 sessions.
TARGET_FACTOR = {'하락장': 0.4, '횡보장': 0.5}  # other regimes 0.6; a falling market takes profit sooner
TARGET_BOUNDS = (0.8, 3.0)                    # %: above round-trip costs, below an unrealistic intraday reach
OVERNIGHT_VOLUME_RATIO = 1.5                  # today's volume so far vs the 20-session average
_DAILY = {}


def stats_from_daily(rows, today):
    """Daily context from NH rows (newest first): median rise from the open over the last 20 past sessions, today's
    volume against their average, today's open/high/low/price and the 5/20-day averages ending today.
    None with under 10 past sessions."""
    past, closes, current = [], [], None
    for row in rows:
        try:
            o, h, l, c, v = (float(row[k]) for k in ('stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol'))
        except (KeyError, TypeError, ValueError):
            continue
        if min(o, l, c) <= 0 or h < max(o, c) or l > min(o, c):
            continue
        if str(row.get('bsop_date')) == today.strftime('%Y%m%d'):
            current = {'open': o, 'high': h, 'low': l, 'price': c, 'volume': v}
        elif len(past) < 20:
            past.append(((h / o - 1) * 100, v))
        if len(closes) < 20:
            closes.append(c)
    if len(past) < 10:
        return None
    ups = sorted(up for up, _ in past)
    average = sum(v for _, v in past) / len(past)
    return {'median_up_pct': round(ups[len(ups) // 2], 3), 'sessions': len(past),
            'volume_ratio': round(current['volume'] / average, 2) if current and average > 0 else None,
            'today': current, 'ma5': sum(closes[:5]) / 5 if len(closes) >= 5 else None,
            'ma20': sum(closes) / 20 if len(closes) >= 20 else None}


def daily_stats(read, symbol, now):
    """Cached once per symbol and day for exit targets; today's volume is refreshed when asked after 14:00."""
    # entries reuse one read per day; from 14:00 today's high/low/volume matter, so the read is at most 3 minutes old
    key = (now.date(), symbol, f'{now.hour}:{now.minute // 3}' if now.hour >= 14 else 'am')
    if key not in _DAILY:
        try:
            response = read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {
                'market_cd': 'KRX', 'iem_cd': symbol, 'gubun': '1', 'array_cnt': '25', 'view_main_yn': 'Y', 'today_cls_code': '0'}})
            data = response.get('data') or {} if response.get('status') == 'ok' else {}
            rows = data.get('Output_1') if data.get('rsp_cd') == '00000' else None
            _DAILY[key] = stats_from_daily(rows, now.date()) if isinstance(rows, list) else None
        except Exception:  # a failed read only means no data-based level: never stop the runner before an exit
            _DAILY[key] = None
    return _DAILY[key]


def defensive_target(stats, regime):
    """Full-exit take-profit % for a defensive position, or None to keep the configured +1%/+2% split."""
    if not stats:
        return None
    pct = stats['median_up_pct'] * TARGET_FACTOR.get(regime, 0.6)
    return round(min(max(pct, TARGET_BOUNDS[0]), TARGET_BOUNDS[1]), 2)


def new_plan(symbol, profile='defensive', peak=None, target=None, basis=None):
    """Exit levels in force now; a trend plan replaces the fixed final take-profit with a trailing stop, and a
    defensive plan with a data target sells everything there (half = all, no partial sale)."""
    if profile == 'oversold':  # 낙폭과대 반등: one full exit when part of the gap is recovered, its own stop, no adds
        stop = float((basis or {}).get('stop_pct') or OVERSOLD.get('oversold_stop_pct', 3.0))
        hold = (basis or {}).get('hold_days')  # 선별 v2 keeps its own sessions ...
        half = (basis or {}).get('half_tp_pct') or target  # ... and sells half at its first target, the rest at the second
        return {'opened': datetime.now(KST).isoformat(), 'profile': 'oversold', 'stop_pct': stop,
                'half_pct': float(half), 'all_pct': float(target), 'target_basis': basis,
                'hold_days': int(OVERSOLD.get('oversold_hold_days', 0) if hold is None else hold), 'peak': peak, 'breakeven_after_half': False,
                'flatten_at': FLATTEN_AT}
    if profile == 'ipo':  # new listing: own stop, half sale and trailing stop; never added to, never carried overnight
        return {'opened': datetime.now(KST).isoformat(), 'profile': 'ipo', 'stop_pct': float(IPO_STOP_PCT),
                'half_pct': float(IPO_HALF_PCT), 'all_pct': float(IPO_HALF_PCT), 'target_basis': basis,
                'trend_half_pct': float(IPO_HALF_PCT), 'trend_trail_pct': float(IPO_TRAIL_PCT),
                'peak': peak, 'breakeven_after_half': True, 'flatten_at': FLATTEN_AT}
    scale = LEVERAGE_SCALE if symbol in LEVERAGED else 1
    half, all_ = float((SPLIT_TAKE_PROFIT - 1) * 100), float((TAKE_PROFIT_ALL - 1) * 100)
    if target is not None:
        half = all_ = float(target)
    if profile == 'inverse_crash':  # user 2026-11-20: a crash-day inverse takes a moderate profit, never a trailing trend
        half, all_ = float(INVERSE['crash_half_pct']), float(INVERSE['crash_all_pct'])
    stop = float((1 - STOP_LOSS) * 100)
    if profile in ('inverse_crash', 'inverse_quick'):
        stop = min(stop, float(INVERSE['stop_pct']))
    return {'opened': datetime.now(KST).isoformat(), 'profile': profile, 'stop_pct': stop,
            'half_pct': half, 'all_pct': all_, 'target_basis': basis,
            'trend_half_pct': round(TREND_HALF_PCT * scale, 3), 'trend_trail_pct': round(TREND_TRAIL_PCT * scale, 3),
            'peak': peak, 'breakeven_after_half': True, 'flatten_at': FLATTEN_AT}


def write_exit_plans(root, plans):
    write_json(Path(root) / 'exit-plans.json', plans, indent=None)


def trend_mode(view, index, short):
    """Strong or rising tape in the position's direction: long in 상승장 (not 약세 today) or on a 강세 day; mirrored for inverse."""
    item = (view or {}).get(index) or {}
    regime, tone = item.get('regime'), item.get('today_tone')
    if short:
        return (regime == '하락장' and tone != '강세') or tone == '약세'
    return (regime == '상승장' and tone != '약세') or tone == '강세'


HOLIDAY_RETRY_S = 600


def krx_holiday_table(year):
    """KRX holidays of `year`, or None while the table is unknown. A failed read is kept HOLIDAY_RETRY_S seconds instead
    of being retried on every call (sessions_held asks day by day, and the dashboard asks per new listing; each read can
    wait 15 seconds). Unknown means the shorter hold: every weekday counts as a session (sessions_held) and the next day
    is not known to open (next_session_open: sell before the close)."""
    known = _HOLIDAYS.get(year)
    if known is None or (known[0] is None and time.monotonic() - known[1] >= HOLIDAY_RETRY_S):
        try:
            from kr_swing_v5_daily_data import krx_holidays
            known = (krx_holidays({year}), time.monotonic())
        except Exception:
            known = (None, time.monotonic())
        _HOLIDAYS[year] = known
    return known[0]


def next_session_open(today):
    """True only when the next calendar day is a KRX trading day (an unknown holiday table: False, see krx_holiday_table)."""
    tomorrow = today + timedelta(days=1)
    table = None if tomorrow.weekday() >= 5 else krx_holiday_table(tomorrow.year)
    return table is not None and tomorrow not in table


# Carrying a position past the close is the exception (user, 2026-11-11: most positions close the same day; only a
# genuine swing setup may stay). Every rule must pass; the first failing code is journaled. The dashboard shows this list.
OVERNIGHT_RULES = (
    ('not_trend_profile', '추세형 포지션'),
    ('regime_not_rising', '지수 상승장 + 오늘 강세 (인버스는 하락장 + 약세)'),
    ('breadth_not_broad', '상승 종목이 하락 종목의 2배 이상 (인버스는 반대)'),
    ('flows_not_supporting', '지수 외국인·기관·프로그램 모두 순매수 (인버스는 모두 순매도)'),
    ('not_first_target', '1차 목표 도달 및 1차 익절 수량(그때 남은 수량의 절반, 올림) 체결 확인'),
    ('volume_not_high', '종목 거래량이 20일 평균의 1.5배 이상 (14:40 누적 기준)'),
    ('weak_close', '시가 위, 오늘 고가-저가 범위의 상위 20% 안에서 마감 중'),
    ('not_uptrend', '현재가 > 5일 평균 > 20일 평균 (종목 자체 상승 추세)'),
    ('jev_not_supportive', 'Jev 최근 30분 보유 의견이 HOLD, 신뢰도 0.60 이상'),
    ('overnight_limit', '이월 금액 합계가 장 마감 보유 한도 이내'),
    ('market_closed_next_day', '다음 날이 거래일 (KRX 휴장표, 확인 실패 시 정리)'),
)
OVERNIGHT_JEV_MIN, OVERNIGHT_BREADTH = 0.6, 2.0


def overnight_reason(plan, view, index, short, avg, bid, protected, budget_left, cost, today, stats=None, jev_view=None, *, first_target_filled=False, require_jev=True):
    """None when the holding may stay past the close (closing-price trade); otherwise the first OVERNIGHT_RULES code that fails."""
    item = (view or {}).get(index) or {}
    regime, tone = item.get('regime'), item.get('today_tone')
    flows = {**(item.get('investor_net_100m_krw') or {}), '프로그램': (item.get('program_net_100m_krw') or {}).get('전체')}
    rise, fall = (list(item.get('advancers_decliners') or []) + [None, None])[:2]
    sign = -1 if short else 1
    stats, today_bar = stats or {}, (stats or {}).get('today') or {}
    jev_choice, jev_confidence = jev_view if isinstance(jev_view, tuple) else (None, None)
    price = Decimal(str(bid))
    checks = (
        ('not_trend_profile', bool(plan) and plan.get('profile') == 'trend'),
        ('regime_not_rising', (regime, tone) == (('하락장', '약세') if short else ('상승장', '강세'))),
        ('breadth_not_broad', all(type(v) in (int, float) and v > 0 for v in (rise, fall))
                              and (fall if short else rise) >= OVERNIGHT_BREADTH * (rise if short else fall)),
        ('flows_not_supporting', all(type(flows.get(k)) in (int, float) and sign * flows[k] > 0 for k in ('외국인', '기관', '프로그램'))),
        ('not_first_target', protected and first_target_filled is True and bool((plan or {}).get('peak'))
                             and Decimal(str(plan['peak'])) >= Decimal(str(avg)) * (1 + Decimal(str(plan.get('trend_half_pct', 0))) / 100)),
        ('volume_not_high', (stats.get('volume_ratio') or 0) >= OVERNIGHT_VOLUME_RATIO),
        ('weak_close', bool(today_bar) and today_bar['high'] > today_bar['low'] and price > Decimal(str(today_bar['open']))
                       and (price - Decimal(str(today_bar['low']))) / Decimal(str(today_bar['high'] - today_bar['low'])) >= Decimal('0.8')),
        ('not_uptrend', bool(stats.get('ma5')) and bool(stats.get('ma20'))
                        and price > Decimal(str(stats['ma5'])) > Decimal(str(stats['ma20']))),
        ('jev_not_supportive', not require_jev or (jev_choice == 'HOLD' and type(jev_confidence) in (int, float) and jev_confidence >= OVERNIGHT_JEV_MIN)),
        ('overnight_limit', cost <= budget_left),
    )
    failed = next((code for code, ok in checks if not ok), None)
    if failed:
        return failed
    return None if next_session_open(today) else 'market_closed_next_day'  # last: may need the network


# A closing trade that was carried overnight keeps riding a strong tape on later days under these lighter checks
# (the first night needs every OVERNIGHT_RULES check; user decision 2026-11-12: swing-able names may stay more than two days).
SWING_RULES = (
    ('swing_days_exceeded', '스윙 최대 보유일 이내 (설정 swing_max_days)'),
    ('regime_not_rising', '지수 국면이 받쳐 줌: 상승장이고 오늘 약세 아님 (인버스는 하락장 · 강세 아님)'),
    ('not_uptrend', '현재가 > 5일 평균 > 20일 평균 (종목 상승 추세 유지)'),
    ('jev_selling', 'Jev 최근 30분 보유 의견이 SELL(신뢰도 0.60 이상)이 아님'),
    ('overnight_limit', '이월 금액 합계가 장 마감 보유 한도 이내'),
    ('market_closed_next_day', '다음 날이 거래일'),
)


def is_trading_day(day):
    """A KRX session: a weekday that is not a known holiday (an unknown holiday table counts weekdays, see krx_holiday_table)."""
    if day.weekday() >= 5:
        return False
    table = krx_holiday_table(day.year)
    return table is None or day not in table


def sessions_held(opened, today):
    """KRX sessions from the entry day to today (the entry day is 0); holidays do not count."""
    day, count = opened, 0
    while day < today:
        day += timedelta(days=1)
        count += is_trading_day(day)
    return count


def swing_reason(plan, view, index, short, bid, today, budget_left, cost, stats=None, jev_view=None):
    """None when a trend position carried from an earlier day may stay another night; else the first SWING_RULES code
    that fails. Positions entered today must pass the full OVERNIGHT_RULES instead."""
    item = (view or {}).get(index) or {}
    regime, tone = item.get('regime'), item.get('today_tone')
    stats, price = stats or {}, Decimal(str(bid))
    jev_choice, jev_confidence = jev_view if isinstance(jev_view, tuple) else (None, None)
    opened = datetime.fromisoformat(plan['opened']).astimezone(KST).date()
    checks = (
        ('swing_days_exceeded', sessions_held(opened, today) + 1 <= SWING_MAX_DAYS),
        ('regime_not_rising', (regime == '하락장' and tone != '강세') if short else (regime == '상승장' and tone != '약세')),
        ('not_uptrend', bool(stats.get('ma5')) and bool(stats.get('ma20')) and price > Decimal(str(stats['ma5'])) > Decimal(str(stats['ma20']))),
        ('jev_selling', not (jev_choice == 'SELL' and type(jev_confidence) in (int, float) and jev_confidence >= OVERNIGHT_JEV_MIN)),
        ('overnight_limit', cost <= budget_left),
    )
    failed = next((code for code, ok in checks if not ok), None)
    if failed:
        return failed
    return None if next_session_open(today) else 'market_closed_next_day'


def latest_holding_view(root, symbol, now, minutes=30):
    """(choice, confidence) of the last Jev HOLD/SELL view of this holding in the recent events, or None."""
    try:
        with (Path(root) / 'events.jsonl').open('rb') as f:
            f.seek(max(0, f.seek(0, 2) - 400_000))
            lines = f.read().decode('utf-8', 'ignore').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
            if event.get('event') == 'jev_holding_decision' and event.get('symbol') == symbol:
                at = datetime.fromisoformat(event['time'])
                fresh = timedelta(0) <= now - at <= timedelta(minutes=minutes)
                return (event.get('choice'), event.get('confidence')) if fresh else None
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return None


ENTRY_VOLUME_RATIO = 1.2  # last 5 one-minute bars' volume vs the 5 before them; setting entry_volume_ratio (0 = no check)
# Restriction switches (strategy_settings, dashboard 제한 규칙; all on by default; apply_settings sets them every cycle)
REENTRY_GUARD = REENTRY_ALLOW = LOSING_STREAK_GUARD = AVERAGING_DOWN_GUARD = OPPOSITE_GUARD = DIRECTION_CONFIRM = True
CAREFUL = {'on': True, 'vwap': 2.0, 'range': 85.0, 'conf': 0.6, 'size': 50.0}  # careful re-entry (user 2026-11-18)
_CAREFUL_REVIEW = {}  # code -> (monotonic time, today's round trips, verdict) of the last Jev re-entry review
# (day, code) of exit requests the direction switch made: sold as the engine's exit, not the user's (r3 X8).
# shortcut: in-process only, so a restart between the request and its sale records it as manual; persist it if that matters.
_SWITCH_EXITS = set()
# after N losing round trips in a row today, no new entry for M minutes (운영 조정값)
LOSING_STREAK = setting('losing_streak')
COOLDOWN = timedelta(minutes=setting('losing_cooldown_min'))


def latest_mover_rows(root, now, max_age=timedelta(minutes=12)):
    """{code: row} of the ranking service's latest NH quotes (movers.json, every 5 minutes) plus '_as_of' 'HH:MM';
    {} when missing or stale. User 2026-11-18: the watchlist (every 30 minutes) left a candidate's change and
    turnover up to 45 minutes old in Jev's choices."""
    try:
        data = json.loads((Path(root) / 'movers.json').read_text())
        at = datetime.fromisoformat(data['time'])
        if not timedelta(0) <= now - at <= max_age or not isinstance(data.get('rows'), dict):
            return {}
        return {**data['rows'], '_as_of': at.astimezone(KST).strftime('%H:%M')}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def with_latest_figures(details, row, as_of):
    """The candidate's price, change and turnover from the latest ranking quote when there is one, with its time. A row the
    ranking service priced from Kiwoom (source 'kiwoom', 2026-11-20 NH call plan P2) is not laid over Jev's NH figures."""
    if not isinstance(row, dict) or type(row.get('change_pct')) not in (int, float) or row.get('source') == 'kiwoom':
        return details
    return {**details, **{k: row[k] for k in ('price', 'change_pct', 'volume', 'value_mil') if row.get(k) is not None},
            'figures_as_of': as_of}


def stock_brief(details):
    """Compact watchlist facts for Jev: valuation, where the price sits in its 52-week range, flows and two headlines."""
    facts = details.get('facts') or {}
    brief = {k: facts[k] for k in ('PER', 'PBR', '업종', '외국인 보유율(%)') if facts.get(k) is not None}
    high, low, price = facts.get('52주 최고'), facts.get('52주 최저'), details.get('price')
    if all(type(v) in (int, float) for v in (high, low, price)) and high > low:
        brief['position_in_52w_range_pct'] = round((price - low) / (high - low) * 100, 1)
    if details.get('investor_daily_net_qty'):
        brief['investor_daily_net_qty'] = details['investor_daily_net_qty']
    brief['headlines'] = recent_stock_headlines(details.get('news'))
    return brief


def watchlist_brief(code, details):
    """stock_brief of one watchlist stock with its own news refreshed every 5 minutes (user 2026-11-18)."""
    from watchlist import fresh_stock_news
    return stock_brief({**details, 'news': fresh_stock_news(code, details.get('name'), datetime.now(KST)) or details.get('news')})


def recent_stock_headlines(news, now=None, limit=2):
    """The stock's own headlines from the last etf_news.RECENT (90 min) with their age, newest first (user 2026-11-18:
    old news must not reach Jev; the watchlist keeps up to a day of them without a time, so Jev could not tell)."""
    from etf_news import RECENT
    now = now or datetime.now(KST)
    out = []
    for n in news if isinstance(news, list) else []:
        try:
            age = (now - datetime.fromisoformat(n['published'])).total_seconds() / 60
        except (KeyError, TypeError, ValueError):
            continue  # no time: cannot be shown as recent
        if 0 <= age <= RECENT.total_seconds() / 60:
            out.append((age, {'title': str(n.get('title'))[:80], 'age_min': round(age)}))
    return [row for _, row in sorted(out, key=lambda x: x[0])[:limit]]


def losing_streak(db, day, now):
    """Name of the cooldown reason when today's last LOSING_STREAK round trips all lost and the last closed recently."""
    from position_paths import positions
    if not isinstance(now, datetime_type) or now.tzinfo is None or now.utcoffset() is None:
        return 'losing_streak_history_unavailable'
    now = now.astimezone(KST)
    if now.strftime('%Y%m%d') != day:
        return 'losing_streak_history_unavailable'
    fills = []
    try:
        for row in db.execute('SELECT symbol, side, quantity, price, time FROM fills ORDER BY rowid'):
            at = datetime.fromisoformat(row[4])
            if at.tzinfo is None or at.utcoffset() is None:
                return 'losing_streak_history_unavailable'
            if at <= now:
                fills.append((at, row))
        fills = [row for _, row in sorted(fills, key=lambda item: item[0])]
        quantities = {}
        for symbol, side, qty, price, _ in fills:
            if (not isinstance(symbol, str) or not symbol or side not in ('BUY', 'SELL')
                    or type(qty) is not int or qty <= 0 or type(price) not in (int, float)
                    or not math.isfinite(price) or price <= 0):
                return 'losing_streak_history_unavailable'
            quantities[symbol] = quantities.get(symbol, 0) + (qty if side == 'BUY' else -qty)
            if quantities[symbol] < 0:
                return 'losing_streak_history_unavailable'
    except (sqlite3.Error, TypeError, ValueError, OverflowError):
        return 'losing_streak_history_unavailable'
    closed = sorted((e for e in positions(fills) if e.get('closed') and e['closed'].astimezone(KST).strftime('%Y%m%d') == day),
                    key=lambda e: e['closed'])[-LOSING_STREAK:]
    if (len(closed) == LOSING_STREAK and all(e['realized'] < 0 for e in closed)
            and timedelta(0) <= now - closed[-1]['closed'] < COOLDOWN):
        return 'losing_streak_cooldown'
    return None


def reentry_floors(db, root, day):
    """User 2026-11-13: {code: last exit price} for every stock whose round trip closed today, whatever the exit
    (first: losses and trailing stops, after 11/13 SFA반도체 was re-bought twice below its exit for −31,100원; then any
    exit, after the 16:15 review found 원익QnC re-buy tries below its take-profit exit, 11/13 #2). The
    watchlist-strength route buys such a stock again today only above that price. `root` is kept for the callers."""
    from position_paths import positions
    fills = db.execute('SELECT symbol, side, quantity, price, time FROM fills ORDER BY time').fetchall()
    last = {}
    for e in positions(fills):
        if e.get('closed') and e['closed'].astimezone(KST).strftime('%Y%m%d') == day:
            if e['symbol'] not in last or e['closed'] > last[e['symbol']]['closed']:
                last[e['symbol']] = e
    return {code: e['exit_price'] for code, e in last.items()}


def reentry_check(code, owned, source, closed_today, allow):
    """(is a same-day re-entry, block reason or None). User 2026-11-17: with re-entry switched off a stock whose round
    trip closed today is not bought again today; 낙폭과대 entries keep their own rules and a held stock is an add."""
    reentry = code not in owned and source not in V2_SOURCES and source != 'oversold' and code in (closed_today or {})
    return reentry, ('reentry_disabled' if reentry and not allow else None)


def careful_due(code, owned, source, closed_today, view, index):
    """The regime that makes this a careful re-entry (user 2026-11-18), or None: a Jev or watchlist-strength buy of a
    long whose round trip closed today, unless the regime is known to back it (상승장). An unknown regime ('판단 불가', or
    no fresh market view) is careful too: missing data waits (r3 E11). An inverse is never one: it buys only through
    inverse_gate, which needs a 하락장 where every view agrees (r3 E12)."""
    from etf_universe import SHORT
    if (not CAREFUL['on'] or source not in ('jev', 'watchlist_strength') or code in {ETF7_SYMBOLS[n] for n in SHORT}
            or not reentry_check(code, owned, source, closed_today, True)[0]):
        return None
    regime = ((view or {}).get(index) or {}).get('regime') or '판단 불가'
    return None if regime == '상승장' else regime


def careful_tape(name, rows, quote=None):
    """(block reason or None, session summary): not chasing (at most CAREFUL['vwap'] % above today's VWAP and under
    CAREFUL['range'] % of today's range; 11/18 043260 was +2.4%) and not weakening (not under VWAP; 11/13 036540 twice).
    Today's open, high, low and VWAP come from NH's `quote` when given (jev_context.note_quote), else from the bars
    seen. Unknown session (under 10 bars) does not buy."""
    import jev_context
    jev_context.remember({name: rows})
    jev_context.note_quote(name, quote)
    summary = jev_context.summary(name)
    if not summary or summary.get('vs_vwap_pct') is None:
        return 'careful_reentry_no_session', summary
    if summary['vs_vwap_pct'] > CAREFUL['vwap'] or summary['range_position_pct'] >= CAREFUL['range']:
        return 'careful_reentry_chase', summary
    if summary['vs_vwap_pct'] < 0:
        return 'careful_reentry_weak', summary
    return None, summary


def today_trades(db, code, day):
    """Today's round trips of one code for the re-entry review: entry/exit time and price, return and how it ended."""
    from position_paths import positions
    fills = db.execute('SELECT symbol, side, quantity, price, time FROM fills WHERE symbol=? ORDER BY time', (code,)).fetchall()
    triggers = dict(db.execute("SELECT f.time, t.trigger FROM fills f LEFT JOIN sell_order_triggers t ON t.order_id = f.order_id "
                               "WHERE f.symbol=? AND f.side='SELL'", (code,)).fetchall())
    out = []
    for e in positions(fills):
        if not e.get('closed') or e['closed'].astimezone(KST).strftime('%Y%m%d') != day:
            continue
        entry = e['spent'] / e['bought']
        out.append({'opened': e['opened'].astimezone(KST).strftime('%H:%M'), 'closed': e['closed'].astimezone(KST).strftime('%H:%M'),
                    'avg_entry': round(entry, 2), 'last_exit': e['exit_price'], 'buys': e['buys'], 'sells': e['sells'],
                    'return_pct': round((e['proceeds'] / e['sold'] / entry - 1) * 100, 2) if e['sold'] else None,
                    'realized_krw': round(e['realized']), 'exit_trigger': triggers.get(e['closed'].isoformat())})
    return out


def careful_review(root, db, day, name, code, bars, news, investor, regime, summary, cycle_id, details=None):
    """True when Jev's re-entry review says BUY at CAREFUL['conf'] or more; else the block reason. Asked at most every
    5 minutes per code while today's round trips stay the same (a trade closed since is asked again, r3 E2); a failed
    call does not buy. `details`: the watchlist entry of a stock, sent as the main entry call sends it (r3 E6)."""
    trades = today_trades(db, code, day)
    cached = _CAREFUL_REVIEW.get(code)
    if cached and cached[1] == len(trades) and time.monotonic() - cached[0] < 300:
        return cached[2]
    facts = {'regime': regime, 'today_trades': trades, 'session': summary,
             'rule': f"re-entry in a regime against the position: BUY needs confidence {CAREFUL['conf']} or more"}
    try:
        from nh_etf_auto_v2 import jev_reentry7
        extra = {'market_regime_themes_global': market_view(root), 'premarket_context': premarket_view(),
                 'own_track_record': track_record(root), 'trading_journal': trading_journal_brief(),
                 'market_playbook': market_playbook.brief('entry'),
                 'watchlist_details': {code: watchlist_brief(code, details)} if details else None}
        answer = jev_reentry7(bars, news, name, code, facts, investor=investor, extra_state={k: v for k, v in extra.items() if v} or None)
        said = answer['reentry']
        verdict = True if said['choice'] == 'BUY' and said['confidence'] >= CAREFUL['conf'] else 'careful_reentry_jev_wait'
        journal_event(root, 'careful_reentry_review', symbol=code, regime=regime, choice=said['choice'], confidence=said['confidence'],
                      permitted=verdict is True, session=summary, trades=facts['today_trades'], input_digest=answer.get('input_digest'),
                      evidence=answer.get('evidence') or None, cycle_id=cycle_id)
    except Exception as exc:
        verdict = 'careful_reentry_review_error'
        journal_event(root, 'careful_reentry_review', symbol=code, regime=regime, permitted=False, error_type=type(exc).__name__,
                      cycle_id=cycle_id)
    _CAREFUL_REVIEW[code] = (time.monotonic(), len(trades), verdict)
    return verdict


# Inverse ETFs (user 2026-11-18): bought only when every view agrees the market is falling, and off a crash day the
# position takes a small profit whole. Replay of 8 round trips 11/11~11/18: +93,833 as traded vs +161,642 with both rules
# (the one crash day, KOSPI200 -3.35%, made +154,553; the rest lost about 60,000).
INVERSE = {'min_drop_pct': 0.5, 'crash_pct': 1.5, 'quick_tp_pct': 0.3,
           # user 2026-11-20 ("인버스는 조건을 더 강화", 10:41 −4.4% 뒤 고가 근처 0.50 진입): the index still falling over the
           # last 30 minutes, Jev at least 0.6, the inverse not chased (≤ VWAP +1%, under 80% of today's range)
           'falling_30m_pct': -0.3, 'jev_min_conf': 0.6, 'vwap_pct': 1.0, 'range_pct': 80.0,
           # and not after the fall is done (user "이미 하락하고 따라 들어가는 건 위험"): the index down at most 3% today
           'late_drop_pct': 3.0,
           # a small side bet beside the stocks (user 2026-11-20 "인버스는 소액으로, 다른 종목도 함께"): this share of a tranche
           'size_pct': 50.0,
           # past late_drop_pct only a strong signal buys (user 2026-11-20 '강한 신호 예외'): the index still falling 0.5%+
           # over 30 minutes, Jev 0.8+, the inverse not above its VWAP, and a quarter of a tranche
           'late_fall_30m_pct': -0.5, 'late_jev_min_conf': 0.8, 'late_size_pct': 25.0,
           # and on a crash day it takes a moderate profit (user "적당히 수익내면 끊고"): half +1%, the rest +2%
           'crash_half_pct': 1.0, 'crash_all_pct': 1.0,  # 11:40 user: "적당히 수익내면 전량" — all at +1%
           # held: "시황 잘 보고 손실 안 보게" — out when the index rebounds (+0.3% over 30 minutes), once +0.5% is reached
           # it never turns into a loss (sold back at the average), and a 1% stop instead of 2%
           'rebound_exit_pct': 0.3, 'protect_at_pct': 0.5, 'stop_pct': 1.0}
OWN_EXIT_PROFILES = ('trend', 'ipo', 'oversold', 'inverse_quick', 'inverse_crash')  # plans the tape never upgrades to a trend plan


def inverse_gate(view, index):
    """None when every view backs an inverse buy on `index` ('코스피'/'코스닥'), else the first that does not: the
    daily regime is 하락장, the index is down at least INVERSE['min_drop_pct'] today and not rebounding over the last
    30 minutes, program trading is net selling, and the US (Nasdaq 100) fell in its last session. Missing (or stale,
    market_context) data refuses as 'inverse_market_unknown', not as a market verdict; a flat 0 is neither net selling
    nor a fall (r3 E5)."""
    item = (view or {}).get(index) or {}
    if item.get('regime') not in ('상승장', '횡보장', '하락장'):
        return 'inverse_market_unknown'
    if item['regime'] != '하락장':
        return 'inverse_regime_not_falling'
    change = item.get('today_change_pct')
    recent = (((view.get('index_intraday') or {}).get(index)) or {}).get('change_30m_pct')
    program = (item.get('program_net_100m_krw') or {}).get('전체')
    nasdaq = (view.get('global_last_daily_change_pct') or {}).get('나스닥100')
    if not all(type(v) in (int, float) for v in (change, recent, program, nasdaq)):
        return 'inverse_market_unknown'
    if change > -INVERSE['min_drop_pct']:
        return 'inverse_index_not_falling'
    if change < -INVERSE['late_drop_pct'] and recent > INVERSE['late_fall_30m_pct']:
        return 'inverse_too_late'
    if recent > 0:
        return 'inverse_index_rebounding'
    if recent > INVERSE['falling_30m_pct']:
        return 'inverse_index_not_falling_now'
    if program >= 0:
        return 'inverse_flows_not_selling'
    if nasdaq >= 0:
        return 'inverse_us_not_falling'
    return None


def inverse_late(view, index):
    """The index is past INVERSE['late_drop_pct'] today: only the strong-signal exception buys, smaller."""
    change = ((view or {}).get(index) or {}).get('today_change_pct')
    return type(change) in (int, float) and change < -INVERSE['late_drop_pct']


def inverse_tape(name, rows, quote=None, confidence=None, source=None, late=False):
    """Block reason or None for an inverse buy beyond inverse_gate (user 2026-11-20): a Jev pick needs confidence
    INVERSE['jev_min_conf'], and the inverse ETF must not be chased (at most INVERSE['vwap_pct'] % over today's VWAP and
    under INVERSE['range_pct'] % of today's range; an unknown session does not buy)."""
    floor = INVERSE['late_jev_min_conf'] if late else INVERSE['jev_min_conf']
    if source == 'jev' and not (type(confidence) in (int, float) and confidence >= floor):
        return 'inverse_low_confidence'
    import jev_context
    jev_context.remember({name: rows})
    jev_context.note_quote(name, quote)
    summary = jev_context.summary(name)
    if not summary or summary.get('vs_vwap_pct') is None or summary.get('range_position_pct') is None:
        return 'inverse_market_unknown'
    if summary['vs_vwap_pct'] > (0 if late else INVERSE['vwap_pct']) or summary['range_position_pct'] >= INVERSE['range_pct']:
        return 'inverse_chase'
    return None


def inverse_refusal(root, code, rows, name=None, read=None, confidence=None, source=None):
    """Why an inverse buy is refused now (inverse_gate, then inverse_tape on NH's fresh quote), or None. The cycle asks it
    before a Jev inverse pick takes the turn, so every refusal hands the turn on (11-20 13:22: a low-confidence INV pick
    was refused only in entry_block and 한진칼, the strength candidate, was never looked at)."""
    view, index = market_view(root), symbol_index(root, code)
    if gate := inverse_gate(view, index):
        return gate
    try:
        quoted = read.call_tool('get_stock_price', {'stock_code': code}) if read else {}
        quote = fresh_quote(quoted.get('data', {}), code, datetime.now(KST)) if quoted.get('status') == 'ok' else None
    except ValueError:
        quote = None
    return inverse_tape(name or code, rows, quote, confidence, source, late=inverse_late(view, index))


def entry_block(root, code, owned, source, rows, closed_today, short, name=None, read=None, review=None, confidence=None):
    """Why the data refuses this pick now, or None; the cycle runs it on every pick and slot_entry before a slot takes
    the cycle. User 2026-11-17: a stock whose round trip closed today is bought again only with re-entry allowed, and a
    watchlist-strength re-entry also needs the index moving its way; then the data must confirm the pick (user choice
    2026-11-11: confidence 0.5 + data confirmation). A 낙폭과대 rebound buys a falling tape on purpose: its own checks.
    A careful re-entry (user 2026-11-18) reads NH's quote through `read` for the session, then asks `review(name, code,
    regime, summary)` (True or a block reason), so a slot the review refuses never takes Jev's turn (r3 E1)."""
    reentry, blocked = reentry_check(code, owned, source, closed_today, REENTRY_ALLOW)
    if blocked or source == 'oversold' or source in V2_SOURCES:
        return blocked
    view, index = market_view(root), symbol_index(root, code)
    if short and (refused := inverse_refusal(root, code, rows, name, read, confidence, source)):
        return refused
    if regime := careful_due(code, owned, source, closed_today, view, index):
        try:
            quoted = read.call_tool('get_stock_price', {'stock_code': code}) if read else {}
            quote = fresh_quote(quoted.get('data', {}), code, datetime.now(KST)) if quoted.get('status') == 'ok' else None
        except ValueError:
            quote = None  # a failed read leaves the bars seen
        tape, summary = careful_tape(name or code, rows, quote)  # no chasing, no weakening tape
        if tape:
            return tape
    unconfirmed = entry_confirmation(view, index, short, rows,
                                     own_strength=source == 'new_listing' or (source == 'watchlist_strength' and not reentry))
    if unconfirmed and reentry and source == 'watchlist_strength' and unconfirmed in ('direction_not_confirmed', 'market_unconfirmed'):
        return 'reentry_market_unconfirmed'
    if unconfirmed or not (regime and review):
        return unconfirmed
    verdict = review(name or code, code, regime, summary)  # last: the only check that calls Jev
    return None if verdict is True else verdict


def entry_context(quote, price):
    """Observation only, recorded on each buy: where the price sits in today's range (review 11/13 #3: % from the day's
    high and open), and NH's market warnings on the quote (2026-11-17): % from the price up to the upward and down to the
    downward static-VI trigger, the short-term overheating code (단기과열 1 예고 2 지정 3 연장, 0 none), the
    investment-alarm code (1 주의 2 경고 3 주의>위험예고 4 경고>위험예고 5 위험, 0 none) and a VI halt flag when sent."""
    out = {}
    for key, field, ratio in (('high_gap_pct', 'stck_hgpr', lambda p, lv: p / lv), ('open_gap_pct', 'stck_oprc', lambda p, lv: p / lv),
                              ('vi_up_gap_pct', 'vi_antc_mxpr', lambda p, lv: lv / p), ('vi_down_gap_pct', 'vi_antc_llam', lambda p, lv: lv / p)):
        try:
            level = float(quote.get(field))
            if level > 0 and type(price) in (int, float) and price > 0:
                out[key] = round((ratio(price, level) - 1) * 100, 2)
        except (TypeError, ValueError):
            continue
    for key, field in (('overheat_code', 'short_over_code'), ('alarm_code', 'mrkt_alrm_code')):
        code = str(quote.get(field) if quote.get(field) is not None else '').strip()
        if code in ('0', '1', '2', '3', '4', '5'):
            out[key] = code
    if str(quote.get('vi_type_code') or '') in ('1', '2'):  # only on a UNT quote: 1 KRX VI, 2 NXT VI
        out['vi_halt'] = str(quote['vi_type_code'])
    return out


def entry_timing_context(quote, ask, bars, decided_at):
    """Observation only: compare the quote used for the order to the latest completed candle and recent low."""
    day = decided_at.astimezone(KST).strftime('%Y%m%d')
    rows = [row for row in (bars or []) if isinstance(row, dict)
            and row.get('bsop_date', day) == day and row.get('market', 'KRX') == 'KRX'][:6]

    def price(row, field):
        try:
            value = float(row.get(field))
        except (TypeError, ValueError, OverflowError):
            return None
        return value if math.isfinite(value) and value > 0 else None

    out = {'gate_time': decided_at.isoformat(timespec='milliseconds'), 'order_ask': ask}
    if quote.get('hoga_bsop_hour'):
        out['quote_time_kst'] = quote['hoga_bsop_hour']
    if rows and (reference := price(rows[0], 'stck_prpr')):
        out.update(decision_bar_time=rows[0].get('bsop_time'), decision_price=reference,
                   ask_vs_decision_pct=round((ask / reference - 1) * 100, 3))
    lows = [price(row, 'stck_lwpr') for row in rows]
    if len(lows) == 6 and all(low is not None for low in lows):
        recent = min(lows)
        out.update(recent_low=recent, recent_low_bars=len(lows), ask_vs_recent_low_pct=round((ask / recent - 1) * 100, 3))
    return out


def minute_bars(read, code, cache=None, prior=False):
    """1-minute bar read, retried once after a failed call (read-only; the API fails now and then under load). `cache`:
    one cycle's successful reads by code, so holding reviews and the entry read each chart once (r3 X3). `prior`: NH's
    today_cls_code 0 also returns the previous session's last bars (the opening review, user 2026-11-19)."""
    key = (code, 'prior') if prior else code
    if cache is not None and key in cache:
        return cache[key]
    if cache is not None and not prior and (code, 'prior') in cache:
        return cache[(code, 'prior')]  # the opening read holds today's bars too (valid_bars keeps only today's)
    for attempt in range(2):
        response = read.call_tool('call_api', {'operationId': 'krstockQuotePeriod', 'input': {'market_cd': 'KRX',
            'iem_cd': code, 'gubun': '5', 'xtick': '1', 'today_cls_code': '0' if prior else '1', 'view_main_yn': 'Y',
            'array_cnt': '30'}})
        if response.get('status') == 'ok':
            if cache is not None:
                cache[key] = response
            return response
        if attempt == 0:
            time.sleep(0.5)
    return response


def entry_confirmation(view, index, short, rows, own_strength=False):
    """None when the data backs a buy; otherwise why not: the index must be moving the position's way (or, for a
    watchlist strength entry, the stock's own strength already checked) and the candidate's volume picking up
    (its rising 1-minute trend is checked separately)."""
    if not DIRECTION_CONFIRM:  # switched off on the dashboard: volume only
        return volume_check(rows)
    item = (view or {}).get(index)
    if not item:
        return 'market_unconfirmed'  # no fresh market view: do not buy on the model alone
    if own_strength:
        return volume_check(rows)
    regime, tone, change = item.get('regime'), item.get('today_tone'), item.get('today_change_pct')
    if short:
        aligned = tone == '약세' or (regime in ('하락장', '횡보장') and type(change) in (int, float) and change <= 0)
    else:
        aligned = tone == '강세' or (regime in ('상승장', '횡보장') and type(change) in (int, float) and change >= 0)
    if not aligned:
        return 'direction_not_confirmed'
    return volume_check(rows)


def volume_check(rows):
    """None when the last 5 one-minute volumes are at least ENTRY_VOLUME_RATIO x the 5 before (always None when it is 0).
    Opening rows (user 2026-11-20, strength path at the bell): today's settled KRX minutes, then NXT pre-market or the
    previous session's; today's average minute volume must be ENTRY_VOLUME_RATIO x that of the 5 rows before it, and
    before the first KRX minute settles (about 09:02) there is no volume to compare, so price, trend and Jev decide."""
    if ENTRY_VOLUME_RATIO <= 0:
        return None
    try:
        if any(isinstance(r, dict) and r.get('bsop_date') for r in rows):
            today = max(str(r.get('bsop_date') or '') for r in rows)
            krx = [float(r['vol']) for r in rows if r.get('bsop_date') == today and r.get('market') != 'NXT']
            before = [float(r['vol']) for r in rows if not (r.get('bsop_date') == today and r.get('market') != 'NXT')][:5]
            if krx and before:
                return None if sum(krx) / len(krx) >= ENTRY_VOLUME_RATIO * sum(before) / len(before) else 'volume_not_rising'
            if not krx:
                return None
        volumes = [float(row['vol']) for row in rows[:10]]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 'volume_not_rising'
    if len(volumes) < 10 or sum(volumes[:5]) < ENTRY_VOLUME_RATIO * sum(volumes[5:]):
        return 'volume_not_rising'
    return None


SLOT_COOLDOWN_MIN = 3   # a strength / new-listing pick the order path refused rests this long (user 2026-11-19),
SLOT_COOLDOWN_MAX = 30  # doubling with each refusal after a rest, so a one-off stale signal costs 3 minutes (review 11-19)
_COOLING = {}  # code -> (when it may be picked again, refusals so far today); a buy clears it
_PICK = {}     # this cycle's strength / new-listing pick ('pick': (code, source)), set by cycle()


def cooling(code, now):
    entry = _COOLING.get(code)
    return entry is not None and now < entry[0]


def cool_refused_picks(fn):
    """User 2026-11-19 "매분 막힘은 절대 일어나면 안돼": a strength or new-listing pick the order path then refused for any
    reason (0 shares, guard, spread, a failed read...) would be picked again next minute, and as a slot's pick it holds
    Jev's turn every minute (009150, 11-19 13:44~14:46). A pick that did not end in a buy rests; the
    candidate searches look past it meanwhile, SLOT_COOLDOWN_MIN at first and twice as long after each further refusal."""
    @wraps(fn)
    def wrapped(db, read, broker, account, root, *args, **kwargs):
        _PICK.clear()
        status = None
        try:
            status = fn(db, read, broker, account, root, *args, **kwargs)
            return status
        finally:
            pick = _PICK.pop('pick', None)
            if pick and status in ('buy', 'would_buy'):
                _COOLING.pop(pick[0], None)
            elif pick:
                code, source = pick
                strikes = (_COOLING.get(code) or (None, 0))[1] + 1
                minutes = min(SLOT_COOLDOWN_MAX, SLOT_COOLDOWN_MIN * 2 ** (strikes - 1))
                until = datetime.now(KST) + timedelta(minutes=minutes)
                _COOLING[code] = (until, strikes)
                journal_event(root, 'pick_cooldown', symbol=code, source=source, status=status or 'error', minutes=minutes,
                              until=until.isoformat(timespec='seconds'))
    return wrapped


def strength_shares(root, code, price, owned=(), closed_today=None):
    """Shares one watchlist-strength buy of `code` (none held) comes to, in the order path's own arithmetic: the tranche
    under the single-order limit, position_sizing's factor, then the careful re-entry's cut when `code` closed a round trip
    today (`closed_today`, reentry_floors) in a regime that makes it careful (review 11-19)."""
    import position_sizing
    settings = (_APPLIED_SETTINGS or ({}, None))[0]
    view = market_view(root)
    factor, _ = position_sizing.buy_factor(settings, view, stop_symbol_index(root, code), False, 0, SPLIT_SYMBOL_KRW)
    qty = int(int(min(SHARED_SINGLE_ORDER_KRW, SPLIT_TRANCHE_KRW * size_factor('watchlist_strength'))) // price * factor)
    if careful_due(code, owned, 'watchlist_strength', closed_today, view, symbol_index(root, code)):
        qty = int(qty * CAREFUL['size'] / 100)
    return qty


_STRENGTH_WHYS = {}  # the last strength_candidate call: every ranked stock -> why it was not picked ('picked' for the one)


def strength_whys(refused_code=None, refused=None):
    """{code: reason} of the last strength look, in Jev's ranking order, for the cycle's records (user 2026-11-20 '한진칼 왜
    안 샀는지도 기록'): before, only the top-ranked stock's reason was written."""
    out = dict(_STRENGTH_WHYS)
    if refused_code and refused:
        out[refused_code] = refused  # picked, then refused by entry_block (careful re-entry, reentry rules)
    return out


def strength_candidate(root, movers, bars, now, owned=(), floors=None, sized=None):
    """(code, facts) of the best watchlist stock among Jev's top STRENGTH_TOP_N picks today (probability >=
    STRENGTH_JEV_MIN) that is up STRENGTH_CHANGE% on its previous close right now and rising on its last minute bars;
    stocks already held are skipped so another strong one can enter, and a stock in `floors` (reentry_floors) only
    above its last exit price. With `sized(code, price)` (strength_shares), a stock one buy cannot afford a share of is
    skipped (11-19 009150 at 847,000원 came out 0 shares every minute and held Jev's turn). (None, why) otherwise."""
    try:
        data = json.loads((Path(root) / 'watchlist.json').read_text())
        at = datetime.fromisoformat(data['time'])
        probabilities = data['probabilities'] if at.astimezone(KST).date() == now.astimezone(KST).date() else {}
    except (OSError, ValueError, KeyError, TypeError):
        probabilities = {}
    ranked = sorted((c for c, v in probabilities.items() if c != 'NONE' and type(v) in (int, float)),
                    key=lambda c: probabilities[c], reverse=True)[:STRENGTH_TOP_N]
    best, why, whys = None, 'no_strength_candidate', {}  # whys: each ranked stock's refusal (the top one is reported)
    _STRENGTH_WHYS.clear()
    _STRENGTH_WHYS.update({code: 'not_in_movers' for code in ranked if code not in movers})
    for code, details in movers.items():
        probability = probabilities.get(code)
        price, change = details.get('price'), details.get('change_pct')
        rows = bars.get(code) or []
        if code not in ranked:
            continue
        if probability < STRENGTH_JEV_MIN or code in owned:
            _STRENGTH_WHYS[code] = 'held' if code in owned else 'jev_below_min'
            continue
        if cooling(code, now):
            whys[code] = 'pick_cooldown'
            continue
        try:
            previous = price / (1 + change / 100)
            now_change = (float(rows[0]['stck_prpr']) / previous - 1) * 100
            rising = float(rows[0]['stck_prpr']) > float(rows[5]['stck_prpr'])
        except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError):
            _STRENGTH_WHYS[code] = 'bars_missing'
            continue
        if floors and code in floors and float(rows[0]['stck_prpr']) <= floors[code]:
            whys[code] = 'reentry_below_last_exit'
            continue
        if not STRENGTH_CHANGE[0] <= now_change <= STRENGTH_CHANGE[1]:
            whys[code] = 'strength_change_out_of_range'
            continue
        if RISING_CHECK and not rising:
            whys[code] = 'trend_not_rising'
            continue
        if sized is not None and sized(code, float(rows[0]['stck_prpr'])) < 1:
            whys[code] = 'one_share_over_buy'
            continue
        if best is None or probability > best[1]['jev_watchlist_confidence']:
            best = (code, {'jev_watchlist_confidence': probability, 'change_pct': round(now_change, 2)})
    # 11-20: the last stock looked at used to name the reason (삼성전자 out of range hid 한진칼 resting after a refusal)
    why = next((whys[c] for c in ranked if c in whys), why)
    _STRENGTH_WHYS.update(whys)
    if best:
        _STRENGTH_WHYS[best[0]] = 'picked'
    return best if best else (None, why)


def strength_shadow(root, movers, bars, owned, symbols, floors, blocked, cycle_id):
    """Observation only (user 2026-11-17): when Jev's own pick is blocked by the data gates, what the watchlist strength
    path would have picked this minute. Never orders; reviewed after about 10 sessions before any rule change."""
    try:
        from etf_universe import SHORT
        code, facts = (strength_candidate(root, movers, bars, datetime.now(KST), owned, floors,
                                          sized=lambda c, pr: strength_shares(root, c, pr, owned, floors)) if movers else (None, 'no_movers'))
        short_codes = {symbols[name] for name in SHORT if name in symbols}
        fields = ({'symbol': code, 'confidence': facts['jev_watchlist_confidence'], 'change_pct': facts['change_pct']}
                  if code else {'strength_reason': facts})
        fields['strength_reasons'] = strength_whys()
        journal_event(root, 'strength_shadow', blocked=blocked, opposite_held=any(c in short_codes for c in owned),
                      cycle_id=cycle_id, **fields)
    except Exception as exc:  # an observation must never stop the runner or its stop-loss watch
        journal_event(root, 'strength_shadow', blocked=blocked, error_type=type(exc).__name__, cycle_id=cycle_id)


def entries_today(root, now, source):
    """Buys submitted today from one entry source (trading-ledger events_v2); no ledger yet = 0, an unreadable or stalled
    one (journal_event left ledger-ingest-error.json) = None, so callers treat the daily cap as full.
    Counted by line digest: a rewritten or restored events.jsonl is imported again under a new file identity."""
    if not (Path(root) / 'trading-ledger.sqlite3').exists():
        return 0
    if (Path(root) / 'ledger-ingest-error.json').exists():
        return None
    try:
        with sqlite3.connect(f"file:{Path(root) / 'trading-ledger.sqlite3'}?mode=ro", uri=True, timeout=2) as db:
            return db.execute("SELECT count(DISTINCT digest) FROM events_v2 WHERE event='submitted' AND time LIKE ? AND data LIKE ?",
                              (now.astimezone(KST).strftime('%Y-%m-%d') + '%', f'%"source": "{source}"%')).fetchone()[0]
    except sqlite3.Error:
        return None


def ipo_entries_today(root, now):
    """New-listing buys submitted today; an unreadable ledger counts as full (no buy)."""
    count = entries_today(root, now, 'new_listing')
    return IPO_DAILY_MAX if count is None else count


def slot_entry(read, root, owned, movers, bars, closed_today, short_codes, now, review=None):
    """Strategy slots (user 2026-11-17 "병렬 트레이딩"): entry paths that are checked every cycle on their own, beside Jev's
    pick, each with its own switch, concurrent positions and daily buys, so one held position (or Jev picking an ETF
    every minute) does not keep the others from trading. First ready slot wins this cycle; Jev's pick goes next cycle.
      new listing  (ipo_slot_on)      : ipo_candidate; not an index bet, so the opposite-direction guard skips it
      strength     (strength_slot_on) : strength_candidate; a long stock, so it waits while an inverse is held
                                        (opposite_guard on) instead of taking Jev's turn
    A slot is ready only when entry_block passes it (with the careful re-entry `review`), so a pick the cycle would
    refuse (a same-day re-entry without the index, 11/17 review; a careful re-entry Jev declines, r3 E1) never takes
    Jev's turn. `closed_today` is reentry_floors.
    Returns (code, source, rows or None, facts) or None. Orders stay one at a time (the guard's rule)."""
    if IPO_SLOT and IPO_DAYS and ipo_entries_today(root, now) < IPO_DAILY_MAX:
        code, facts, rows = ipo_candidate(read, root, owned, now)
        if code is not None and not entry_block(root, code, owned, 'new_listing', rows, closed_today, code in short_codes):
            return code, 'new_listing', rows, facts
    if STRENGTH_SLOT and movers and STRENGTH_TOP_N:
        plans = exit_plans(root)
        held = sum(1 for code in owned if code not in ETF7_SYMBOLS.values()
                   and (plans.get(code) or {}).get('profile') not in ('ipo', 'oversold') and not is_v2(plans.get(code) or {}))
        today = entries_today(root, now, 'watchlist_strength')
        if held < STRENGTH_SLOT_MAX and today is not None and today < STRENGTH_SLOT_DAILY:
            code, facts = strength_candidate(root, movers, bars, now, owned, closed_today if REENTRY_GUARD else None,
                                             sized=lambda c, pr: strength_shares(root, c, pr, owned, closed_today))
            if code is not None and not entry_block(root, code, owned, 'watchlist_strength', bars.get(code) or [],
                                                    closed_today, code in short_codes, read=read, review=review):
                return code, 'watchlist_strength', None, facts
    return None


def ipo_time_stop(plan, now):
    """'ipo_time_stop' once a new-listing position has been open IPO_TIME_STOP_MIN minutes (user 2026-11-17: a listing
    day swings hard, e.g. 옵토닉스 11/17 -41% open to close), else None."""
    if not IPO_TIME_STOP_MIN or (plan or {}).get('profile') != 'ipo':
        return None
    try:
        opened = datetime.fromisoformat(plan['opened'])
    except (KeyError, TypeError, ValueError):
        return None
    return 'ipo_time_stop' if (now - opened).total_seconds() >= IPO_TIME_STOP_MIN * 60 else None


_IPO_SEEN = {}  # (day, code) -> the verdict last journaled as ipo_seen


def ipo_candidate(read, root, owned, now):
    """(code, facts, minute rows) of a recent new listing trading strongly right now, else (None, why, None).

    Strong: above its base (the offer price on listing day, the previous close later), above today's open, within
    IPO_NEAR_HIGH_PCT of today's high, rising on its last minute bars and on rising volume. NH's quote must confirm the
    listing date KIND reported. One new-listing position at a time; none before IPO_START."""
    import new_listings
    if not IPO_DAYS:
        return None, 'ipo_off', None
    if not ipo_open(now):
        return None, 'ipo_too_early', None
    plans = exit_plans(root)
    if sum((plans.get(code) or {}).get('profile') == 'ipo' for code in owned) >= IPO_MAX_POSITIONS:
        return None, 'ipo_already_held', None
    today, why = now.astimezone(KST).date(), 'no_new_listing'
    for item in new_listings.recent(root, now)[:IPO_MAX_SCAN]:
        code = item['code']
        listed = datetime.strptime(item['listed'], '%Y%m%d').date()
        if code in owned or sessions_held(listed, today) >= IPO_DAYS:
            continue
        if cooling(code, now):
            why = 'pick_cooldown'
            continue
        quote = ((read.call_tool('get_stock_price', {'stock_code': code}).get('data') or {}).get('Output_0') or {})
        price, base, opened, high = (quote.get(k) for k in ('stck_prpr', 'stck_prdy_clpr', 'stck_oprc', 'stck_hgpr'))
        if quote.get('iem_cd') != code or not all(type(v) in (int, float) and v > 0 for v in (price, base, opened, high)):
            why = 'ipo_quote_unavailable'  # a failed or throttled read, not a verdict on the listing
            continue
        if quote.get('stck_lstn_date') != item['listed']:
            why = 'listing_unconfirmed'
            continue
        # the quote alone settles these, so no minute-bar read is spent on them (one NH call per listing per cycle)
        failed = ('below_base' if price < base else 'below_open' if price < opened else
                  'off_high' if price < high * (1 - IPO_NEAR_HIGH_PCT / 100) else None)
        if not failed:
            try:
                rows = valid_bars(minute_bars(read, code).get('data', {}), code, now)
            except ValueError:
                rows, failed = None, 'ipo_bars_unavailable'
        if not failed:
            failed = ('trend_not_rising' if RISING_CHECK and float(rows[0]['stck_prpr']) <= float(rows[5]['stck_prpr'])
                      else volume_check(rows))
        # each listing's own verdict and base, journaled when it changes (user 2026-11-19, ipo_shadow): `why` alone kept
        # only the last listing's (11-18 에이엘로봇 was lost), and every path that scans listings records it
        if _IPO_SEEN.get((today, code)) != (failed or 'pass'):
            _IPO_SEEN[(today, code)] = failed or 'pass'
            journal_event(root, 'ipo_seen', symbol=code, why=failed or 'pass', price=price, base=base)
        if failed:
            why = failed
            continue
        listed_shares = quote.get('lstn_stcn_unit0')
        volume = quote.get('acml_vol')
        return code, {'name': item['name'], 'listed': item['listed'], 'sessions': sessions_held(listed, today),
                      'change_pct': round((price / base - 1) * 100, 2),
                      'turnover_pct': round(volume / listed_shares * 100, 1) if type(volume) in (int, float) and type(listed_shares) in (int, float) and listed_shares > 0 else None}, rows
    return None, why, None


def size_factor(source):
    """One buy as a share of the tranche: new listings smaller, 낙폭과대 rebounds larger (user 2026-11-13)."""
    if source == 'new_listing':
        return IPO_SIZE_PCT / 100
    if source == 'oversold':
        return OVERSOLD.get('oversold_size_pct', 100) / 100
    if source in V2_SOURCES:
        return OVERSOLD.get('oversold_v2_size_pct', 100) / 100
    return 1


def oversold_may_carry(plan, today):
    """A 낙폭과대 rebound may stay past the flatten for its configured sessions (0: sells by the flatten like any trade).
    The intraday rebound never crosses a weekend or holiday; 선별 v2 counts KRX sessions through them (its replay held
    five sessions, weekends included)."""
    if (plan or {}).get('profile') != 'oversold' or not plan.get('hold_days'):
        return False
    try:
        opened = datetime.fromisoformat(plan['opened']).astimezone(KST).date()
    except (KeyError, TypeError, ValueError):
        return False
    within = sessions_held(opened, today) < int(plan['hold_days'])
    return within if is_v2(plan) else within and next_session_open(today)


def is_v2(plan):
    """A 선별 v2 position (its plan basis says so)."""
    return ((plan or {}).get('target_basis') or {}).get('variant') == "v2"


def oversold_entry(read, root, owned, bars, news, investor, modes, cycle_id):
    """(code, minute rows, plan basis) of a watched stock/ETF that fell far beyond its usual range, stopped falling and
    is confirmed by Jev (BUY at oversold_jev_min);
    None otherwise. Every triggered code is journaled (oversold_scan) so the dashboard and reviews see it."""
    import oversold
    from nh_etf_auto_v2 import jev_oversold7
    from strategy_settings import DEFAULTS
    names = oversold.universe(root)
    required = max(6, int(OVERSOLD.get('oversold_calm_min', DEFAULTS['oversold_calm_min'])))
    def rows_of(code):
        response = minute_bars(read, code)
        now = datetime.now(KST)
        rows = valid_bars(response.get('data', {}), code, now, required=required) if response.get('status') == 'ok' else None
        if not rows or not fresh_series(rows, now):
            raise ValueError('oversold bars unavailable')
        return rows
    rows_cache = {}
    def bars_of(code):
        rows_cache[code] = rows_of(code)
        return rows_cache[code]
    found = oversold.scan(read, datetime.now(KST), OVERSOLD, list(names), held=set(owned), bars_of=bars_of)
    today = datetime.now(KST).date()
    for row in found:
        atr_status = row.get('atr_status')
        atr_key = (today, row['code'], 'atr_unknown')
        if atr_status == 'unknown' and atr_key not in _OVERSOLD_LOGGED:
            _OVERSOLD_LOGGED.add(atr_key)
            journal_event(root, 'oversold_atr_status', symbol=row['code'], status='unknown', need_pct=row['need_pct'],
                          drop_pct=row['drop_pct'], retry_seconds=oversold.ATR_RETRY_SECONDS, cycle_id=cycle_id)
        elif atr_status == 'confirmed' and atr_key in _OVERSOLD_LOGGED:
            _OVERSOLD_LOGGED.remove(atr_key)
            journal_event(root, 'oversold_atr_status', symbol=row['code'], status='confirmed', need_pct=row['need_pct'],
                          drop_pct=row['drop_pct'], cycle_id=cycle_id)
        if not row['triggered']:
            continue
        ready = row['stabilized'] and row['code'] in rows_cache and row['target_pct'] > 0
        facts = oversold.brief(row, names.get(row['code']), with_analyst=ready)  # the Naver read only when it is used
        key = (today, row['code'], row['stabilized'])
        if key not in _OVERSOLD_LOGGED:  # first trigger and each change of state, not every minute
            _OVERSOLD_LOGGED.add(key)
            journal_event(root, 'oversold_scan', symbol=row['code'], drop_pct=row['drop_pct'], atr_multiple=row['atr_multiple'],
                          atr_status=atr_status, need_pct=row.get('need_pct'),
                          stabilized=row['stabilized'], bounce_pct=row['bounce_pct'], target_pct=row['target_pct'],
                          stop_pct=row['stop_pct'], analyst=facts.get('analyst'), cycle_id=cycle_id)
        if not ready:
            continue
        if not oversold.market_ok(row, OVERSOLD):  # a lone fall: not bought, the engine is not asked
            if (today, row['code'], 'market') not in _OVERSOLD_LOGGED:
                _OVERSOLD_LOGGED.add((today, row['code'], 'market'))
                journal_event(root, 'oversold_signal', symbol=row['code'], permitted=False, reason='market_not_falling',
                              market_low_pct=row.get('market_low_pct'), cycle_id=cycle_id)
            continue
        asked = _OVERSOLD_ASKED.get((today, row['code']))
        if asked and datetime.now(KST) - asked[0] < OVERSOLD_REASK and row['drop_pct'] >= asked[1] - 1:
            if asked[2]:  # BUY a few minutes ago and a later gate refused it: tried again without asking
                return row['code'], rows_cache[row['code']], asked[3]
            continue  # the engine said no a few minutes ago and the stock has not fallen another 1%
        if modes.get('jev'):
            try:
                answer = jev_oversold7({**bars, row['code']: rows_cache[row['code']]}, news, row['code'], facts,
                                       investor=investor, extra_state={'market_regime_themes_global': market_view(root),
                                                                      'premarket_context': premarket_view()})
                verdict = answer['oversold']
                ok = verdict['choice'] == 'BUY' and verdict['confidence'] >= OVERSOLD['oversold_jev_min']
                journal_event(root, 'oversold_signal', symbol=row['code'], engine='jev', choice=verdict['choice'],
                              confidence=verdict['confidence'], permitted=ok, input_digest=answer.get('input_digest'),
                              evidence=answer.get('evidence') or None,
                              premarket_audit=premarket_input_audit(answer.get('input_digest'), 'jev'), cycle_id=cycle_id)
            except Exception as exc:  # no confirmation, no buy
                journal_event(root, 'jev_error', error_type=type(exc).__name__, context='oversold', cycle_id=cycle_id,
                              premarket_audit={'status': 'input_record_unavailable'})
                ok = False
        else:
            try:
                journal_event(root, 'oversold_research_failure', symbol=row['code'], engine='jev',
                              reason='engine_unavailable', premarket_audit={'status': 'input_record_unavailable'}, cycle_id=cycle_id)
            except Exception:
                pass  # An extra audit record must not change the existing fallback.
            ok = False
        basis = {**facts, 'prev_close': row['prev_close'], 'low': row['low'], 'target_price': row['target_price'],
                 'stop_price': row['stop_price']}
        _OVERSOLD_ASKED[(today, row['code'])] = (datetime.now(KST), row['drop_pct'], ok, basis)
        if ok:
            return row['code'], rows_cache[row['code']], basis
    return None


_V2_LOGGED, _V2_BOUGHT, _V2_VERDICT = set(), {}, {}  # journal once a day; codes sent per day; research per (day, code)
V2_SOURCES = ('oversold_v2', 'oversold_v2_add', 'oversold_v2_reentry')  # first half, second half, re-buy after a stop
V2_ROUTE = ('선별 v2 낙주매매: 시장 전체가 폭락한 날 장 막판에 절반을 사고, 다음 거래일 더 싸면 나머지 절반을 산다. '
            '+8% 절반, +15% 나머지 익절, 최대 3거래일, 평균가 −2% 손절 뒤 저점이 확인되면 다시 산다. 기본은 산다. '
            'WAIT는 종목 고유 악재, 애널리스트 하향, 위기가 번지는 중(신용 경색·전쟁 확대 등)처럼 분명한 이유가 있을 때만 고른다.')


def premarket_view():
    try:
        from premarket import engine_brief
        return engine_brief()
    except (ImportError, OSError, ValueError, TypeError):
        return None


def premarket_input_audit(digest, engine):
    """Describe recorded model input only; never infer how the model used the context."""
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return {'status': 'input_record_unavailable', 'reason': 'digest_missing_or_invalid'}
    try:
        import research_store
        from sqlite_ro import ro_connect
        with ro_connect(research_store.ROOT / 'market-research.sqlite3', timeout=0.05) as db:
            row = db.execute('SELECT inputs FROM model_inputs WHERE digest=? AND engine=? AND question=?',
                             (digest, engine, 'oversold' if engine == 'jev' else 'scores')).fetchone()
        if not row:
            return {'status': 'input_record_unavailable', 'reason': 'input_row_missing'}
        inputs = json.loads(row[0])
        context = inputs['state' if engine == 'jev' else 'context']
        brief = context.get('premarket_context')
        if not isinstance(brief, dict):
            return {'status': 'context_unavailable', 'reason': 'missing_or_invalid_at_analysis'}
        sources = brief.get('sources') if isinstance(brief.get('sources'), dict) else {}
        safe_text = lambda value: value[:80] if isinstance(value, str) else None
        return {'status': 'included_in_recorded_input', 'as_of': safe_text(brief.get('as_of')),
                'sources': {name: {key: safe_text(data.get(key)) for key in ('status', 'observed_at')}
                            for name, data in sources.items()
                            if name in ('market', 'news', 'dart') and isinstance(data, dict)}}
    except Exception as exc:
        return {'status': 'input_record_unavailable', 'reason': 'input_lookup_error', 'error_type': type(exc).__name__}


def trading_journal_brief():
    """The journal of the last sessions for the engines' context (trading_journal.brief); {} on any failure."""
    try:
        import trading_journal
        return trading_journal.brief()
    except Exception:
        return {}


def v2_window(now=None):
    """True inside the 선별 v2 buy window (oversold_v2_from_hhmm ~ oversold_v2_until_hhmm; it may run past the entry
    cutoff: the replay's best entry was the last hour of the session)."""
    from session_times import hhmm, shifted
    now = now or datetime.now(KST)
    clock, day = now.strftime('%H:%M'), now.date()
    return (bool(OVERSOLD.get('oversold_v2_on'))
            and shifted(hhmm(OVERSOLD.get('oversold_v2_from_hhmm', 1430)), day) <= clock
            < shifted(hhmm(OVERSOLD.get('oversold_v2_until_hhmm', 1455)), day))


def ipo_open(now):
    """New listings are looked at from IPO_START on the day's exchange clock (09:20, 10:20 on 수능 day)."""
    from session_times import shifted
    return now.strftime('%H:%M') >= shifted(IPO_START, now.date())


def v2_research(root, row, rows, bars, news, investor, modes, attempt=None):
    """(verdict, size multiple, facts) — the main engine reads everything once a day per code: the chart, news, investor
    flows, market and global themes, the analyst consensus and reports, and the similar past falls. 'veto' (Jev WAIT at
    oversold_v2_veto_conf) skips the code; 'boost' (Jev BUY 0.8+) sizes it by oversold_v2_boost_pct; no answer leaves the rule to decide at normal size (not cached)."""
    import oversold
    key = (datetime.now(KST).date(), row['code'], *((attempt,) if attempt else ()))  # a re-buy is judged again
    if key in _V2_VERDICT:
        return _V2_VERDICT[key]
    facts = {k: row.get(k) for k in ('drop_pct', 'market', 'market_low_pct', 'rel_drop', 'stop_pct', 'half_tp_pct',
                                     'target_pct', 'hold_days')}
    facts['past_similar_falls'] = oversold_cases.brief(row['code'], row['drop_pct'], mkt_low_pct=row['market_low_pct'])
    verdict, detail = 'normal', {}
    try:
        facts['analyst'] = oversold.analyst(row['code'])
        if modes.get('jev'):
            from nh_etf_auto_v2 import jev_oversold7
            answer = jev_oversold7({**bars, row['code']: rows}, news, row['code'], {**facts, 'route': V2_ROUTE},
                                   investor=investor, extra_state={'market_regime_themes_global': market_view(root),
                                                                      'premarket_context': premarket_view()})
            said = answer['oversold']
            detail = {'engine': 'jev', 'choice': said['choice'], 'confidence': said['confidence'], 'input_digest': answer.get('input_digest')}
            verdict = ('veto' if said['choice'] == 'WAIT' and said['confidence'] >= OVERSOLD['oversold_v2_veto_conf']
                       else 'boost' if said['choice'] == 'BUY' and said['confidence'] >= 0.8 else 'normal')
        else:
            return verdict, 1.0, {**facts, 'engine': None}
    except Exception as exc:  # research failed this minute: asked again next minute; the rule alone does not wait for it
        return verdict, 1.0, {**facts, 'research_error': type(exc).__name__}
    boost = OVERSOLD.get('oversold_v2_boost_pct', 100) / 100 if verdict == 'boost' else 1.0
    for old in [k for k in _V2_VERDICT if k[0] != key[0]]:
        _V2_VERDICT.pop(old)
    _V2_VERDICT[key] = (verdict, boost, {**facts, **detail})
    return _V2_VERDICT[key]


def v2_possible(read, root, owned):
    """Worth the full entry cycle for 선별 v2: a second half is due, or a market passes the gate (cached per minute)."""
    import oversold
    today = datetime.now(KST).date()
    for code, plan in exit_plans(root).items():
        if OVERSOLD.get('oversold_v2_add_on') and is_v2(plan) and code in owned and not (plan.get('target_basis') or {}).get('added'):
            try:
                if sessions_held(datetime.fromisoformat(plan['opened']).astimezone(KST).date(), today) == 1:
                    return True
            except (KeyError, TypeError, ValueError):
                continue
    try:
        gate = oversold.v2_gate(read, datetime.now(KST), OVERSOLD)
    except Exception as exc:
        if (today, 'gate_error') not in _V2_LOGGED:  # once a day (improvement report 11/17-1)
            _V2_LOGGED.add((today, 'gate_error'))
            journal_event(root, 'oversold_v2_gate_error', error_type=type(exc).__name__)
        return False
    state = (today, 'gate', gate['kospi']['pass'], gate['kosdaq']['pass'])
    if state not in _V2_LOGGED:  # every day, passing or not, so the replay can be checked against the live verdict
        _V2_LOGGED.add(state)
        journal_event(root, 'oversold_v2_gate', kospi=gate['kospi'], kosdaq=gate['kosdaq'], nasdaq_prev=gate['nasdaq_prev'],
                      passed=gate['kospi']['pass'] or gate['kosdaq']['pass'])
    return gate['kospi']['pass'] or gate['kosdaq']['pass']


def fresh_rows(read, code, now):
    try:
        response = minute_bars(read, code)
        rows = valid_bars(response.get('data', {}), code, now) if response.get('status') == 'ok' else None
    except ValueError:
        return None
    return rows if rows and fresh_series(rows, now) else None


def buy_budget(source, size_mult=1, inverse_mult=1):
    return int(min(SHARED_SINGLE_ORDER_KRW, SPLIT_TRANCHE_KRW * size_factor(source) * size_mult * inverse_mult))


def note_single_order_limit(root, today, code, source, price, budget, cycle_id, sizing=None):
    key = (today, 'one_share', source, code)
    if key not in _V2_LOGGED:
        _V2_LOGGED.add(key)
        journal_event(root, 'v2_execution_gate', permitted=False, reason='single_order_limit', symbol=code,
                      source=source, price=price, buy_budget_krw=budget, sizing=sizing, cycle_id=cycle_id)


def v2_unaffordable(root, today, code, source, ask, budget, cycle_id):
    if type(ask) is not int or ask <= 0:
        return (today, 'one_share', source, code) in _V2_LOGGED
    if ask <= budget:
        return False
    note_single_order_limit(root, today, code, source, ask, budget, cycle_id)
    return True


def restore_single_order_limits(path, today):
    """Keep the daily log deduplication across a runner restart."""
    prefix = f'"{today.isoformat()}'
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                if prefix not in line[:40] or '"single_order_limit"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get('event') == 'v2_execution_gate' and event.get('reason') == 'single_order_limit':
                    _V2_LOGGED.add((today, 'one_share', event.get('source'), event.get('symbol')))
    except OSError:
        pass


def oversold_v2_entry(read, root, owned, cycle_id, bars=None, news=(), investor=None, modes=None, db=None):
    """(code, minute rows, plan basis, source) for 선별 v2 inside its window, or None:
    - 'oversold_v2_add': the second half of a v2 position bought the previous session, when it now trades under the
      first price but above the stop (oversold_v2_add_on);
    - 'oversold_v2': on a market crash day (oversold.v2_gate) a watched code that fell with its market and that the
      research did not veto; half the size when the second half is on.
    The gate, each candidate and its research verdict are journaled once a day."""
    import oversold
    now = datetime.now(KST)
    if not v2_window(now):
        return None
    today = now.date()
    plans = exit_plans(root)
    if today not in _V2_BOUGHT:  # first look today (or after a restart): what this runner already sent or vetoed today
        events_path = Path(root) / 'events.jsonl'
        for source, code in oversold.sent_today(events_path, today.isoformat()):
            v2_sent(source, code)
        _V2_LOGGED.update((today, 'veto', code) for code in v2_vetoed_today(events_path, today.isoformat()))
        restore_single_order_limits(events_path, today)
        _V2_BOUGHT.setdefault(today, set())  # read once a day
    if OVERSOLD.get('oversold_v2_add_on'):
        for code, plan in plans.items():
            basis = plan.get('target_basis') or {}
            if not is_v2(plan) or code not in owned or basis.get('added') or (today, 'add', code) in _V2_LOGGED:
                continue
            try:
                if db is not None and position_since_open(db, code, owned[code])['sells']:
                    continue  # part was already sold: the plan is past its entry
            except ValueError:
                continue
            try:
                if sessions_held(datetime.fromisoformat(plan['opened']).astimezone(KST).date(), today) != 1:
                    continue
                q = (read.call_tool('get_stock_price', {'stock_code': code}).get('data') or {}).get('Output_0') or {}
                price = float(q['stck_prpr'])
            except Exception:
                continue
            if not float(basis.get('stop_price') or 0) < price < float(basis.get('entry_price') or 0):
                continue
            rows = fresh_rows(read, code, now)
            if rows:
                return code, rows, {**basis, 'add': True}, 'oversold_v2_add'
    bought = _V2_BOUGHT.setdefault(today, set())
    bought |= {code for code, plan in plans.items() if is_v2(plan) and str(plan.get('opened', '')).startswith(today.isoformat())}
    if len(bought) >= int(OVERSOLD.get('oversold_v2_max_new', 3)):
        return None
    try:
        gate = oversold.v2_gate(read, now, OVERSOLD)
    except Exception:  # a failed read is retried next minute; it must not end the cycle
        return None
    state = (today, 'gate', gate['kospi']['pass'], gate['kosdaq']['pass'])
    if state not in _V2_LOGGED:
        _V2_LOGGED.add(state)
        journal_event(root, 'oversold_v2_gate', kospi=gate['kospi'], kosdaq=gate['kosdaq'], nasdaq_prev=gate['nasdaq_prev'],
                      passed=gate['kospi']['pass'] or gate['kosdaq']['pass'], cycle_id=cycle_id)
    if not (gate['kospi']['pass'] or gate['kosdaq']['pass']):
        return None
    vetoed = {code for day, kind, code in (k for k in _V2_LOGGED if len(k) == 3) if day == today and kind == 'veto'}
    half = 0.5 if OVERSOLD.get('oversold_v2_add_on') else 1.0
    source = 'oversold_v2'
    for row in oversold.v2_candidates(read, gate, OVERSOLD, list(oversold.universe(root)), held=set(owned) | bought | vetoed):
        code = row['code']
        cached = _V2_VERDICT.get((today, code))
        possible_boost = cached[1] if cached else max(1.0, OVERSOLD.get('oversold_v2_boost_pct', 100) / 100)
        if v2_unaffordable(root, today, code, source, row.get('ask'), buy_budget(source, half * possible_boost), cycle_id):
            continue
        rows = fresh_rows(read, code, now)
        if not rows:
            continue
        verdict, boost, facts = v2_research(root, row, rows, bars or {}, news, investor, modes or {})
        if (today, 'code', row['code']) not in _V2_LOGGED:
            error = facts.get('research_error')
            if not error:
                _V2_LOGGED.add((today, 'code', row['code']))
            engine = facts.get('engine')
            fallback = 'research_error' if error else 'engine_unavailable' if engine is None else None
            event_fields = dict(symbol=row['code'], drop_pct=row['drop_pct'], market=row['market'],
                          market_low_pct=row['market_low_pct'], rel_drop=row['rel_drop'], stop_pct=row['stop_pct'],
                          hold_days=row['hold_days'], permitted=verdict != 'veto', research=verdict,
                          engine=engine, choice=facts.get('choice'), confidence=facts.get('confidence'),
                          score=facts.get('score'), cycle_id=cycle_id, input_digest=facts.get('input_digest'), advice_id=facts.get('advice_id'),
                          fallback_reason=fallback, research_error=error,
                          premarket_audit=premarket_input_audit(facts.get('input_digest'), engine)
                          if engine == 'jev' else {'status': 'input_record_unavailable'})
            if error:
                try:
                    journal_event(root, 'oversold_v2_research_failure', **event_fields)
                except Exception:
                    pass  # An extra audit record must not change the existing fallback.
            else:
                journal_event(root, 'oversold_v2_signal', **event_fields)
        if verdict == 'veto':
            _V2_LOGGED.add((today, 'veto', row['code']))
            continue
        if v2_unaffordable(root, today, code, source, row.get('ask'), buy_budget(source, half * boost), cycle_id):
            continue
        return row['code'], rows, {**row, 'variant': 'v2', 'entry_price': row['price'], 'size_mult': half * boost,
                                   'research': verdict, 'past_similar_falls': facts.get('past_similar_falls')}, 'oversold_v2'
    return None


def v2_vetoed_today(events_path, day):
    """Codes the 선별 v2 research vetoed `day` (once a day per code; a re-buy veto waits for the next low instead)."""
    try:
        lines = Path(events_path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return set()
    out = set()
    for line in lines:
        if f'"{day}' not in line[:40] or '"oversold_v2_signal"' not in line or '"veto"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get('event') == 'oversold_v2_signal' and e.get('research') == 'veto' and 'reentry' not in e:
            out.add(e.get('symbol'))
    return out


def v2_sent(source, symbol):
    today = datetime.now(KST).date()
    if source == 'oversold_v2_reentry':
        return  # its watch entry is cleared when sent (v2_reentry.clear)
    if source == 'oversold_v2_add':
        _V2_LOGGED.add((today, 'add', symbol))
    else:
        _V2_BOUGHT.setdefault(today, set()).add(symbol)


def v2_reentry_due(root, owned, now=None):
    """A stopped v2 code waits for its re-buy now (v2_reentry; the cycle then runs past the entry cutoff for it)."""
    import v2_reentry
    now = now or datetime.now(KST)
    return any(code not in owned and v2_reentry.open_for(w, now, OVERSOLD, sessions_held)
               for code, w in v2_reentry.prune(root, now, sessions_held).items())


def v2_reentry_entry(read, root, owned, cycle_id, bars=None, news=(), investor=None, modes=None):
    """(code, rows, basis, 'oversold_v2_reentry') once a stopped v2 code has held a low and the engine, asked again,
    does not veto it (user 2026-11-17 "−2%에 손절하고 저점에 다시 매수"); None otherwise."""
    import v2_reentry
    now = datetime.now(KST)
    for code, w in sorted(v2_reentry.prune(root, now, sessions_held).items()):  # a watch whose sessions are over is dropped
        if code in owned or not v2_reentry.open_for(w, now, OVERSOLD, sessions_held):
            continue  # the stop is still selling, or it was bought back; or outside its buying hours
        rows = fresh_rows(read, code, now)
        if not rows or not v2_reentry.bottom(root, code, rows, now, OVERSOLD):
            continue
        w = v2_reentry.watch(root).get(code) or w
        left = int(w['hold_days']) - sessions_held(datetime.fromisoformat(w['opened']).astimezone(KST).date(), now.date())
        row = {**(w.get('facts') or {}), 'code': code, 'stop_pct': float(OVERSOLD['oversold_v2_stop_pct']), 'hold_days': left,
               'half_tp_pct': float(OVERSOLD['oversold_v2_half_tp_pct']), 'target_pct': float(OVERSOLD['oversold_v2_tp_pct'])}
        row['drop_pct'] = row.get('drop_pct') or 0.0  # facts for the model's text; the past-falls lookup needs a number
        verdict, _, facts = v2_research(root, row, rows, bars or {}, news, investor, modes or {}, attempt=('reentry', w['reentries'], w.get('low_at')))
        logged = (now.date(), 'reentry', code, w['reentries'], w.get('low_at'))
        if logged not in _V2_LOGGED and not facts.get('research_error'):
            _V2_LOGGED.add(logged)
            journal_event(root, 'oversold_v2_signal', symbol=code, reentry=w['reentries'] + 1, low=w.get('low'), low_at=w.get('low_at'),
                          permitted=verdict != 'veto', research=verdict, engine=facts.get('engine'), choice=facts.get('choice'),
                          confidence=facts.get('confidence'), score=facts.get('score'), stop_pct=row['stop_pct'], hold_days=left,
                          cycle_id=cycle_id, input_digest=facts.get('input_digest'))
        if verdict == 'veto':
            continue  # asked again at the next new low
        price = int(rows[0]['stck_prpr'])
        return code, rows, {**row, 'variant': 'v2', 'entry_price': price, 'stop_price': round(price * (1 - row['stop_pct'] / 100)),
                            'size_mult': 1, 'added': True, 'research': verdict, 'reentries': w['reentries'] + 1,
                            'reentry_qty': int(w['qty']), 'first_opened': w['opened'], 'first_hold_days': int(w['hold_days'])}, 'oversold_v2_reentry'
    return None


def update_v2_plan(root, symbol, price, cycle_id=None, held=0, added=0):
    """After the second half was sent: mark it added and keep the disaster stop at the same price against the new
    average (from the held and added quantities; two equal halves when unknown); the entry time, targets and sessions
    stay."""
    path = Path(root) / 'exit-plans.json'
    try:
        plans = json.loads(path.read_text())
        plan = plans[symbol]
        basis = plan['target_basis']
        first = float(basis['entry_price'])
        average = (first * held + float(price) * added) / (held + added) if held and added else (first + float(price)) / 2
        basis.update(added=True, add_price=price, stop_price=round(average * (1 - OVERSOLD['oversold_v2_stop_pct'] / 100)))
        plan['stop_pct'] = float(OVERSOLD['oversold_v2_stop_pct'])  # under the new average (user 2026-11-17)
        write_json(path, plans, indent=None)
        journal_event(root, 'exit_plan', symbol=symbol, cycle_id=cycle_id, **plan)
    except (OSError, KeyError, TypeError, ValueError):
        pass  # the position keeps its first plan


def entry_plan(root, read, symbol, short):
    """(profile, target %, basis) at entry: trend in a strong tape, else a defensive full exit at the data target. An
    inverse ETF bought off a crash day (index down less than INVERSE['crash_pct']) sells everything at a small profit."""
    view, index = market_view(root), symbol_index(root, symbol)
    change = ((view or {}).get(index) or {}).get('today_change_pct')
    if short and not (type(change) in (int, float) and change <= -INVERSE['crash_pct']):
        return 'inverse_quick', INVERSE['quick_tp_pct'], {'index_change_pct': change, 'crash_pct': INVERSE['crash_pct']}
    if short:  # a crash day: a moderate profit, half and then all (user 2026-11-20), never a trailing trend plan
        return 'inverse_crash', None, {'index_change_pct': change, 'crash_pct': INVERSE['crash_pct']}
    if trend_mode(view, index, short):
        return 'trend', None, None
    regime = ((view or {}).get(index) or {}).get('regime')
    stats = daily_stats(read, symbol, datetime.now(KST))
    target = defensive_target(stats, regime)
    basis = None if target is None else {**stats, 'regime': regime, 'factor': TARGET_FACTOR.get(regime, 0.6)}
    return 'defensive', target, basis


STRENGTH_TREND_AT = 1.0  # user 2026-11-20 (replay .scratch/strength_trend_replay.py): a strength pick at +1% rides its own trend


def save_exit_plan(root, symbol, cycle_id=None, profile='defensive', target=None, basis=None, source=None):
    """Record the exit levels in force at entry; later setting changes do not move an open position's levels.
    `source` watchlist_strength is kept on the plan (entry_source): such a position switches to the trend plan at
    +STRENGTH_TREND_AT% whatever the index regime (track)."""
    plan = new_plan(symbol, profile, target=target, basis=basis)
    if source == 'watchlist_strength':
        plan['entry_source'] = source
    try:
        plans = exit_plans(root)
        plans[symbol] = plan
        write_json(Path(root) / 'exit-plans.json', plans, indent=None)
        journal_event(root, 'exit_plan', symbol=symbol, cycle_id=cycle_id, **plan)
    except (OSError, TypeError, ValueError):
        pass  # the position then follows the current settings, as before


def prune_exit_plans(root, owned):
    try:
        write_exit_plans(root, {code: plan for code, plan in exit_plans(root).items() if code in owned})
    except OSError:
        pass


def plan_levels(plan):
    """{'stop', 'half', 'all', 'trail'}: price multiples of the average; a trend plan has no fixed final take-profit."""
    if not plan:
        return {'stop': STOP_LOSS, 'half': SPLIT_TAKE_PROFIT, 'all': TAKE_PROFIT_ALL, 'trail': None}
    pct = lambda key: Decimal(str(plan[key])) / 100
    if plan.get('profile') in ('trend', 'ipo'):  # ipo: the same half + trailing shape with the new-listing numbers
        return {'stop': 1 - pct('stop_pct'), 'half': 1 + pct('trend_half_pct'), 'all': None, 'trail': pct('trend_trail_pct')}
    return {'stop': 1 - pct('stop_pct'), 'half': 1 + pct('half_pct'), 'all': 1 + pct('all_pct'), 'trail': None}


def tightening_regimes(symbol):
    """Index regimes that work against a position: a long is tightened in 횡보장·하락장, an inverse ETF in 상승장·횡보장
    (user 2026-11-17: the 12:10 inverse stop at -1.01% in a 하락장, a regime that favors the inverse)."""
    from etf_universe import SHORT
    return ('상승장', '횡보장') if symbol in {ETF7_SYMBOLS[n] for n in SHORT} else ('횡보장', '하락장')


def regime_stop_levels(plan, view, index, symbol=None):
    """Tighten ordinary positions to 1% when the current market regime works against them (direction-aware)."""
    levels = plan_levels(plan)
    if (plan or {}).get('profile') not in ('ipo', 'oversold') and ((view or {}).get(index) or {}).get('regime') in tightening_regimes(symbol):
        levels['stop'] = max(levels['stop'], Decimal('0.99'))
    return levels


def inverse_exit(plan, avg, bid, view, index):
    """Why a held inverse leaves now beyond its price levels (user 2026-11-20 "시황 잘 보고 손실 안 보게"), or None: the
    index rebounding INVERSE['rebound_exit_pct'] % over the last 30 minutes, or back at the average after the best bid
    reached +INVERSE['protect_at_pct'] %."""
    if (plan or {}).get('profile') not in ('inverse_quick', 'inverse_crash'):
        return None
    recent = (((view or {}).get('index_intraday') or {}).get(index) or {}).get('change_30m_pct')
    if type(recent) in (int, float) and recent >= INVERSE['rebound_exit_pct']:
        return 'inverse_market_rebound'
    peak = plan.get('peak')
    if (type(peak) in (int, float) and type(avg) in (int, float) and avg > 0
            and peak >= avg * (1 + INVERSE['protect_at_pct'] / 100) and bid <= avg):
        return 'inverse_protect'
    return None


def exit_reason(avg, bid, levels, protected, peak=None):
    """Why a holding must exit now, or None: final take-profit, stop, back to breakeven after a partial sale, or
    (trend plans) a fall of the trailing distance from the highest bid once the first take-profit level was reached."""
    exit_signal(avg, bid)  # keeps its input validation
    base, current = Decimal(str(avg)), Decimal(str(bid))
    if levels['all'] is not None and current >= base * levels['all']:
        return 'take_profit_all'
    if current <= base * levels['stop']:
        return 'stop_loss'
    if protected and current <= base:
        return 'breakeven_stop'
    if (levels['trail'] is not None and peak and Decimal(str(peak)) >= base * levels['half']
            and current <= Decimal(str(peak)) * (1 - levels['trail'])):
        return 'trailing_stop'
    return None


def symbol_index(root, symbol):
    """'코스닥' for KOSDAQ ETFs and KOSDAQ150 scanner stocks (from watchlist/movers metadata), else '코스피'."""
    return stop_symbol_index(root, symbol) or '코스피'


def stop_symbol_index(root, symbol):
    """Identify the market only from the ETF universe or recorded stock metadata."""
    if symbol in ETF7_SYMBOLS.values():
        return '코스닥' if symbol in KOSDAQ_CODES else '코스피'
    for name, filename in (('stocks', 'watchlist.json'), ('rows', 'movers.json')):
        try:
            market = str(((json.loads((Path(root) / filename).read_text()).get(name) or {}).get(symbol) or {}).get('index') or '').upper()
        except (OSError, ValueError, AttributeError):
            continue
        if 'KOSDAQ' in market:
            return '코스닥'
        if 'KOSPI' in market:
            return '코스피'
    return None


def weak_market(view, symbol, short, index=None):
    """The tape works against the position: long in a 하락장 that is not having a strong day (mirrored for inverse)."""
    index = (view or {}).get(index or ('코스닥' if symbol in KOSDAQ_CODES else '코스피')) or {}
    if short:
        return index.get('regime') == '상승장' and index.get('today_tone') not in ('약세', None)
    return index.get('regime') == '하락장' and index.get('today_tone') not in ('강세', None)


def averaging_down_blocked(view, symbol, short, index=None):
    """No dip-buying against a falling tape (user 2026-11-12: any weak-market day, not only a 약세 one)."""
    return weak_market(view, symbol, short, index)


def weak_targets(plan):
    """A defensive plan in a weak market takes profit early: half at WEAK_HALF_PCT and the rest at WEAK_ALL_PCT at the
    latest (or the data target when that is lower); never loosens a plan, applied once."""
    half = min(plan['half_pct'], WEAK_HALF_PCT)
    return {**plan, 'half_pct': half, 'all_pct': max(half, min(plan['all_pct'], WEAK_ALL_PCT)), 'weak_caps': True}


def stop_inside_spread(quote):
    """The stop distance must be at least two bid/ask spreads, or ordinary quoting would trigger it.

    Only a quote with a bid can be judged; entries never depended on the bid, so a missing one is not a refusal.
    """
    ask, bid = quote.get('askp'), quote.get('bidp')
    if type(ask) is not int or type(bid) is not int or not 0 < bid <= ask:
        return False
    return Decimal(ask) * (1 - STOP_LOSS) < 2 * (ask - bid)


def position_since_open(db, symbol, quantity):
    """Buys, sells, first buy price and the latest fill's side since the journal position in symbol last opened from zero."""
    remaining, buys, sells, first, bought, sold, last = quantity, 0, 0, None, 0, 0, None
    for side, qty, price in db.execute('SELECT side,quantity,price FROM fills WHERE symbol=? ORDER BY rowid DESC', (symbol,)):
        last = last or side
        if side == 'BUY':
            remaining, buys, first, bought = remaining - qty, buys + 1, price, bought + qty
        else:
            remaining, sells, sold = remaining + qty, sells + 1, sold + qty
        if remaining == 0:
            return {'buys': buys, 'sells': sells, 'first': first, 'bought': bought, 'sold': sold, 'last': last}
        if remaining < 0:
            break
    raise ValueError('position history unavailable')


def breakeven_protected(position, plan):
    """Breakeven guards the rest only while the latest fill is a sell. A buy after a partial sale re-bases the position on
    its new average and the normal stop (user approval 2026-11-17; 11/16 11:21 767 shares added after a half take-profit
    raised the average to just above the bid and were sold at breakeven 3 minutes later)."""
    return bool(position['sells']) and position.get('last') == 'SELL' and (plan or {}).get('breakeven_after_half', True)


def sold_for_profit(db, symbol):
    """A take-profit sale is among the sales since the last buy of `symbol`: only that sale arms the breakeven stop. A
    hand-chosen quantity, Jev's partial SELL or a regime trim leaves the rest under its normal stop (second review 11/17)."""
    for side, trigger in db.execute('SELECT f.side, t.trigger FROM fills f LEFT JOIN sell_order_triggers t ON t.order_id=f.order_id '
                                    'WHERE f.symbol=? ORDER BY f.rowid DESC', (symbol,)):
        if side == 'BUY':
            return False
        if trigger == 'take_profit':
            return True
    return False


def take_profit_target(plan):
    """Shares the first take-profit sells in all: half (rounded up) of `base` (take_profit_base), or of the shares bought
    when no base is given (낙폭과대 counts every sale as taken profit)."""
    return -(-plan.get('base', plan['bought']) // 2)


def take_profit_base(db, symbol, quantity):
    """The shares the first take-profit halves: those bought since the position opened, less what another rule (Jev's
    partial SELL, a regime trim, a hand-chosen quantity) sold before the first take-profit sale. User decision 2026-11-18
    (review 3 X1): half of what is left, not of what was bought, which sold the whole rest after a 50% trim. Rebuys still
    add to it, and a sale after the first take-profit does not move it. None when a sale's rule is unknown."""
    remaining, cycle = quantity, []
    for side, qty, trigger in db.execute('SELECT f.side, f.quantity, t.trigger FROM fills f LEFT JOIN sell_order_triggers t '
                                         'ON t.order_id=f.order_id WHERE f.symbol=? ORDER BY f.rowid DESC', (symbol,)):
        cycle.append((side, qty, trigger))
        remaining += -qty if side == 'BUY' else qty
        if remaining <= 0:
            break
    if remaining != 0:
        raise ValueError('position history unavailable')
    bought, other, taken = 0, 0, False
    for side, qty, trigger in reversed(cycle):
        if side == 'BUY':
            bought += qty
        elif trigger == 'take_profit':
            taken = True
        elif trigger not in ('risk', 'manual', 'model'):
            return None
        elif not taken:
            other += qty
    return bought - other


def verified_take_profit_sold(db, symbol, quantity):
    """Return confirmed take-profit quantity for this open position, or None if uncertain."""
    try:
        if type(quantity) is not int or quantity <= 0:
            return None
        left, cycle = quantity, []
        rows = db.execute('SELECT f.side,f.quantity,t.trigger,i.symbol,i.side,i.qty,i.status,f.price,i.price '
                          'FROM fills f LEFT JOIN sell_order_triggers t ON t.order_id=f.order_id '
                          'LEFT JOIN intents i ON i.id=f.order_id '
                          'WHERE f.symbol=? ORDER BY f.rowid DESC', (symbol,))
        for side, qty, trigger, intent_symbol, intent_side, intent_qty, status, fill_price, limit_price in rows:
            if side not in ('BUY', 'SELL') or type(qty) is not int or qty <= 0:
                return None
            if type(fill_price) not in (int, float) or not math.isfinite(fill_price) or fill_price <= 0:
                return None
            cycle.append((side, qty, trigger, intent_symbol, intent_side, intent_qty, status, fill_price, limit_price))
            if side == 'BUY':
                left -= qty
            else:
                left += qty
                if (intent_symbol != symbol or intent_side != 'SELL' or type(intent_qty) is not int
                        or intent_qty < qty or status != 'terminal'
                        or trigger not in ('take_profit', 'risk', 'manual', 'model')):
                    return None
                if trigger == 'take_profit':
                    if (type(limit_price) is not int or limit_price <= 0
                            or Decimal(str(fill_price)) < Decimal(limit_price)):
                        return None
            if left == 0:
                break
            if left < 0:
                return None
        if left:
            return None
        held, average, profit_sold = 0, Decimal(0), 0
        for side, qty, trigger, _, _, _, _, fill_price, _ in reversed(cycle):
            price = Decimal(str(fill_price))
            if side == 'BUY':
                average = (average * held + price * qty) / (held + qty)
                held += qty
            else:
                if qty > held:
                    return None
                if trigger == 'take_profit':
                    if price <= average:
                        return None
                    profit_sold += qty
                held -= qty
        return profit_sold if held == quantity else None
    except (sqlite3.Error, TypeError, ValueError, ArithmeticError):
        return None


def take_profit_progress(db, symbol, quantity):
    """True only when confirmed take-profit fills cover the current half target."""
    try:
        plan = position_since_open(db, symbol, quantity)
        sold, plan['base'] = verified_take_profit_sold(db, symbol, quantity), take_profit_base(db, symbol, quantity)
        return sold is not None and plan['base'] is not None and sold >= take_profit_target(plan)
    except (sqlite3.Error, TypeError, ValueError, ArithmeticError):
        return False


def split_step(plan, quantity, avg, bid, half=None):
    """'take_profit' (sell toward half of the shares bought) or None for a kept holding; adds go through scale_in_signal."""
    if (type(bid) is int and plan.get('profit_sold') is not None and plan['profit_sold'] < take_profit_target(plan)
            and quantity >= 2 and Decimal(bid) >= Decimal(str(avg)) * (half or SPLIT_TAKE_PROFIT)):
        return 'take_profit'
    return None


def adds_used_up(db, symbol, quantity):
    """The open position's adds (its buys after the first) reached scale_max_adds: every add path stops (user 2026-11-17)."""
    try:
        return position_since_open(db, symbol, quantity)['buys'] - 1 >= SCALE_MAX_ADDS
    except ValueError:
        return False  # no verified history: scale_in_history refuses it on its own


def scale_in_history(db, root, symbol, quantity, *, allow_profit_rebuy=True):
    """Return a verified open-position cycle; ambiguous sell provenance blocks re-entry."""
    try:
        if type(quantity) is not int or quantity <= 0:
            return None
        rows = db.execute('SELECT f.side,f.quantity,f.price,f.time,f.order_id,l.cycle_id '
                          'FROM fills f LEFT JOIN decision_order_links l ON l.order_id=f.order_id '
                          'WHERE f.symbol=? ORDER BY f.rowid DESC', (symbol,)).fetchall()
        left, cycle = quantity, []
        for side, qty, price, stamp, order_id, cycle_id in rows:
            if side not in ('BUY', 'SELL') or type(qty) is not int or qty <= 0:
                return None
            cycle.append((side, qty, price, stamp, order_id, cycle_id))
            left += -qty if side == 'BUY' else qty
            if left == 0:
                break
            if left < 0:
                return None
        if left or not cycle or adds_used_up(db, symbol, quantity):
            return None
        sells = [r for r in cycle if r[0] == 'SELL']
        if sells and not allow_profit_rebuy:
            return None
        if sells:
            known = {r[0]: r[1] for r in db.execute('SELECT order_id,trigger FROM sell_order_triggers')}
            missing = {r[5] for r in sells if r[4] not in known and r[5]}
            legacy = {}
            if missing:
                with (Path(root) / 'events.jsonl').open() as stream:
                    for line in stream:
                        event = json.loads(line)
                        if event.get('event') == 'sell_submitted' and event.get('cycle_id') in missing and event.get('symbol') == symbol:
                            legacy.setdefault(event['cycle_id'], []).append(event)
            for side, qty, price, stamp, order_id, cycle_id in sells:
                intent = db.execute('SELECT symbol,side,qty,status,created FROM intents WHERE id=?', (order_id,)).fetchone()
                if not intent or intent[0] != symbol or intent[1] != side or intent[2] < qty or intent[3] != 'terminal':
                    return None
                if order_id in known:
                    if known[order_id] != 'take_profit':
                        return None
                else:
                    events = legacy.get(cycle_id, [])
                    if (not cycle_id or len(events) != 1 or events[0].get('trigger') != 'take_profit'
                            or events[0].get('qty') != intent[2]
                            or db.execute('SELECT COUNT(*) FROM decision_order_links WHERE cycle_id=?', (cycle_id,)).fetchone()[0] != 1):
                        return None
                    recorded = datetime.fromisoformat(events[0]['time'])
                    filled = datetime.fromisoformat(stamp)
                    if recorded.tzinfo is None or filled.tzinfo is None or not intent[4] <= recorded.timestamp() <= filled.timestamp():
                        return None
        return cycle
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ArithmeticError):
        return None


ADD_CUTOFF_BEFORE_FLATTEN_MIN = 30


def add_window_open(now):
    """Add-on buys to a held position (new-signal add, price-step add, re-buy after a take-profit) stop 30 minutes before
    the close cleanup: 14:30 with the 15:00 flatten (user approval 2026-11-17; 11/16 adds at 14:30-14:47 only raised the
    average before the 15:00 flatten). New entries keep ENTRY_CUTOFF and 낙폭과대 v2 keeps its own window."""
    import session_times
    cutoff = session_times.clock_text(session_times.minutes(FLATTEN_AT) - ADD_CUTOFF_BEFORE_FLATTEN_MIN)
    return now.astimezone(KST).strftime('%H:%M') < cutoff


def day_iso():
    return datetime.now(KST).date().isoformat()


def add_size_factor(confidence, entry_min):
    """Share of a tranche an add buys: add_size_pct (default 50) whatever the confidence. User 2026-11-17: the
    confidence scaling was dropped because Jev entry calls at 0.8+ hit 38% vs 72% at 0.6~0.7 (signal_calibration,
    11/11~11/17); revisit when the calibration report shows higher bands hitting more often."""
    return ADD_SIZE_PCT / 100


def add_reason(avg, ask, bid, confidence, last_confidence, step):
    """Why an add improves on what the position already has, or None: 'confidence_up' (a stronger decision than the
    last buy, +0.02), 'cheaper_step' (a price a step under the average) or 'profit_step' (a step in profit). 11/17: nine
    Jev adds at 1,335~1,345 with the average 1,342 and the same confidence only piled up one bet."""
    if (type(confidence) in (int, float) and type(last_confidence) in (int, float)
            and confidence >= last_confidence + 0.02):
        return 'confidence_up'
    if Decimal(ask) <= Decimal(str(avg)) * (1 - step):
        return 'cheaper_step'
    if type(bid) is int and Decimal(bid) >= Decimal(str(avg)) * (1 + step):
        return 'profit_step'
    return None


def last_buy_confidence(db, symbol, day):
    row = db.execute('SELECT confidence FROM buy_confidence WHERE symbol=? AND time>=? ORDER BY rowid DESC LIMIT 1',
                     (symbol, day)).fetchone()
    return row[0] if row else None


def partial_sell_qty(db, symbol, quantity, confidence):
    """Shares to sell for a Jev SELL between PARTIAL_SELL_CONF and the full-sell confidence: PARTIAL_SELL_PCT of the
    position, once while the latest fill is a buy (a sale blocks adds until the position is flat, so once per position);
    0 when it does not apply."""
    if (type(confidence) not in (int, float) or not PARTIAL_SELL_CONF <= confidence < ETF7_JEV_SELL_MIN
            or PARTIAL_SELL_CONF >= ETF7_JEV_SELL_MIN):
        return 0
    try:
        if position_since_open(db, symbol, quantity).get('last') != 'BUY':
            return 0
    except ValueError:
        return 0
    qty = max(1, int(Decimal(quantity) * Decimal(str(PARTIAL_SELL_PCT)) / 100))
    return qty if qty < quantity else 0


def scale_in_allowed(db, root, symbol, quantity, *, allow_profit_rebuy=True):
    return add_window_open(datetime.now(KST)) and bool(scale_in_history(db, root, symbol, quantity, allow_profit_rebuy=allow_profit_rebuy))


def scale_in_signal(db, root, symbol, quantity, ask, closes, now, step_pct, *, allow_profit_rebuy=True, interval_min=5):
    """Use verified fills as price anchors; unproven sell reasons never permit another buy."""
    try:
        if not add_window_open(now):
            return None
        if type(ask) is not int or ask <= 0 or len(closes) < 2 or int(closes[0]) <= int(closes[1]):
            return None
        step = Decimal(str(step_pct)) / 100
        if not Decimal('0') < step < Decimal('1'):
            return None
        cycle = scale_in_history(db, root, symbol, quantity, allow_profit_rebuy=allow_profit_rebuy)
        if not cycle:
            return None
        last = cycle[0]
        at = datetime.fromisoformat(last[3])
        if at.tzinfo is None or now.tzinfo is None or (now - at).total_seconds() < interval_min * 60:
            return None
        anchor = Decimal(str(last[2]))
        if not anchor.is_finite() or anchor <= 0:
            return None
        if last[0] == 'BUY' and Decimal(ask) >= anchor * (1 + step):
            return 'trend'
        if Decimal(ask) <= anchor * (1 - step):
            return 'dip'
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ArithmeticError):
        return None
    return None


def scale_in_projected_safe(avg, quantity, ask, qty, bid, plan, view, index, protected, symbol=None):
    """Do not add a tranche that would immediately hit an exit on the new average."""
    try:
        if min(avg, quantity, ask, qty, bid) <= 0 or (plan or {}).get('exiting'):
            return False
        projected = (Decimal(str(avg)) * quantity + Decimal(ask) * qty) / (quantity + qty)
        levels = regime_stop_levels(plan, view, index, symbol)
        if protected and (ask < bid or Decimal(bid) - projected < 2 * Decimal(ask - bid)):
            return False
        if Decimal(bid) >= projected * levels['half']:
            return False
        return exit_reason(float(projected), bid, levels, protected, (plan or {}).get('peak')) is None
    except (ValueError, TypeError, ArithmeticError):
        return False


def submit(db, broker, account, day, symbol, side, qty, price, *, cycle_id=None, etf7=False, signal_check=None, deadline_ms=None, allowed=None, sell_trigger=None):
    if not order_session(datetime.now(KST)):
        raise PaperOrderNotSent('order session not open')
    if side not in ('BUY', 'SELL') or symbol not in (allowed or (ETF7_SYMBOLS if etf7 else SYMBOLS).values()) or type(qty) is not int or qty <= 0 or type(price) is not int or price <= 0:
        raise ValueError('invalid order request')
    if db.execute("SELECT 1 FROM intents WHERE status!='terminal'").fetchone():
        raise ValueError('previous submission requires reconciliation')
    if cycle_id is not None and (not isinstance(cycle_id, str) or not re.fullmatch(r'[0-9a-f]{32}', cycle_id)):
        raise ValueError('invalid decision cycle identity')
    if etf7 and ((signal_check is None)
                 or ((signal_check is None) != (deadline_ms is None))
                 or (signal_check is not None and not callable(signal_check))
                 or (deadline_ms is not None and (type(deadline_ms) is not int or not 0 < deadline_ms < 2**53
                                                   or deadline_ms <= math.floor(datetime.now(KST).timestamp() * 1000)))):
        raise ValueError('ETF guarded order deadline unavailable')
    order_id = uuid.uuid4().hex
    with db:
        db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?,?)',
                   (order_id, day, symbol, side, qty, price, 'uncertain', None, time.time()))
        if cycle_id is not None:
            db.execute('INSERT INTO decision_order_links VALUES (?,?)', (order_id, cycle_id))
        if side == 'SELL' and sell_trigger is not None:
            db.execute('INSERT INTO sell_order_triggers VALUES (?,?)', (order_id, sell_trigger))
    fields = {'act_no':account,'iem_cd':symbol,'orr_qty':qty,'orr_pr':price,'nmn_pr_tp_cd':'01',
              'orr_cnd_dit_cd':'00','ssl_nmn_pr_dit_cd':'00','rmt_mkt_cd':'KRX','sor_mkt_sli_yn':'N'}
    operation = 'krstockOrderCash' + ('Buy' if side == 'BUY' else 'Sell')
    arguments = {'operationId':operation, 'input':fields}
    checked_settings = _BUY_SETTINGS.get() if side == 'BUY' else None
    if signal_check is not None and checked_settings is not None:
        check_signal = signal_check
        root, snapshot = checked_settings
        signal_check = lambda: snapshot is not None and _settings_snapshot(root) == snapshot and check_signal()
    checks = {'pre_reserve_check':signal_check, 'pre_send_check':signal_check} if signal_check is not None else {}
    if deadline_ms is not None:
        checks['order_guard_deadline_ms'] = deadline_ms
    response = broker.call_tool('call_api', arguments, order_intent_id=order_id, **checks)
    if (signal_check is not None and isinstance(response, dict) and response.get('status') == 'error'
            and response.get('error_type') == 'NHPreSendCheckFailed'
            and response.get('order_safety') == {'state':'aborted_no_send', 'resubmit_allowed':False}):
        with db:
            updated = db.execute("UPDATE intents SET status='terminal' WHERE id=? AND status='uncertain' AND order_number IS NULL", (order_id,))
            if updated.rowcount != 1:
                raise ValueError('pre-send abort identity unavailable')
            db.execute('INSERT INTO pre_send_aborts VALUES (?,?)', (order_id, datetime.now(KST).isoformat()))
        raise PaperOrderNotSent('signal expired before transport')
    # MCPServerAdapter adds order_safety once the guard reserves an intent; without it a NHOrderBlocked never reached transport.
    if (signal_check is not None and isinstance(response, dict) and response.get('status') == 'error'
            and response.get('error_type') == 'NHOrderBlocked' and 'order_safety' not in response):
        with db:
            updated = db.execute("UPDATE intents SET status='terminal' WHERE id=? AND status='uncertain' AND order_number IS NULL", (order_id,))
            if updated.rowcount != 1:
                raise ValueError('guard refusal identity unavailable')
            db.execute('INSERT INTO pre_send_aborts VALUES (?,?)', (order_id, datetime.now(KST).isoformat()))
        raise PaperOrderGuardRefused(str(response.get('error') or 'order guard refused before transport'))
    data = response.get('data', {})
    number = data.get('Output_0', {}).get('mkt_orr_no') if isinstance(data, dict) else None
    expected_code, label = ('00048','매수') if side == 'BUY' else ('00047','매도')
    message = data.get('rsp_msg') if isinstance(data, dict) else None
    if (response.get('status') != 'ok' or type(number) is not int or number <= 0
            or data.get('rsp_cd') != expected_code or not isinstance(message, str)
            or label not in message or '완료' not in message):
        raise ValueError('submission uncertain; no retry')
    db.execute('UPDATE intents SET status=?,order_number=? WHERE id=?', ('acknowledged', number, order_id))
    db.commit()
    return order_id, number


def reconcile(db, broker, account, row, *, allow_cancel=True, stopping=False):
    order_id, day, symbol, side, qty, _, status, number, created = row
    if status != 'acknowledged' or type(number) is not int or number <= 0:
        raise ValueError('submission identity unknown; reconcile manually')
    data = paper_pages(broker, 'krstockInquiryDailyOrderExecution', {'act_no':account,'orr_dt':day,
                       'orr_mkt_cd':'00','ost_cns_dit':'0'}, 'Output_0')
    matches = [r for r in data['Output_0'] if r.get('itg_orr_no') == number and r.get('iem_cd') == symbol]
    if len(matches) != 1:
        raise ValueError('order history ambiguous')
    item = matches[0]
    if item.get('sby_dit_cd_nm') != ('현금매수' if side == 'BUY' else '현금매도'):
        raise ValueError('order side mismatch')
    counts = [item.get(k) for k in ('orr_qty', 'tot_cns_qty', 'ny_cns_qty', 'can_qty')]
    if any(type(v) is not int or v < 0 for v in counts) or counts[0] != qty or sum(counts[1:]) != qty:
        raise ValueError('order quantities invalid')
    _, filled, remaining, cancelled = counts
    if remaining:
        if not allow_cancel or (not stopping and time.time() - created < 30):
            return False
        if db.execute('SELECT 1 FROM cancels WHERE order_id=?', (order_id,)).fetchone():
            return False
        cancel_id = uuid.uuid4().hex
        db.execute('INSERT INTO cancels VALUES (?,?,?)', (order_id,cancel_id,time.time()))
        db.commit()
        result = broker.call_tool('call_api', {'operationId':'krstockOrderCancel','input':{
            'act_no':account,'iem_cd':symbol,'org_mkt_orr_no':number,'all_pat_dit_cd':'1'}},
            order_intent_id=cancel_id)
        data = result.get('data')
        message = data.get('rsp_msg') if isinstance(data, dict) else None
        if (result.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00192'
                or not isinstance(message, str) or '취소' not in message or '완료' not in message):
            raise ValueError('cancel outcome uncertain; no retry')
        return False
    if filled:
        price = item.get('cns_avg_uit_pr')
        if type(price) not in (int,float) or not math.isfinite(price) or price <= 0:
            raise ValueError('fill price invalid')
        db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', (order_id,symbol,side,filled,price,datetime.now(KST).isoformat()))
    db.execute("UPDATE intents SET status='terminal' WHERE id=?", (order_id,))
    db.commit()
    return True


def owned_positions(db):
    result = {}
    for symbol, side, qty in db.execute('SELECT symbol,side,quantity FROM fills ORDER BY rowid'):
        result[symbol] = result.get(symbol, 0) + (qty if side == 'BUY' else -qty)
        if result[symbol] < 0:
            raise ValueError('journal ownership invalid')
    return {key:value for key,value in result.items() if value}


def verify_positions(rows, owned, *, etf7=False):
    actual = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('rsdl_qty')) not in (int,float):
            raise ValueError('paper holding invalid')
        qty, code = row['rsdl_qty'], row.get('iem_cd')
        if not math.isfinite(qty) or qty < 0 or qty != int(qty):
            raise ValueError('paper holding invalid')
        if qty:
            if (code not in (ETF7_SYMBOLS if etf7 else SYMBOLS).values() and not (etf7 and code in owned)) or code in actual:
                raise ValueError('unowned paper position')
            actual[code] = int(qty)
    if actual != owned:
        raise ValueError('broker holdings differ from journal')


def learned_filter(root, choice, now, *, etf7=False):
    """Suppress entries only after 20 strictly earlier matured observations."""
    path = Path(root) / 'paper-model.sqlite3'
    if not path.is_file():
        return {'status':'insufficient'}
    with sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True) as db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(predictions)')}
        if etf7:
            if 'universe' not in columns:
                return {'status':'insufficient'}
            row = db.execute("SELECT p.decision_time,p.alpha,p.beta,p.probability FROM predictions p JOIN runs r ON r.digest=p.run_digest WHERE p.symbol=? AND p.universe='etf7' AND r.version='beta-direction-prequential-v2' ORDER BY p.decision_time DESC, p.rowid DESC LIMIT 1", (choice,)).fetchone()
        elif 'universe' in columns:
            row = db.execute("SELECT p.decision_time,p.alpha,p.beta,p.probability FROM predictions p JOIN runs r ON r.digest=p.run_digest WHERE p.symbol=? AND ((p.universe='legacy2' AND r.version='beta-direction-prequential-v2') OR (p.universe IS NULL AND r.version='beta-direction-prequential-v1')) ORDER BY p.decision_time DESC, p.rowid DESC LIMIT 1", (choice,)).fetchone()
        else:
            row = db.execute('SELECT decision_time,alpha,beta,probability FROM predictions WHERE symbol=? ORDER BY decision_time DESC, rowid DESC LIMIT 1',(choice,)).fetchone()
    if row is None:
        return {'status':'insufficient'}
    at=datetime.fromisoformat(row[0])
    if at.tzinfo is None or at >= now or type(row[1]) is not int or type(row[2]) is not int:
        raise ValueError('learning state timing or counts invalid')
    samples=row[1]+row[2]-2
    probability=row[3]
    if samples<0 or type(probability) not in (int,float) or not math.isfinite(probability) or not 0<=probability<=1:
        raise ValueError('learning state invalid')
    return {'status':'ready' if samples>=20 else 'insufficient','samples':samples,'probability':probability}


def select_entry(choice, confidence, comparison, *, etf7=False, modes=None, universe=None):
    symbols = universe or (ETF7_SYMBOLS if etf7 else SYMBOLS)
    modes = modes if etf7 and modes is not None else {'decision': True, 'jev': True}
    if not modes['decision'] and not modes['jev']:
        return None, None, 'models_disabled'
    if not modes['decision']:
        choice, confidence = 'WAIT', 0.0
    if (not isinstance(choice, str) or choice not in {*symbols, 'WAIT'} or type(confidence) not in (int, float)
            or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        return None, None, 'invalid_decision'
    if not modes['jev']:
        if choice == 'WAIT':
            return None, None, 'decision_wait'
        if confidence >= 0.8:
            return choice, 'decision', 'entry_conditions_met'
        return None, None, 'confidence_below_threshold'
    if comparison is None:
        return None, None, 'jev_error'
    if not isinstance(comparison, dict) or comparison.get('model') != 'jev-1.14.0':
        return None, None, 'jev_model_mismatch'
    digest = comparison.get('chart_sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return None, None, 'jev_invalid'
    candidate = comparison.get('candidate') if isinstance(comparison, dict) else None
    if not isinstance(candidate, dict) or candidate.get('type') != 'choice':
        return None, None, 'jev_invalid'
    other, score = candidate.get('choice'), candidate.get('confidence')
    if (not isinstance(other, str) or other not in {*symbols, 'WAIT'} or type(score) not in (int, float)
            or not math.isfinite(score) or not 0 <= score <= 1):
        return None, None, 'jev_invalid'
    if other == 'WAIT':
        return None, None, 'jev_wait'
    if score < (ETF7_JEV_ENTRY_MIN if etf7 else 0.7):
        return None, None, 'jev_low_confidence'
    if choice == 'WAIT':
        return other, 'jev', 'entry_conditions_met'
    if choice == other and confidence >= 0.8:
        return choice, 'consensus', 'entry_conditions_met'
    if choice != other:
        return None, None, 'model_disagreement'
    return None, None, 'confidence_below_threshold'


_INVESTOR_CACHE = {}


INVESTOR_TTL = 600


def investor_flows(read, now):
    """Per-ETF daily investor net quantities for Jev (latest closed day and today's provisional); cached 10 minutes
    (2026-11-20 NH call plan P1: a daily figure, seven NH calls a refresh).

    ETFs whose read fails are left out; None when none could be read.
    """
    cached = _INVESTOR_CACHE.get('value')
    if cached and 0 <= (now - cached[0]).total_seconds() < INVESTOR_TTL:
        return cached[1]
    today = now.astimezone(KST).strftime('%Y%m%d')
    keys = (('frgn_ntby_qty', 'foreign'), ('gigwan', 'institution'), ('person', 'individual'))
    result = {}
    try:
        for name, code in ETF7_SYMBOLS.items():
            response = read.call_tool('call_api', {'operationId': 'krstockQuoteCurrentInvestor',
                                                   'input': {'market_cd': 'KRX', 'iem_cd': code, 'array_cnt': '5'}})
            data = response.get('data') if isinstance(response, dict) else None
            if (response.get('status') != 'ok' or not isinstance(data, dict) or data.get('rsp_cd') != '00000'
                    or not isinstance(data.get('Output_0'), list)):
                continue  # that ETF is left out; the others still reach Jev
            rows = [r for r in data['Output_0'] if isinstance(r, dict) and isinstance(r.get('bsop_date1'), str)
                    and all(type(r.get(k)) in (int, float) for k, _ in keys) and any(r[k] for k, _ in keys)]
            entry = {}
            for label, matches in (('last_close_day', [r for r in rows if r['bsop_date1'] < today]),
                                   ('today_provisional', [r for r in rows if r['bsop_date1'] == today])):
                if matches:
                    entry[label] = {'date': matches[0]['bsop_date1'], **{out: matches[0][k] for k, out in keys}}
            result[name] = entry
    except (AttributeError, TypeError, ValueError):
        return None
    if not result:
        return None
    _INVESTOR_CACHE['value'] = (now, result)
    return result


def fresh_model_time(value, now):
    try:
        stamp = datetime.fromisoformat(value)
        return (stamp.tzinfo is not None and stamp.utcoffset() is not None
                and stamp.astimezone(KST).date() == now.astimezone(KST).date()
                and timedelta(seconds=-5) <= now - stamp <= timedelta(seconds=60))
    except (TypeError, ValueError, OverflowError):
        return False


def fresh_series(rows, now):
    local = now.astimezone(KST)
    try:
        stamp = datetime.strptime(local.strftime('%Y%m%d') + rows[0]['bsop_time'], '%Y%m%d%H%M%S').replace(tzinfo=KST)
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return timedelta(minutes=1) <= local - stamp <= timedelta(minutes=4)


def fresh_etf7_bars(bars, now):
    return set(ETF7_SYMBOLS) <= set(bars) and all(fresh_series(rows, now) or opening_series(rows, now) for rows in bars.values())


# User 2026-11-19: "종가베팅 종목이 있다면 9:00~9:05 안에 판단해서 손절·익절·더 보유·스윙을 즉시 결정". Jev's holding
# review needs six settled bars of today (valid_bars), about 09:08; until then it reads today's settled bars followed by the
# previous session's (opening_bars, each row dated), read again every cycle, plus today's open/gap from the quote.
OPENING_REVIEW_MIN = 8


def opening_window(now):
    from session_times import minutes, order_start
    local = now.astimezone(KST)
    start = minutes(order_start(local.date()))
    return start <= minutes(local.strftime('%H:%M')) < start + OPENING_REVIEW_MIN


def carried(root, symbol, now):
    """The position was opened before today (its exit plan's date); unknown counts as carried."""
    opened = str((exit_plans(root).get(symbol) or {}).get('opened') or '')
    return opened[:10] < now.astimezone(KST).date().isoformat()


def opening_split(bars, now):
    """(today's rows by chart, the previous session's rows by chart) of the opening review's bars: only today's may be
    logged as market_bars (the ledger keeps bsop_time only, so yesterday's 15:19 would become today's 15:19)."""
    today = now.astimezone(KST).strftime('%Y%m%d')
    split = lambda keep: {n: [r for r in rows if (r.get('bsop_date', today) == today and r.get('market') != 'NXT') == keep]  # noqa: E731
                          for n, rows in bars.items()}
    return {n: r for n, r in split(True).items() if r}, {n: r for n, r in split(False).items() if r}


def opening_series(rows, now):
    return (isinstance(rows, list) and bool(rows) and all(isinstance(r, dict) and r.get('bsop_date') for r in rows)
            and opening_window(now))


def opening_bars(data, code, now):
    """Today's settled 1-minute bars (newest first, the current and previous minute left out as in valid_bars) and then
    the previous session's, each with its bsop_date; up to 30 rows."""
    if data.get('rsp_cd') != '00000' or (data.get('Output_0') or {}).get('iem_cd') != code:
        raise ValueError('bar response invalid')
    fields = ('bsop_date', 'bsop_time', 'stck_oprc', 'stck_hgpr', 'stck_lwpr', 'stck_prpr', 'vol')
    rows = [r for r in data.get('Output_1') or [] if isinstance(r, dict) and all(r.get(k) not in (None, '') for k in fields)]
    local = now.astimezone(KST)
    today, cutoff = local.strftime('%Y%m%d'), (local - timedelta(minutes=1)).strftime('%H%M00')
    ordered = sorted((r for r in rows if r['bsop_date'] == today and r['bsop_time'] < cutoff),
                     key=lambda r: r['bsop_time'], reverse=True)
    ordered += sorted((r for r in rows if r['bsop_date'] < today), key=lambda r: (r['bsop_date'], r['bsop_time']), reverse=True)
    if not ordered:
        raise ValueError('bars missing')
    return [{k: r[k] for k in fields} for r in ordered[:30]]


def entry_rows(data, code, now, opening, premarket=None):
    """Entry chart rows: valid_bars (six settled bars of today), or inside the opening window the opening_bars (today's
    settled bars, then the previous session's). User 2026-11-20: "9:00~9:05가 피크라 바로 시작" — entries no longer wait
    until about 09:06 for six bars of today. `premarket(code)`: a stock's NXT pre-market rows, used instead of the previous
    session's when there are any (user 2026-11-20 '키움으로 바꾸기'; ETFs have no NXT trading)."""
    try:
        return valid_bars(data, code, now)
    except ValueError as exc:
        if not opening or str(exc) != 'insufficient bars':
            raise
        rows = opening_bars(data, code, now)
        pre = premarket(code) if premarket else []
        if not pre:
            return rows
        today = now.astimezone(KST).strftime('%Y%m%d')
        return ([r for r in rows if r['bsop_date'] == today] + pre)[:30]


_NXT_PRE = {}  # (day, code) -> rows: the pre-market is over by 08:50, so one Kiwoom read per stock a day


def nxt_premarket(code, now=None):
    """Today's NXT pre-market 1-minute rows of a stock (Kiwoom ka10080 `_NX`), dated today and marked market NXT so they are
    charted but never logged or kept as KRX bars; [] when Kiwoom has none or fails (the previous session's rows stand in)."""
    now = now or datetime.now(KST)
    key = (now.astimezone(KST).date(), code)
    if key not in _NXT_PRE:
        try:
            import kiwoom_data
            rows = kiwoom_data.nxt_premarket_bars(code, now)
        except Exception:  # noqa: BLE001 - no key, a failed call: the opening chart falls back to yesterday's bars
            return []
        day = now.astimezone(KST).strftime('%Y%m%d')
        if any(k[0] != key[0] for k in _NXT_PRE):
            _NXT_PRE.clear()  # yesterday's go
        _NXT_PRE[key] = [{'bsop_date': day, 'market': 'NXT', **r} for r in rows]
    return _NXT_PRE[key]


OPENING_ENTRY_NOTE = ('Opening minutes of the session: today\'s settled KRX bars come first; rows marked market NXT are today\'s '
                      'NXT pre-market trades (08:00~08:50) of that stock, rows with bsop_date before today are the previous '
                      'session\'s last bars (ETFs have no NXT trading). Judge the open now from them, today\'s open/gap in the '
                      'candidates and premarket_context (overnight markets); do not wait for more bars.')


def etf7_signal_deadline(bars, decision_time, jev_time, now, *, modes=None):
    modes = modes or {'decision': True, 'jev': True}
    times = [value for enabled, value in ((modes['decision'], decision_time), (modes['jev'], jev_time)) if enabled]
    if (not hasattr(now, 'tzinfo') or now.tzinfo is None or now.utcoffset() is None
            or not times or any(not fresh_model_time(value, now) for value in times)
            or not fresh_etf7_bars(bars, now)):
        return None
    local = now.astimezone(KST)
    if not in_session(local):
        return None
    try:
        timestamps = [datetime.fromisoformat(value) for value in times]
        if any(stamp > now for stamp in timestamps):
            return None
        bar_cutoffs = [datetime.strptime(local.strftime('%Y%m%d') + rows[0]['bsop_time'], '%Y%m%d%H%M%S').replace(tzinfo=KST)
                       + timedelta(minutes=4) for rows in bars.values()]
        cutoffs = [*(stamp + timedelta(seconds=60) for stamp in timestamps),
                   session_close_at(local), *bar_cutoffs]
        deadline_ms = math.floor(min(cutoffs).timestamp() * 1000)
        return deadline_ms if math.floor(now.timestamp() * 1000) < deadline_ms else None
    except (TypeError, ValueError, OverflowError, KeyError, IndexError):
        return None


def etf7_protective_deadline(quote, symbol, now):
    try:
        if (now.tzinfo is None or now.utcoffset() is None or not isinstance(quote, dict)
                or quote.get('iem_cd') != symbol or quote.get('market_status') != '1'):
            return None
        local = now.astimezone(KST)
        if not in_session(local):
            return None
        stamp = datetime.strptime(local.strftime('%Y%m%d') + quote['hoga_bsop_hour'], '%Y%m%d%H:%M:%S').replace(tzinfo=KST)
        if not timedelta(seconds=-5) <= local - stamp <= timedelta(seconds=120):
            return None
        deadline_ms = math.floor(min(stamp + timedelta(seconds=120),
                                     session_close_at(local)).timestamp() * 1000)
        return deadline_ms if math.floor(now.timestamp() * 1000) < deadline_ms else None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return None


def held_stock_context(read, root, symbol, quote, now):
    """Current holding figures and optional stock research, with absent fields kept unknown."""
    from watchlist import fresh_stock_news, investor_days, load_candidates, stock_news

    quote = quote if quote and quote.get('iem_cd') == symbol else {}
    details = load_candidates(now, Path(root) / 'watchlist.json', Path(root) / 'watchlist-manual.json').get(symbol) or {}
    name = str(quote.get('iem_nm') or details.get('name') or '').lstrip('#*').strip() or None
    try:
        news = fresh_stock_news(symbol, name, now, fetch=lambda label, at: stock_news(label, at, timeout=3))
    except Exception:  # optional research must not interrupt the holding review
        news = None
    if news is None:
        news = details.get('news')
    headlines = recent_stock_headlines(news, now, limit=3)
    cached = _INVESTOR_CACHE.get(symbol)
    if not cached or cached[0].date() != now.date() or not 0 <= (now - cached[0]).total_seconds() < INVESTOR_TTL:
        try:
            flows = investor_days(read, symbol, now.strftime('%Y%m%d'))
        except Exception:
            flows = None
        cached = _INVESTOR_CACHE[symbol] = (now, flows)
    figures = {}
    for label, field in (('session_volume_shares', 'acml_vol'), ('session_turnover_million_krw', 'acml_tr_pbmn')):
        value = quote.get(field)
        figures[label] = value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    return {'symbol': symbol, 'name': name, **figures, 'quote_as_of_kst': quote.get('hoga_bsop_hour'),
            'headlines': headlines, 'headlines_status': ('recent' if headlines else 'unavailable' if news is None else 'none_recent'),
            'investor_daily_net_qty': cached[1] or None,
            'investor_read_at': cached[0].isoformat(timespec='seconds') if cached[1] else None}


def holding_action(read, root, symbol, avg, bid, *, cycle_id=None, etf7=False, signal_context=None, modes=None, stop_hit=None, position_extra=None,
                   position_quote=None,
                   bar_cache=None):
    if price_exit(avg, bid) if stop_hit is None else stop_hit:
        return True
    modes = modes if etf7 and modes is not None else {'decision': True, 'jev': True}
    if etf7 and not modes['decision'] and not modes['jev']:
        return False
    try:
        bars = {}
        series = dict(ETF7_SYMBOLS if etf7 else SYMBOLS)
        if etf7 and symbol not in series.values():
            series[symbol] = symbol  # a held scanner stock is charted under its own code
        opening = etf7 and opening_window(datetime.now(KST)) and carried(root, symbol, datetime.now(KST))
        opening_used = False
        for name, code in series.items():
            response = minute_bars(read, code, bar_cache, prior=opening)
            if response.get('status') != 'ok':
                raise ValueError('paper signal bars unavailable')
            try:
                bars[name] = valid_bars(response.get('data', {}), code, datetime.now(KST))
            except ValueError as exc:
                if not opening or str(exc) != 'insufficient bars':
                    raise
                bars[name] = opening_bars(response.get('data', {}), code, datetime.now(KST))
                opening_used = True
        if etf7 and not fresh_etf7_bars(bars, datetime.now(KST)):
            raise ValueError('stale holding bars')
        if etf7:
            from etf_news import headlines7
            news = headlines7(datetime.now(KST))
        else:
            news = headlines(datetime.now(KST))
        response_time = jev_time = None
        choice, confidence = None, None
        review_type = 'preclose' if datetime.now(KST).strftime('%H:%M') >= preclose_from() else 'regular'
        extra = {'market_bars': bars, 'market_source': 'NH KRX 1-minute completed bars', 'universe': 'etf7'} if etf7 else {}
        if opening_used:
            today_rows, prior_rows = opening_split(bars, datetime.now(KST))
            extra.pop('market_bars')
            if set(today_rows) & set(ETF7_SYMBOLS):  # the ledger takes a partial set only when some ETF is in it
                extra['market_bars'] = today_rows
            extra['opening_prior_bars'] = prior_rows
        investor = investor_flows(read, datetime.now(KST)) if etf7 and modes['jev'] else None
        stock_context = (held_stock_context(read, root, symbol, position_quote, datetime.now(KST))
                         if etf7 and symbol not in ETF7_SYMBOLS.values() else None)
        if etf7:
            extra['investor_flows'] = investor
        if stock_context is not None:
            extra['held_stock_context'] = stock_context
        if modes['decision']:
            judge = holding_decision7 if etf7 else holding_decision
            context = {'stock_context': stock_context} if stock_context is not None else {}
            choice, confidence, usage = judge(bars, news, symbol, avg, bid, **context)
            if etf7:
                response_time = usage.get('response_time') if isinstance(usage, dict) else None
                if not fresh_model_time(response_time, datetime.now(KST)):
                    raise ValueError('stale holding decision')
                extra.update(model_response_time=response_time, review_type=review_type)
            journal_event(root, 'v2_holding_decision', symbol=symbol, choice=choice, confidence=confidence, usage=usage, cycle_id=cycle_id, **extra)
        elif etf7:
            journal_event(root, 'market_context', symbol=symbol, context='holding', news=news,
                          cycle_id=cycle_id, **extra)
        if etf7:
            if not modes['jev']:
                if not fresh_model_time(response_time, datetime.now(KST)) or not fresh_etf7_bars(bars, datetime.now(KST)):
                    raise ValueError('stale holding inputs')
                if isinstance(signal_context, dict):
                    signal_context.update(response_time=response_time, jev_time=None, bars=bars, modes=modes)
                return choice == 'SELL' and confidence >= 0.8
            extra = {'own_track_record': track_record(root), 'opening_review': opening_quote(read, symbol) if opening_used else None,
                     'market_regime_themes_global': market_view(root), 'past_falls_reference': oversold_cases.bars_context(bars),
                     'trading_journal': trading_journal_brief(), 'premarket_context': premarket_view(),
                     'market_playbook': market_playbook.brief('holding'), 'held_stock_context': stock_context}
            comparison = jev_holding7(bars, news, symbol, avg, bid, investor=investor,
                                      extra_state={k: v for k, v in extra.items() if v} or None, position_extra=position_extra)
            candidate = comparison.get('holding') if isinstance(comparison, dict) else None
            digest = comparison.get('chart_sha256') if isinstance(comparison, dict) else None
            jev_time = comparison.get('response_time') if isinstance(comparison, dict) else None
            if (not isinstance(comparison, dict) or comparison.get('model') != 'jev-1.14.0'
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                    or not isinstance(candidate, dict) or candidate.get('type') != 'choice'
                    or candidate.get('choice') not in ('HOLD', 'SELL')
                    or type(candidate.get('confidence')) not in (int, float)
                    or not math.isfinite(candidate['confidence']) or not 0 <= candidate['confidence'] <= 1
                    or not fresh_model_time(jev_time, datetime.now(KST))):
                raise ValueError('invalid Jev holding decision')
            journal_event(root, 'jev_holding_decision', symbol=symbol, choice=candidate['choice'],
                          confidence=candidate['confidence'], model='jev-1.14.0', model_response_time=jev_time,
                          review_type=review_type, input_digest=comparison.get('input_digest'),
                          evidence=comparison.get('evidence') or None, cycle_id=cycle_id)
            if ((modes['decision'] and not fresh_model_time(response_time, datetime.now(KST)))
                    or not fresh_model_time(jev_time, datetime.now(KST))
                    or not fresh_etf7_bars(bars, datetime.now(KST))):
                raise ValueError('stale holding inputs')
            if isinstance(signal_context, dict):
                signal_context.update(response_time=response_time, jev_time=jev_time, bars=bars, modes=modes, opening=opening_used,
                                      jev_sell_confidence=candidate['confidence'] if candidate['choice'] == 'SELL' else None)
            return ((not modes['decision'] or (choice == 'SELL' and confidence >= 0.8))
                    and candidate['choice'] == 'SELL' and candidate['confidence'] >= ETF7_JEV_SELL_MIN)
        try:
            journal_event(root, 'jev_shadow', openai_choice=choice, jev=jev(bars, news), cycle_id=cycle_id)
        except Exception as exc:
            journal_event(root, 'jev_error', error_type=type(exc).__name__, cycle_id=cycle_id)
        return choice == 'SELL' and confidence >= 0.8
    except Exception as exc:
        detail = {'detail': str(exc)} if re.fullmatch(r'[a-z ]{3,60}', str(exc)) else {}  # internal messages only
        journal_event(root, 'v2_holding_decision_error', symbol=symbol, error_type=type(exc).__name__, cycle_id=cycle_id, **detail)
        return False


def opening_quote(read, symbol):
    """Today's open, high, low, last and change from the previous close for the opening review; {} when unread."""
    try:
        quote = (read.call_tool('get_stock_price', {'stock_code': symbol}).get('data') or {}).get('Output_0') or {}
    except Exception:
        quote = {}
    out = {k: quote[f] for k, f in (('open', 'stck_oprc'), ('high', 'stck_hgpr'), ('low', 'stck_lwpr'), ('last', 'stck_prpr'),
                                    ('change_pct', 'prdy_ctrt')) if quote.get(f) not in (None, '')}
    if out:
        out['note'] = ('Opening review (first minutes of the session): rows with bsop_date before today in the charts are '
                       "the previous session's last bars; today's settled bars come first. This position was carried "
                       'from an earlier session: decide now whether to keep it (today or longer) or sell it.')
    return out


def direction_switch_targets(holdings, owned, buying_short, short_codes, view, root):
    """Opposite-direction holdings to close when Jev confidently picks the other side: only those below their
    average price and in a market that works against them (weak_market). Winners stay; the new entry then waits."""
    targets = []
    for row in holdings:
        code = row.get('iem_cd') if isinstance(row, dict) else None
        now, avg = row.get('now_pr') if code else None, row.get('phs_pr') if code else None
        if (code in owned and (code in short_codes) != buying_short and type(now) in (int, float) and type(avg) in (int, float)
                and 0 < now < avg and weak_market(view, code, code in short_codes, symbol_index(root, code))):
            targets.append(code)
    return targets


def request_exits(root, codes):
    """Ask to sell these codes entirely (the runner sells them through its protective path and clears each when flat)."""
    path = Path(root) / 'exit-requests.json'
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError):
        current = []
    current = {c: None for c in current} if isinstance(current, list) else current if isinstance(current, dict) else {}
    merged = {**{c: q for c, q in current.items() if isinstance(c, str)}, **{c: None for c in codes}}
    write_requests(path, merged if any(merged.values()) else sorted(merged))  # sell-alls only: the plain list as before


def exit_requests(root, owned):
    """{code: shares or None (all)} the user asked to sell: a JSON list sells all, an object {code: shares} sells that
    many (user 2026-11-17). A request lapses once the journal no longer owns the symbol."""
    path = Path(root) / 'exit-requests.json'
    try:
        requested = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}  # an unreadable request never triggers a sale
    if isinstance(requested, list):  # a non-string item makes the whole file malformed (never a TypeError)
        requested = {code: None for code in requested} if all(isinstance(c, str) for c in requested) else None
    if (not isinstance(requested, dict) or not all(isinstance(code, str) and re.fullmatch(CODE_PATTERN, code) for code in requested)
            or not all(qty is None or (type(qty) is int and qty > 0) for qty in requested.values())):
        return {}
    active = {code: qty for code, qty in requested.items() if code in owned}
    if active != requested:
        write_requests(path, active)
    return active


def write_requests(path, requests):
    if not requests:
        Path(path).unlink(missing_ok=True)
        return
    write_json(path, requests, indent=None)


def manual_buys(root):
    """{code: shares} the user asked to buy by hand (manual-buys.json); empty on any doubt."""
    try:
        requested = json.loads((Path(root) / 'manual-buys.json').read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(requested, dict):
        return {}
    return {c: q for c, q in requested.items() if isinstance(c, str) and re.fullmatch(CODE_PATTERN, c) and type(q) is int and q > 0}


def manual_buy(db, read, broker, account, root, day, owned, execute_orders):
    """One hand-requested buy per cycle (user 2026-11-17), through the protective quote path and the order guard. The
    request is taken when the order goes to the guard (an uncertain send must never buy it twice) and kept by a dry run,
    a stale quote or an order proven unsent; it lapses from the flatten time, when the close cleanup would sell it. The
    order is recorded with source 'manual' and a plan like any entry."""
    requests = manual_buys(root)
    if not requests:
        return None
    code, qty = next(iter(requests.items()))
    path = Path(root) / 'manual-buys.json'
    done = lambda: write_requests(path, {c: q for c, q in manual_buys(root).items() if c != code})
    cycle_id = uuid.uuid4().hex
    if datetime.now(KST).strftime('%H:%M') >= FLATTEN_AT:
        journal_event(root, 'v2_execution_gate', permitted=False, reason='manual_after_flatten', symbol=code, source='manual', cycle_id=cycle_id)
        done()
        return None
    quoted = read.call_tool('get_stock_price', {'stock_code': code})
    try:
        quote = fresh_quote(quoted.get('data', {}), code, datetime.now(KST)) if quoted.get('status') == 'ok' else None
    except ValueError:
        quote = None
    price = (quote or {}).get('askp')
    if type(price) is not int or price <= 0:
        journal_event(root, 'v2_execution_gate', permitted=False, reason='quote_unavailable', symbol=code, source='manual', cycle_id=cycle_id)
        return None  # kept; the cycle goes on to its other entries
    if not execute_orders:
        return 'would_buy'
    ensure_trading_open(root, day)
    deadline_ms = etf7_protective_deadline(quote, code, datetime.now(KST))
    if deadline_ms is None:
        journal_event(root, 'v2_execution_gate', permitted=False, reason='signal_or_session_invalid', symbol=code, source='manual', cycle_id=cycle_id)
        return None

    def current():
        try:
            ensure_trading_open(root, day)
            return etf7_protective_deadline(quote, code, datetime.now(KST)) == deadline_ms
        except (OSError, TypeError, ValueError):
            return False
    from etf_universe import SHORT
    if code not in owned:
        save_exit_plan(root, code, cycle_id, *entry_plan(root, read, code, code in {ETF7_SYMBOLS[n] for n in SHORT}))
    done()
    try:
        submit(db, broker, account, day, code, 'BUY', qty, price, cycle_id=cycle_id, etf7=True, signal_check=current,
               deadline_ms=deadline_ms, allowed=set(ETF7_SYMBOLS.values()) | set(owned) | {code})
    except PaperOrderGuardRefused as exc:
        journal_event(root, 'v2_execution_gate', permitted=False, reason='order_guard_refused', symbol=code, source='manual',
                      cycle_id=cycle_id, **guard_note(exc))
        return 'wait'  # the guard's limits will not change this minute; the user asks again
    except PaperOrderNotSent:
        journal_event(root, 'v2_execution_gate', permitted=False, reason='signal_or_session_invalid', symbol=code, source='manual', cycle_id=cycle_id)
        write_requests(path, {**manual_buys(root), code: qty})  # proven unsent: tried again next cycle
        return None
    journal_event(root, 'submitted', symbol=code, limit=price, qty=qty, source='manual', cycle_id=cycle_id)
    return 'buy'


def ensure_trading_open(root, day):
    now = datetime.now(KST)
    if now.strftime('%Y%m%d') != day or not order_session(now) or (root / 'STOP').exists():
        raise ValueError('paper session stopped before submission')


def settle_before_stop(db, broker, account, root, execute_orders):
    pending = db.execute("SELECT id,day,symbol,side,qty,price,status,order_number,created FROM intents WHERE status!='terminal' ORDER BY rowid").fetchall()
    if len(pending) > 1:
        journal_event(root, 'stopped', reason='multiple unresolved submissions', reconciliation_required=True)
        return
    if pending:
        try:
            for attempt in range(6):
                if reconcile(db, broker, account, pending[0], allow_cancel=execute_orders, stopping=True):
                    break
                if attempt < 5:
                    time.sleep(5)
            else:
                journal_event(root, 'stopped', reason='order remains unverified', reconciliation_required=True)
                return
        except Exception as exc:
            journal_event(root, 'stopped', reason='order reconciliation failed', error_type=type(exc).__name__, reconciliation_required=True)
            return
    positions = owned_positions(db)
    journal_event(root, 'stopped', reason='session or STOP', remaining=positions, reconciliation_required=bool(positions))


@cool_refused_picks
@buy_settings_cycle
def cycle(db, read, broker, account, root, execute_orders, now, *, etf7=False):
    day = now.astimezone(KST).strftime('%Y%m%d')
    keep_today(datetime.now(KST).date(), _DAILY, _OVERNIGHT_LOGGED, _OVERSOLD_LOGGED, _OVERSOLD_ASKED, _V2_LOGGED, _V2_BOUGHT, _V2_VERDICT,
               _SWITCH_EXITS)
    observing_open = in_observation_session(now) and not order_session(now)
    pending = db.execute("SELECT id,day,symbol,side,qty,price,status,order_number,created FROM intents WHERE status!='terminal' ORDER BY rowid").fetchall()
    if len(pending) > 1:
        raise ValueError('multiple unresolved submissions')
    if pending:
        complete = reconcile(db, broker, account, pending[0], allow_cancel=execute_orders)
        return 'reconciled' if complete else 'pending'
    history = paper_pages(broker, 'krstockInquiryDailyOrderExecution', {'act_no':account,'orr_dt':day,
                          'orr_mkt_cd':'00','ost_cns_dit':'0'}, 'Output_0')
    if any(not isinstance(r, dict) or type(r.get('ny_cns_qty')) is not int or r['ny_cns_qty'] != 0 for r in history['Output_0']):
        raise ValueError('external or ambiguous paper orders')
    balance = paper_pages(broker, 'krstockInquiryBalance', {'act_no':account,'bnc_bse_cd':'5','ltg_aot_dit_cd':'9',
                          'aet_bse':'2','qut_dit_cd':'UNT','aly_qut_cd':'1'}, 'Output_1')
    owned = owned_positions(db)
    verify_positions(balance['Output_1'], owned, etf7=etf7)
    bar_cache = {}  # this cycle's minute charts, read once for the holding reviews and the entry (r3 X3)
    settings = {}
    if etf7:
        settings = apply_settings(root)
        rows = {r.get('iem_cd'): r for r in balance['Output_1'] if isinstance(r, dict) and r.get('rsdl_qty')}
        try:  # the dashboard's account card reads it
            write_json(Path(root) / 'account-state.json', {  # atomic: readers may look at any moment
                'time': datetime.now(KST).isoformat(), 'cash': balance.get('Output_0', {}).get('orr_pbl_amt1'),
                'holdings': {c: {k: rows[c].get(k) for k in ('iem_nm', 'rsdl_qty', 'phs_pr', 'now_pr', 'eal_pls_amt', 'pft_rt')}
                             for c in owned if c in rows}}, indent=None)
        except (OSError, TypeError, ValueError):
            pass
        account_snapshot(root, balance.get('Output_0') or {})
        try:
            import position_sizing
            position_sizing.prune(root, owned)
        except OSError:
            pass  # stale entries only; tried again next cycle
    modes_error = False
    if etf7:
        try:
            modes = read_modes(root) if observing_open else apply_pending_modes(root)
        except (OSError, ValueError, TypeError):
            modes = {'version': 1, 'decision': False, 'jev': False}
            modes_error = True
    else:
        modes = {'decision': True, 'jev': True}
    local = db.execute('SELECT COUNT(*) FROM intents i WHERE day=? AND NOT EXISTS (SELECT 1 FROM pre_send_aborts a WHERE a.order_id=i.id)', (day,)).fetchone()[0]
    cancelled = db.execute('SELECT COUNT(*) FROM cancels c JOIN intents i ON c.order_id=i.id WHERE i.day=?', (day,)).fetchone()[0]
    order_count = len(history['Output_0']) + local + cancelled
    max_qty = (ETF7_MAX_ORDER_QTY or float('inf')) if etf7 else 1

    def average_price(symbol):
        matches = [r for r in balance['Output_1'] if isinstance(r, dict) and r.get('iem_cd') == symbol and r.get('rsdl_qty')]
        avg = matches[0].get('phs_pr') if len(matches) == 1 else None
        if type(avg) not in (int,float) or not math.isfinite(avg) or avg <= 0:
            raise ValueError('broker average price unavailable')
        return avg

    def split_add(symbol, quantity, avg, plan, cycle_id, bars):
        """Buy one tranche only after the latest verified fill and a new price step."""
        if not fresh_etf7_bars(bars, datetime.now(KST)):
            journal_event(root, 'v2_execution_gate', permitted=False, reason='stale_market_bars', cycle_id=cycle_id)
            return None
        quoted = read.call_tool('get_stock_price', {'stock_code':symbol})
        if quoted.get('status') != 'ok':
            raise ValueError('quote unavailable')
        quote = fresh_quote(quoted.get('data', {}), symbol, datetime.now(KST))
        ask, cash = quote.get('askp'), balance.get('Output_0', {}).get('orr_pbl_amt1')
        if type(ask) is not int or ask <= 0 or type(cash) not in (int, float) or not math.isfinite(cash) or cash < 0:
            raise ValueError('paper buying power or ask unavailable')
        name = next((n for n, c in ETF7_SYMBOLS.items() if c == symbol), symbol)
        closes = [r.get('stck_prpr') for r in bars.get(name, [])[:2]]
        if not scale_in_signal(db, root, symbol, quantity, ask, closes, datetime.now(KST), float(SPLIT_STEP * 100),
                               allow_profit_rebuy=bool(settings.get('scale_profit_rebuy_on', 1)),
                               interval_min=settings.get('scale_interval_min', 5)):
            return None
        qty = min(max_qty, int(cash // ask), quantity_room(owned), SHARED_SINGLE_ORDER_KRW // ask,
                  int(SPLIT_TRANCHE_KRW * add_size_factor(None, ETF7_JEV_ENTRY_MIN)) // ask,  # add_size_pct, as the signal add (user 2026-11-17)
                  int((SPLIT_SYMBOL_KRW - avg * quantity) // ask))
        import position_sizing  # sized by holdings and market direction (user 2026-11-17)
        from etf_universe import SHORT as short_names
        qty = int(qty * position_sizing.buy_factor(settings, market_view(root), stop_symbol_index(root, symbol),
                                                   symbol in {ETF7_SYMBOLS[n] for n in short_names}, avg * quantity,
                                                   SPLIT_SYMBOL_KRW)[0])
        if qty < 1 or order_count >= 998:
            journal_event(root, 'v2_execution_gate', permitted=False, reason='split_limit', cycle_id=cycle_id)
            return None
        if not scale_in_projected_safe(avg, quantity, ask, qty, quote.get('bidp'), plans.get(symbol),
                                       market_view(root), stop_symbol_index(root, symbol), symbol in protected, symbol):
            return None
        journal_event(root, 'split_add', symbol=symbol, tranche=plan['buys'] + 1, limit=ask, qty=qty, cycle_id=cycle_id)
        if not execute_orders:
            return 'would_buy'
        ensure_trading_open(root, day)
        deadline_ms = etf7_protective_deadline(quote, symbol, datetime.now(KST))
        if deadline_ms is None:
            return None
        def add_signal_current():
            try:
                ensure_trading_open(root, day)
                now_check = datetime.now(KST)
                return (etf7_protective_deadline(quote, symbol, now_check) == deadline_ms
                        and fresh_etf7_bars(bars, now_check)
                        and fresh_model_time((comparison or {}).get('response_time'), now_check))
            except (OSError, TypeError, ValueError):
                return False
        with db:
            inserted = db.execute('INSERT OR IGNORE INTO jev_add_signals VALUES (?,?)',
                                  (symbol, comparison['response_time'])).rowcount
        if inserted != 1:
            return None
        try:
            submit(db, broker, account, day, symbol, 'BUY', qty, ask, cycle_id=cycle_id, etf7=True,
                   signal_check=add_signal_current, deadline_ms=deadline_ms, allowed=set(ETF7_SYMBOLS.values()) | set(owned))
        except PaperOrderGuardRefused as exc:
            journal_event(root, 'v2_execution_gate', permitted=False, reason='order_guard_refused', cycle_id=cycle_id, **guard_note(exc))
            if 'reconciliation' in str(exc):
                raise ValueError('order guard requires reconciliation') from exc
            return 'wait'
        except PaperOrderNotSent:
            return None
        journal_event(root, 'submitted', symbol=symbol, limit=ask, qty=qty, source='split', add_reason='price_step', cycle_id=cycle_id)
        return 'buy'

    holdings = sorted(owned.items())
    protective_pending = False
    pending_add = None
    requested = exit_requests(root, owned) if etf7 and not observing_open else {}
    plans = exit_plans(root) if etf7 else {}
    if etf7 and not observing_open and set(plans) - set(owned):
        prune_exit_plans(root, owned)  # a flat symbol's next entry must not inherit old levels
        plans = {code: plan for code, plan in plans.items() if code in owned}
    protected = set()  # a take-profit sale has started: the rest may not fall back below the average price
    if etf7:
        for symbol, quantity in holdings:
            try:
                if breakeven_protected(position_since_open(db, symbol, quantity), plans.get(symbol)) and sold_for_profit(db, symbol):
                    protected.add(symbol)  # a 낙폭과대 plan keeps its remainder for the final target or its own stop
            except ValueError:
                # the breakeven stop cannot be applied without the fill history; say so instead of silently skipping
                journal_event(root, 'v2_execution_gate', permitted=False, reason='split_history_unavailable', symbol=symbol)

    def exit_now(symbol, avg, bid):
        if not etf7:
            return price_exit(avg, bid)
        plan = plans.get(symbol) or {}
        if plan.get('exiting'):  # a full exit larger than one order keeps selling until flat
            return True
        return (exit_reason(avg, bid, regime_stop_levels(plans.get(symbol), view, stop_symbol_index(root, symbol), symbol), symbol in protected, plan.get('peak')) is not None
                or ipo_time_stop(plan, datetime.now(KST)) is not None
                or inverse_exit(plan, avg, bid, view, stop_symbol_index(root, symbol) or '코스피') is not None)

    from etf_universe import SHORT as _SHORT
    short_codes = {ETF7_SYMBOLS[n] for n in _SHORT}
    view = market_view(root) if etf7 else None
    overnight_left = OVERNIGHT_MAX_KRW

    def position_context(symbol, avg, bid, quantity):
        """What Jev's holding review needs to judge this position: its return and the exit plan it is held under."""
        plan, levels = plans.get(symbol) or {}, regime_stop_levels(plans.get(symbol), view, stop_symbol_index(root, symbol), symbol)
        pct = lambda level: None if level is None else round(float((level - 1) * 100), 2)
        if plan.get('profile') == 'oversold':  # its sales count as taken profit, as in the cycle's take-profit step
            try:
                position = position_since_open(db, symbol, quantity)
                half_sold = position['sold'] >= take_profit_target(position)
            except ValueError:
                half_sold = None
        else:
            verified = verified_take_profit_sold(db, symbol, quantity)
            try:
                base = take_profit_base(db, symbol, quantity)
                half_sold = None if verified is None or base is None else verified >= take_profit_target(
                    {**position_since_open(db, symbol, quantity), 'base': base})
            except (sqlite3.Error, TypeError, ValueError):
                half_sold = None
        return {'chart_title': next((n for n, c in ETF7_SYMBOLS.items() if c == symbol), symbol), 'quantity': quantity,
                'return_pct': round((bid / avg - 1) * 100, 2), 'exit_profile': plan.get('profile', 'defensive'),
                'stop_pct': pct(levels['stop']), 'first_take_profit_pct': pct(levels['half']),
                'final_take_profit_pct': pct(levels['all']), 'trailing_pct': None if levels['trail'] is None else float(levels['trail'] * 100),
                'peak_bid': plan.get('peak'), 'half_already_sold': half_sold,
                'pre_close_review': datetime.now(KST).strftime('%H:%M') >= ENTRY_CUTOFF,
                'same_day_exit_from': FLATTEN_AT}

    def track(symbol, bid, cycle_id, avg=None):
        """Keep the highest bid; switch a defensive plan to trend when the tape turns strong (never back), or a
        watchlist strength pick once it is STRENGTH_TREND_AT% up on its own (user 2026-11-20: 한진칼 sold at +1.3/+2.2%
        on the 하락장 caps while it ran to +5.8%; in the replay the losing side is unchanged)."""
        if observing_open:
            return
        plan, changed = plans.get(symbol), False
        own_trend = ((plan or {}).get('entry_source') == 'watchlist_strength' and (plan or {}).get('profile') == 'defensive'
                     and type(avg) in (int, float) and avg > 0 and bid >= avg * (1 + STRENGTH_TREND_AT / 100))
        if ((own_trend or trend_mode(view, symbol_index(root, symbol), symbol in short_codes))
                and (plan or {}).get('profile') not in OWN_EXIT_PROFILES):  # new-listing / rebound / quick inverse plans keep their own numbers
            plan = {**(plan or new_plan(symbol)), 'profile': 'trend', 'peak': max((plan or {}).get('peak') or 0, bid),
                    **{k: v for k, v in new_plan(symbol, 'trend').items() if k in ('trend_half_pct', 'trend_trail_pct')}}
            journal_event(root, 'exit_plan', symbol=symbol, reason='strength_trend' if own_trend else 'trend_upgrade', cycle_id=cycle_id, profile=plan['profile'],
                          **{k: plan[k] for k in ('stop_pct', 'half_pct', 'all_pct', 'trend_half_pct', 'trend_trail_pct')})
            changed = True
        if (plan and plan.get('profile') == 'defensive' and not plan.get('weak_caps')
                and weak_market(view, symbol, symbol in short_codes, symbol_index(root, symbol))):
            plan = weak_targets(plan)
            journal_event(root, 'exit_plan', symbol=symbol, reason='weak_market_targets', cycle_id=cycle_id, profile=plan['profile'], weak_caps=True,
                          **{k: plan[k] for k in ('stop_pct', 'half_pct', 'all_pct')})
            changed = True
        if plan and bid > (plan.get('peak') or 0):
            plan, changed = {**plan, 'peak': bid}, True
        if changed:
            plans[symbol] = plan
            try:
                write_exit_plans(root, plans)
            except OSError:
                pass  # the peak is kept in memory for this cycle
    if etf7 and len(holdings) > 1:
        # A slow Jev review of one holding must not delay another holding's 2% exit; any unquoted holding blocks the cycle.
        risky = set()
        for symbol, _ in holdings:
            quoted = read.call_tool('get_stock_price', {'stock_code':symbol})
            if quoted.get('status') != 'ok':
                raise ValueError('quote unavailable')
            bid = fresh_quote(quoted.get('data', {}), symbol, datetime.now(KST)).get('bidp')
            if type(bid) is not int or bid <= 0:
                raise ValueError('invalid bid')
            if exit_now(symbol, average_price(symbol), bid):
                risky.add(symbol)
        holdings.sort(key=lambda item: item[0] not in risky)
    for symbol, quantity in holdings:
        cycle_id = uuid.uuid4().hex
        avg = average_price(symbol)
        quoted = read.call_tool('get_stock_price', {'stock_code':symbol})
        if quoted.get('status') != 'ok':
            raise ValueError('quote unavailable')
        quote = fresh_quote(quoted.get('data', {}), symbol, datetime.now(KST))
        bid = quote.get('bidp')
        if type(bid) is not int or bid <= 0:
            raise ValueError('invalid bid')
        manual = symbol in requested
        switched = manual and requested[symbol] is None and (datetime.now(KST).date(), symbol) in _SWITCH_EXITS
        if etf7:
            track(symbol, bid, cycle_id, avg)
        # Before the runner stops at 15:00 a holding leaves unless its trend plan, a rising market, a profit, the
        # overnight limit and an open next session all allow carrying it.
        flatten = etf7 and datetime.now(KST).strftime('%H:%M') >= FLATTEN_AT
        if flatten and not manual:
            cost = avg * quantity
            trend_plan = (plans.get(symbol) or {}).get('profile') == 'trend'  # only then are volume and Jev worth fetching
            stats = daily_stats(read, symbol, datetime.now(KST)) if trend_plan else None
            jev_view = latest_holding_view(root, symbol, datetime.now(KST)) if trend_plan else None
            today = datetime.now(KST).date()
            plan = plans.get(symbol) or {}
            try:
                carried = trend_plan and datetime.fromisoformat(plan['opened']).astimezone(KST).date() < today
            except (KeyError, TypeError, ValueError):
                carried = False
            if carried:  # already a swing: lighter daily checks and a maximum number of sessions
                held_reason = swing_reason(plan, view, symbol_index(root, symbol), symbol in short_codes, bid, today,
                                           overnight_left, cost, stats=stats, jev_view=jev_view)
            else:
                held_reason = overnight_reason(plans.get(symbol), view, symbol_index(root, symbol), symbol in short_codes, avg, bid,
                                               symbol in protected, overnight_left, cost, today, stats=stats,
                                               jev_view=jev_view,
                                               first_target_filled=take_profit_progress(db, symbol, quantity))
            rule_reason = held_reason
            if held_reason is not None and oversold_may_carry(plan, today):
                held_reason = None  # 낙폭과대 반등 still within its sessions: it waits for its target or stop
            key = (today, symbol, held_reason, rule_reason)
            if key not in _OVERNIGHT_LOGGED:  # first decision and every change; the live mirror reads `permitted`
                journal_event(root, 'overnight_hold', symbol=symbol, permitted=held_reason is None,
                              reason=held_reason or ('oversold_hold_days' if rule_reason else 'swing_continued' if carried
                                                     else 'closing_trade_supported'),
                              **({'rule_reason': rule_reason} if rule_reason and held_reason is None else {}), cycle_id=cycle_id)
                _OVERNIGHT_LOGGED.add(key)
            if held_reason is None:
                flatten = False
                overnight_left -= cost
        v2_hold = is_v2(plans.get(symbol))  # 선별 v2 tests its own rule: stop, cap or sessions only (engines still record views)
        # A user exit request or the pre-close exit uses the protective (quote-deadline) path.
        full_exit = manual or flatten or exit_now(symbol, avg, bid)
        risk_exit = full_exit
        signal_context = {}
        jev_sell = manual or flatten or (holding_action(read, root, symbol, avg, bid, cycle_id=cycle_id, etf7=etf7,
                                                        signal_context=signal_context, modes=modes, stop_hit=exit_now(symbol, avg, bid),
                                                        bar_cache=bar_cache, position_quote=quote,
                                                        position_extra=position_context(symbol, avg, bid, quantity) if etf7 else None)
                                         and (not v2_hold or exit_now(symbol, avg, bid)))
        should_sell = jev_sell
        partial_qty = 0
        if etf7 and not should_sell and not v2_hold and not modes['decision']:
            partial_qty = partial_sell_qty(db, symbol, quantity, signal_context.get('jev_sell_confidence'))
            if partial_qty:  # a weaker Jev SELL: sell part, keep the rest under its stop and the next decisions
                should_sell = True
                journal_event(root, 'v2_holding_decision', symbol=symbol, choice='SELL_PART', confidence=signal_context.get('jev_sell_confidence'),
                              qty=partial_qty, cycle_id=cycle_id)
        regime_trim = 0
        if (etf7 and not should_sell and not v2_hold
                and (plans.get(symbol) or {}).get('profile') not in ('oversold', 'ipo')):  # their own plans
            import position_sizing
            from etf_universe import SHORT as short_names
            regime_trim = position_sizing.regime_trim(root, db, symbol, quantity, market_view(root), stop_symbol_index(root, symbol),
                                                      symbol in {ETF7_SYMBOLS[n] for n in short_names}, settings)
            if regime_trim:  # the market turned against the position after it was bought: half, once (user 2026-11-17)
                partial_qty, should_sell = regime_trim, True
                journal_event(root, 'v2_holding_decision', symbol=symbol, choice='SELL_PART', reason='regime_reversal',
                              qty=regime_trim, cycle_id=cycle_id)
        quoted = read.call_tool('get_stock_price', {'stock_code':symbol})
        if quoted.get('status') != 'ok':
            raise ValueError('quote unavailable')
        latest = fresh_quote(quoted.get('data', {}), symbol, datetime.now(KST))
        current = latest.get('bidp')
        if type(current) is not int or current <= 0:
            raise ValueError('invalid bid')
        full_exit = full_exit or exit_now(symbol, avg, current)
        risk_exit = risk_exit or full_exit
        take_profit = False
        if not should_sell:
            if not exit_now(symbol, avg, current):
                if not etf7:
                    return 'hold'
                name = next((n for n, c in ETF7_SYMBOLS.items() if c == symbol), symbol)
                closes = [row.get('stck_prpr') for row in signal_context.get('bars', {}).get(name, [])[:2]]
                try:
                    plan = position_since_open(db, symbol, quantity)
                except ValueError:
                    journal_event(root, 'v2_execution_gate', permitted=False, reason='split_history_unavailable', cycle_id=cycle_id)
                    continue
                if (plans.get(symbol) or {}).get('profile') == 'oversold':
                    plan['profit_sold'] = plan['sold']
                else:
                    plan['base'] = take_profit_base(db, symbol, quantity)
                    plan['profit_sold'] = None if plan['base'] is None else verified_take_profit_sold(db, symbol, quantity)
                if plan['profit_sold'] is None:
                    journal_event(root, 'v2_execution_gate', permitted=False, reason='take_profit_history_unavailable', cycle_id=cycle_id)
                step = split_step(plan, quantity, avg, current, half=plan_levels(plans.get(symbol))['half'])
                scale = None if step == 'take_profit' or v2_hold or not settings.get('scale_step_add_on', 1) else scale_in_signal(
                    db, root, symbol, quantity, latest.get('askp'), closes, datetime.now(KST), float(SPLIT_STEP * 100),
                    allow_profit_rebuy=bool(settings.get('scale_profit_rebuy_on', 1)), interval_min=settings.get('scale_interval_min', 5))
                if scale == 'trend' and (plans.get(symbol) or {}).get('profile') != 'trend':
                    scale = None
                if (plans.get(symbol) or {}).get('profile') == 'oversold' or (IPO_ADD_GUARD and (plans.get(symbol) or {}).get('profile') == 'ipo'):
                    scale = None
                if scale and pending_add is None and AUTO:
                    from etf_universe import SHORT
                    view = market_view(root)
                    index = symbol_index(root, symbol)
                    if view is None or index not in view:
                        # the rule cannot judge without a fresh market view; adds stay allowed as before, but visibly
                        journal_event(root, 'v2_execution_gate', permitted=True, reason='averaging_down_unknown', cycle_id=cycle_id)
                    inverse = symbol in {ETF7_SYMBOLS[n] for n in SHORT}
                    if scale == 'dip' and AVERAGING_DOWN_GUARD and averaging_down_blocked(view, symbol, inverse, index):
                        journal_event(root, 'v2_execution_gate', permitted=False, reason='downtrend_no_averaging_down', cycle_id=cycle_id)
                    elif inverse and (gate := inverse_gate(view, index)):  # adds to an inverse need the same agreement
                        journal_event(root, 'v2_execution_gate', permitted=False, reason=gate, symbol=symbol, cycle_id=cycle_id)
                    else:
                        # Adds wait until every holding's exits have been checked this cycle.
                        pending_add = (symbol, quantity, avg, plan, cycle_id, signal_context.get('bars', {}))
                if step != 'take_profit':
                    continue
                take_profit = True
                take_profit_qty = take_profit_target(plan) - plan['profit_sold']
            risk_exit = True
        if order_count >= 1000:
            raise ValueError('daily order limit reached; holding requires manual attention')
        sellable = broker.call_tool('call_api', {'operationId':'krstockInquirySellableQuantity', 'input':{
            'act_no':account,'iem_cd':symbol,'cfd_lon_cd':'00'}})
        data = sellable.get('data', {})
        available = data.get('Output_0', {}).get('sll_pbl_qty') if isinstance(data, dict) else None
        if (sellable.get('status') != 'ok' or data.get('rsp_cd') not in ('00000','XA102')
                or data.get('Output_0', {}).get('iem_cd') != symbol
                or type(available) not in (int,float) or not math.isfinite(available) or available < 1):
            raise ValueError('sellable quantity unavailable')
        quoted = read.call_tool('get_stock_price', {'stock_code':symbol})
        if quoted.get('status') != 'ok':
            raise ValueError('quote unavailable')
        quote = fresh_quote(quoted.get('data', {}), symbol, datetime.now(KST))
        price = quote.get('bidp')
        if type(price) is not int or price <= 0:
            raise ValueError('invalid bid')
        if exit_now(symbol, avg, price):
            full_exit, risk_exit, take_profit = True, True, False
        elif take_profit and Decimal(price) < Decimal(str(avg)) * plan_levels(plans.get(symbol))['half']:
            continue  # the +1% level was lost while checking sellable quantity
        deadline_ms = None
        if etf7:
            deadline_ms = (etf7_protective_deadline(quote, symbol, datetime.now(KST)) if risk_exit else
                           etf7_signal_deadline(signal_context.get('bars', {}), signal_context.get('response_time'),
                                                signal_context.get('jev_time'), datetime.now(KST), modes=modes))
            if deadline_ms is None:
                if risk_exit and flatten:
                    # an unsellable quote now must not stop the runner and leave every other holding overnight
                    journal_event(root, 'v2_risk_exit_error', symbol=symbol, reason='quote_not_fresh', cycle_id=cycle_id)
                    protective_pending = True
                    continue
                if risk_exit:
                    raise ValueError('protective quote expired')
                journal_event(root, 'v2_holding_decision_error', symbol=symbol, error_type='StaleModelDecision', cycle_id=cycle_id)
                if etf7:
                    continue
                return 'hold'
        if execute_orders:
            ensure_trading_open(root, day)
            if risk_exit:
                try:
                    reason = (((plans.get(symbol) or {}).get('exiting')
                               or exit_reason(avg, price, regime_stop_levels(plans.get(symbol), view, stop_symbol_index(root, symbol), symbol), symbol in protected, (plans.get(symbol) or {}).get('peak'))
                               or ipo_time_stop(plans.get(symbol), datetime.now(KST))
                               or inverse_exit(plans.get(symbol), avg, price, view, stop_symbol_index(root, symbol) or '코스피')
                               or ('direction_switch' if switched else 'session_flatten' if flatten else None)) if etf7 else None)
                    if etf7 and reason and not take_profit and symbol in plans and not plans[symbol].get('exiting'):
                        plans[symbol] = {**plans[symbol], 'exiting': reason}
                        write_exit_plans(root, plans)
                        if reason == 'stop_loss' and is_v2(plans[symbol]):  # 선별 v2: buy it again at a held low
                            import v2_reentry
                            basis = plans[symbol].get('target_basis') or {}
                            v2_reentry.stopped(root, symbol, opened=basis.get('first_opened') or plans[symbol]['opened'],
                                               hold_days=basis.get('first_hold_days') or plans[symbol]['hold_days'],
                                               qty=quantity, reentries=int(basis.get('reentries') or 0),
                                               limit=OVERSOLD.get('oversold_v2_reentry_max', 0),
                                               facts={k: basis.get(k) for k in ('drop_pct', 'market', 'market_low_pct', 'rel_drop')})
                    journal_event(root, 'manual_exit' if manual and not switched else 'split_take_profit' if take_profit else 'v2_risk_exit',
                                  symbol=symbol, cycle_id=cycle_id, **({'reason': reason} if reason else {}))
                except Exception:
                    # A failed diagnostic write must not prevent a protective exit.
                    pass
            def sell_signal_current():
                try:
                    ensure_trading_open(root, day)
                    observed = datetime.now(KST)
                    if math.floor(observed.timestamp() * 1000) >= deadline_ms:
                        return False
                    if risk_exit:
                        return etf7_protective_deadline(quote, symbol, observed) == deadline_ms
                    return (read_modes(root) == modes
                            and (not modes['decision'] or fresh_model_time(signal_context.get('response_time'), observed))
                            and (not modes['jev'] or fresh_model_time(signal_context.get('jev_time'), observed))
                            and fresh_etf7_bars(signal_context.get('bars', {}), observed))
                except (OSError, TypeError, ValueError):
                    return False
            cap = quantity if etf7 and full_exit and not take_profit else (ETF7_MAX_ORDER_QTY or quantity) if etf7 else 1
            sell_qty = min(cap, take_profit_qty if take_profit else partial_qty if partial_qty and not risk_exit else quantity, int(available))
            if manual and requested.get(symbol) and not (flatten or exit_now(symbol, avg, price)):
                sell_qty = min(sell_qty, requested[symbol])  # a hand-chosen quantity (user 2026-11-17); never shrinks a stop
            trigger = ('manual' if manual and not switched else 'take_profit' if take_profit
                       else 'risk' if risk_exit else 'model')
            try:
                order_id, number = submit(db, broker, account, day, symbol, 'SELL', sell_qty, price,
                                          cycle_id=cycle_id, etf7=etf7,
                                          signal_check=sell_signal_current if etf7 else None,
                                          allowed=set(ETF7_SYMBOLS.values()) | set(owned) if etf7 else None,
                                          deadline_ms=deadline_ms, sell_trigger=trigger)
            except PaperOrderNotSent:
                if risk_exit:
                    journal_event(root, 'v2_risk_exit_error', symbol=symbol, reason='signal_or_session_invalid', cycle_id=cycle_id)
                    if etf7:
                        protective_pending = True
                        continue
                    return 'protective_wait'
                journal_event(root, 'v2_holding_decision_error', symbol=symbol, error_type='SignalOrSessionInvalid', cycle_id=cycle_id)
                if etf7:
                    continue
                return 'hold'
            journal_event(root, 'sell_submitted', symbol=symbol, limit=price, qty=sell_qty, cycle_id=cycle_id, trigger=trigger)
            if manual and requested.get(symbol):  # a quantity request shrinks by what was sent; the unsold rest stays asked
                left = requested[symbol] - sell_qty
                write_requests(Path(root) / 'exit-requests.json', {**{c: q for c, q in exit_requests(root, owned).items() if c != symbol},
                                                                   **({symbol: left} if left > 0 else {})})
            if regime_trim:  # once per position, even when the order cap sent less than half
                import position_sizing
                position_sizing.mark_trimmed(root, symbol)
        return 'sell' if execute_orders else 'would_sell'
    if protective_pending:
        return 'protective_wait'  # no new entry while a 2% exit is still unsent
    if etf7 and not observing_open:
        bought = manual_buy(db, read, broker, account, root, day, owned, execute_orders)
        if bought:
            return bought
    late = etf7 and datetime.now(KST).strftime('%H:%M') >= ENTRY_CUTOFF
    v2_open = etf7 and ((v2_window() and v2_possible(read, root, owned))  # 선별 v2 may buy past the cutoff, nothing else may
                        or v2_reentry_due(root, owned))  # a stopped v2 code waiting for its low (user 2026-11-17)
    if late and not v2_open:
        cycle_id = uuid.uuid4().hex
        late = {}
        for name, code in ETF7_SYMBOLS.items():  # prices only: settlements and post-exit paths need them until 15:00
            try:
                response = minute_bars(read, code)
                if response.get('status') == 'ok':
                    late[name] = valid_bars(response.get('data', {}), code, datetime.now(KST))
            except (ValueError, TypeError, KeyError):
                continue
        if late:
            journal_event(root, 'market_context', market_bars=late, market_source='NH KRX 1-minute completed bars',
                          universe='etf7', context='late_session', cycle_id=cycle_id)
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='entry_window_closed', cycle_id=cycle_id)
        return 'wait'
    # An existing holding also waits for this cycle's fresh entry decision below.
    if etf7 and not AUTO:
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='auto_off', cycle_id=uuid.uuid4().hex)
        return 'wait'
    if etf7 and LOSING_STREAK_GUARD:
        streak_reason = losing_streak(db, day, datetime.now(KST))
        if streak_reason:
            journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason=streak_reason, cycle_id=uuid.uuid4().hex)
            return 'wait'
    if etf7 and not modes['decision'] and not modes['jev']:
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False,
                      reason='ai_settings_unavailable' if modes_error else 'models_disabled', cycle_id=uuid.uuid4().hex)
        return 'wait'
    bars = {}
    symbols = ETF7_SYMBOLS if etf7 else SYMBOLS
    opening = etf7 and opening_window(datetime.now(KST))
    try:
        for name, symbol in symbols.items():
            response = minute_bars(read, symbol, bar_cache, prior=opening)
            if response.get('status') != 'ok':
                raise ValueError('paper signal bars unavailable')
            bars[name] = entry_rows(response.get('data', {}), symbol, datetime.now(KST) if etf7 else now, opening)
    except ValueError as exc:
        if not etf7:
            raise
        # A failed entry-signal read only skips this cycle's entry; it must not stop the runner (and its 2% watch).
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='market_bars_unavailable',
                      symbol=symbol, bar_problem=BAR_PROBLEMS.get(str(exc), 'other'), cycle_id=uuid.uuid4().hex)
        return 'wait'
    movers = {}
    if etf7:
        # Today's watchlist stocks (watchlist.py) join under their codes; one that cannot be charted freshly is dropped.
        ranked = latest_mover_rows(root, datetime.now(KST))
        for code, details in mover_candidates(datetime.now(KST)).items():
            details = with_latest_figures(details, ranked.get(code), ranked.get('_as_of'))
            if code in symbols.values():
                continue
            try:
                response = minute_bars(read, code, bar_cache, prior=opening)
                rows = (entry_rows(response.get('data', {}), code, datetime.now(KST), opening, nxt_premarket)
                        if response.get('status') == 'ok' else None)
            except ValueError:
                rows = None
            if rows and (fresh_series(rows, datetime.now(KST)) or opening_series(rows, datetime.now(KST))):
                bars[code], movers[code] = rows, details
        symbols = {**symbols, **{code: code for code in movers}}
    cycle_id = uuid.uuid4().hex
    # observation only (user 2026-11-17): what the strength path would pick when Jev's own pick is blocked or cannot add
    shadow = lambda blocked: strength_shadow(root, movers, bars, owned, symbols,
                                             reentry_floors(db, root, day) if REENTRY_GUARD else None, blocked, cycle_id)
    if etf7 and not fresh_etf7_bars(bars, datetime.now(KST)):
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='stale_market_bars', cycle_id=cycle_id)
        return 'wait'
    try:
        if etf7:
            from etf_news import headlines7
            news = headlines7(datetime.now(KST))
        else:
            news = headlines(now)
    except (OSError, ValueError, ParseError) as exc:
        error_type = ('TimeoutError' if isinstance(exc, TimeoutError) else
                      'OSError' if isinstance(exc, OSError) else
                      'ValueError' if isinstance(exc, ValueError) else 'ParseError')
        journal_event(root, 'v2_news_error', error_type=error_type, cycle_id=cycle_id)
        return 'wait'
    choice, confidence, usage = None, None, {}
    investor = investor_flows(read, datetime.now(KST)) if etf7 and modes['jev'] else None
    logged = {'market_bars': bars}  # only today's rows go to market_bars (the ledger keeps bsop_time only)
    today_rows, prior_rows = opening_split(bars, datetime.now(KST))
    if prior_rows:
        logged = {'market_bars': today_rows} if set(today_rows) & set(ETF7_SYMBOLS) else {}
        logged['opening_prior_bars'] = prior_rows
    if modes['decision']:
        choice, confidence, usage = (decision7 if etf7 else decision)(bars, news)
        extra = {'universe':'etf7', 'model_response_time':usage.get('response_time')} if etf7 else {}
        journal_event(root, 'decision', choice=choice, confidence=confidence, **logged,
                      market_source='NH KRX 1-minute completed bars', model='gpt-6-luna', news=news,
                      recent_range_pct={k:volatility(v) for k,v in bars.items()}, usage=usage, cycle_id=cycle_id, **extra)
    else:
        journal_event(root, 'market_context', **logged, market_source='NH KRX 1-minute completed bars',
                      universe='etf7', context='entry', news=news, investor_flows=investor,
                      candidates={c: {k: d.get(k) for k in ('name', 'reasons', 'change_pct', 'value_mil', 'index')}
                                  for c, d in movers.items()}, track_record=track_record(root), cycle_id=cycle_id)
    comparison = None
    if modes['jev']:
        try:
            extra = {'own_track_record': track_record(root),
                     'market_regime_themes_global': market_view(root), 'premarket_context': premarket_view(),
                     'market_playbook': market_playbook.brief('entry'),  # user 2026-11-18: reference lens, not a rule
                     'opening_entry': OPENING_ENTRY_NOTE if prior_rows else None}
            extra['watchlist_details'] = {code: watchlist_brief(code, d) for code, d in movers.items()}  # facts, flows, recent news
            extra['trading_journal'] = trading_journal_brief()  # 2026-11-13: the last sessions' journal and lessons
            extra['past_falls_reference'] = oversold_cases.bars_context(  # 2026-11-13: the past of today's falls, always
                bars, {c: d.get('change_pct') for c, d in movers.items()}, {c: d.get('name') for c, d in movers.items()},
                hints={c: d.get('index') for c, d in movers.items()})
            comparison = (jev7(bars, news, investor=investor, extra_state={k: v for k, v in extra.items() if v} or None, candidates={
                code: (f"{d.get('name', code)} 개별주식({d.get('index', '')}) · {', '.join(d.get('reasons') or [])} · "
                       f"등락 {d.get('change_pct', 0):+.2f}% · 거래대금 {d.get('value_mil', 0) / 100:,.0f}억원"
                       + (f" ({d['figures_as_of']} 기준)" if d.get('figures_as_of') else ''))
                for code, d in movers.items()}) if etf7 else jev(bars, news))
            kwargs = {'openai_choice': choice} if modes['decision'] else {}
            journal_event(root, 'jev_shadow', jev=comparison, cycle_id=cycle_id, **kwargs)
        except Exception as exc:
            comparison = None
            journal_event(root, 'jev_error', error_type=type(exc).__name__, cycle_id=cycle_id)
    if observing_open:
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='opening_observation_only', cycle_id=cycle_id)
        return 'opening_observation'
    candidate, source, reason = select_entry(choice, confidence, comparison, etf7=etf7, modes=modes,
                                             universe=symbols if etf7 else None)
    if etf7 and candidate is not None and source == 'jev':  # user 2026-11-20: an inverse the gate refuses leaves the turn
        from etf_universe import SHORT as _S  # to a strong stock or a new listing, as a Jev WAIT does
        if symbols.get(candidate) in {ETF7_SYMBOLS[n] for n in _S} and (gate := inverse_refusal(
                root, symbols[candidate], bars.get(candidate) or [], candidate, read,
                ((comparison or {}).get('candidate') or {}).get('confidence'), source)):
            journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=False, reason=gate, cycle_id=cycle_id)
            candidate, reason = None, 'jev_wait'
    can_confirm = modes.get('jev')
    pick = oversold_v2_entry(read, root, owned, cycle_id, bars, news, investor, modes, db) if v2_open else None
    if pick is None and v2_open:
        pick = v2_reentry_entry(read, root, owned, cycle_id, bars, news, investor, modes)
    if pick is None and late:
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='entry_window_closed', cycle_id=cycle_id)
        return 'wait'
    if pick is None and etf7 and OVERSOLD.get('oversold_on') and can_confirm:
        found = oversold_entry(read, root, owned, bars, news, investor, modes, cycle_id)
        pick = (*found, 'oversold') if found else None
    if pick:  # 낙폭과대 (선별 v2, then the intraday rebound) comes first: such a fall is rare and does not wait
        code, rows, oversold_basis, source = pick
        candidate = code
        bars[code], symbols = rows, {**symbols, code: code}
    careful_ok = {}  # code -> regime of a careful re-entry the Jev review let through this cycle (bought smaller)

    def review(name, code, regime, summary):
        verdict = careful_review(root, db, day, name, code, bars, news, investor, regime, summary, cycle_id, movers.get(code))
        if verdict is True:
            careful_ok[code] = regime
        return verdict
    slot = None
    gated = False  # the Jev-wait strength pick already passed entry_block
    if candidate is not None and source == 'jev' and etf7:
        # strategy slots (user 2026-11-17): an inverse on a falling index and a new listing or a strong stock are
        # separate bets; the slots are checked every cycle, not only when Jev waits (11/17 Jev picked the inverse
        # 131/131 and 옵토닉스 was never looked at)
        from etf_universe import SHORT as _SHORT
        slot = slot_entry(read, root, owned, movers, bars, reentry_floors(db, root, day), {ETF7_SYMBOLS[n] for n in _SHORT},
                          datetime.now(KST), review=review)
        if slot is not None:
            code, source, rows, facts = slot
            candidate = code
            _PICK['pick'] = (code, source)  # rests if the order path refuses it (cool_refused_picks)
            if rows is not None:
                bars[code], symbols = rows, {**symbols, code: code}
            if source == 'new_listing':
                journal_event(root, 'new_listing', symbol=code, change_pct=facts['change_pct'], turnover_pct=facts['turnover_pct'],
                              sessions=facts['sessions'], cycle_id=cycle_id)
            else:
                journal_event(root, 'watchlist_strength', symbol=code, confidence=facts['jev_watchlist_confidence'],
                              change_pct=facts['change_pct'], cycle_id=cycle_id, slot=True)
    if candidate is None and etf7 and reason in ('jev_wait', 'jev_low_confidence'):
        strength_why, strength_all = None, None
        if movers:
            closed = reentry_floors(db, root, day)
            code, facts = strength_candidate(root, movers, bars, datetime.now(KST), owned, closed if REENTRY_GUARD else None,
                                             sized=lambda c, pr: strength_shares(root, c, pr, owned, closed))
            # the data gate first, as for a slot (11-20 180640: three careful_reentry_chase refusals took the cycle
            # and the new-listing look after it); a refused stock falls through to the next path
            refused = code is not None and entry_block(root, code, owned, 'watchlist_strength', bars.get(code) or [], closed,
                                                       code in short_codes, read=read, review=review)
            if code is None or refused:
                strength_why = refused or facts
                strength_all = strength_whys(code, refused)
            else:
                candidate, source, gated = code, 'watchlist_strength', True
                _PICK['pick'] = (code, source)
                journal_event(root, 'watchlist_strength', symbol=code, confidence=facts['jev_watchlist_confidence'],
                              change_pct=facts['change_pct'], cycle_id=cycle_id)
        if candidate is None:  # a recent new listing trading strongly (works in any market regime)
            code, facts, rows = ((None, 'ipo_daily_limit', None) if ipo_entries_today(root, datetime.now(KST)) >= IPO_DAILY_MAX
                                 else ipo_candidate(read, root, owned, datetime.now(KST)))
            if code is None:
                journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason=reason,
                              strength_reason=strength_why, strength_reasons=strength_all, ipo_reason=facts, cycle_id=cycle_id)
                return 'wait'
            candidate, source = code, 'new_listing'
            _PICK['pick'] = (code, source)
            bars[code], symbols = rows, {**symbols, code: code}
            journal_event(root, 'new_listing', symbol=code, change_pct=facts['change_pct'], turnover_pct=facts['turnover_pct'],
                          sessions=facts['sessions'], cycle_id=cycle_id)
    if candidate is None:
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason=reason, cycle_id=cycle_id)
        return 'wait'
    if etf7 and ((modes['decision'] and not fresh_model_time(usage.get('response_time'), datetime.now(KST)))
                 or (modes['jev'] and not fresh_model_time((comparison or {}).get('response_time'), datetime.now(KST)))):
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='stale_model_signal', cycle_id=cycle_id)
        return 'wait'
    if etf7 and not fresh_etf7_bars(bars, datetime.now(KST)):
        journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='stale_market_bars', cycle_id=cycle_id)
        return 'wait'
    # 선별 v2 buys the weakness near the close on purpose: no rising-minute check
    if RISING_CHECK and source not in V2_SOURCES and int(bars[candidate][0]['stck_prpr']) <= int(bars[candidate][5]['stck_prpr']):
        journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=False, reason='trend_not_rising', cycle_id=cycle_id)
        if etf7 and source == 'jev':
            shadow('trend_not_rising')
        return 'wait'
    if etf7:
        from etf_universe import SHORT
        short_codes = {symbols[name] for name in SHORT}
        # Long and inverse ETFs offset each other (today's 1-minute correlations -0.4 to -0.85); holding both only pays costs.
        if symbols[candidate] in owned and source != 'oversold_v2_add':
            held_symbol = symbols[candidate]
            held_plan = plans.get(held_symbol) or {}
            response_time = (comparison or {}).get('response_time')
            blocked = (source != 'jev' or not isinstance(response_time, str) or not response_time
                       or held_plan.get('profile') in ('oversold', 'ipo') or is_v2(held_plan)
                       or not (settings.get('scale_signal_add_on', 1) or (pending_add is not None and pending_add[0] == held_symbol))
                       or db.execute('SELECT 1 FROM jev_add_signals WHERE symbol=? AND response_time=?', (held_symbol, response_time)).fetchone())
            why = ('add_cutoff' if not add_window_open(datetime.now(KST)) else 'split_plan_holding' if blocked
                   else None if scale_in_allowed(db, root, held_symbol, owned[held_symbol],
                                                 allow_profit_rebuy=bool(settings.get('scale_profit_rebuy_on', 1)))
                   else 'add_count_limit' if adds_used_up(db, held_symbol, owned[held_symbol]) else 'split_plan_holding')
            if why:
                journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=False, reason=why, cycle_id=cycle_id)
                if source == 'jev':
                    shadow(why)
                return 'wait'
        etfs = set(ETF7_SYMBOLS.values())  # user 2026-11-20: a held inverse no longer keeps stocks out (and stocks not the inverse)
        if (OPPOSITE_GUARD and source not in V2_SOURCES  # 선별 v2 holds for days; a same-day inverse is another trade
                and not (source == 'new_listing' and IPO_SLOT)  # a new listing is not an index bet (user 2026-11-17)
                and symbols[candidate] in etfs
                and any((code in short_codes) != (symbols[candidate] in short_codes) for code in owned if code in etfs)):
            journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=False,
                          reason='opposite_direction_holding', cycle_id=cycle_id)
            if source == 'jev':
                shadow('opposite_direction_holding')
            buying_short = symbols[candidate] in short_codes
            switch = (direction_switch_targets(balance['Output_1'], owned, buying_short, short_codes, market_view(root), root)
                      if DIRECTION_SWITCH and source == 'jev'  # an inverse the gate refuses is never bought: the longs stay (r3 E4)
                      and not (buying_short and inverse_gate(market_view(root), symbol_index(root, symbols[candidate]))) else [])
            if switch:  # sold next cycle through the protective path, like a manual exit request
                request_exits(root, switch)
                _SWITCH_EXITS.update((datetime.now(KST).date(), code) for code in switch)
                journal_event(root, 'direction_switch', symbol=switch[0], choice=candidate, cycle_id=cycle_id)
            return 'wait'
        refused = None if slot or gated else entry_block(root, symbols[candidate], owned, source, bars[candidate], reentry_floors(db, root, day),
                                                symbols[candidate] in short_codes, name=candidate, read=read, review=review,
                                                confidence=((comparison or {}).get('candidate') or {}).get('confidence'))  # a slot passed it
        if refused:
            journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=False, reason=refused, cycle_id=cycle_id)
            if source == 'jev' and refused != 'reentry_disabled':
                shadow(refused)
            return 'wait'
        if pending_add is not None and source == 'jev' and pending_add[0] == symbols[candidate]:
            result = split_add(*pending_add)
            if result:
                return result
        if symbols[candidate] in owned and source == 'jev' and not settings.get('scale_signal_add_on', 1):
            journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=False,
                          reason='scale_signal_disabled', cycle_id=cycle_id)
            shadow('scale_signal_disabled')
            return 'wait'
    careful = careful_ok.get(symbols[candidate])  # a careful re-entry the review let through (user 2026-11-18)
    decided_at = datetime.now(KST)
    journal_event(root, 'v2_entry_gate', choice=candidate, source=source, permitted=True, reason='entry_conditions_met', cycle_id=cycle_id)
    if order_count >= 998 or quantity_room(owned) == 0:
        journal_event(root, 'v2_execution_gate', permitted=False,
                      reason='order_limit' if order_count >= 998 else 'position_limit', cycle_id=cycle_id)
        return 'limit'
    # Decision-trained research must not influence a Jev-only order.
    learned = learned_filter(root, candidate, datetime.now(KST), etf7=etf7) if modes['decision'] else {'status':'insufficient'}
    if learned['status']=='ready' and learned['probability']<0.45:
        journal_event(root, 'v2_execution_gate', permitted=False, reason='learning_filter', cycle_id=cycle_id)
        return 'learned_filter_wait'
    symbol = symbols[candidate]
    quoted = read.call_tool('get_stock_price', {'stock_code':symbol})
    if quoted.get('status') != 'ok':
        raise ValueError('quote unavailable')
    quote = fresh_quote(quoted.get('data', {}), symbol, datetime.now(KST))
    price = quote.get('askp')
    cash = balance.get('Output_0', {}).get('orr_pbl_amt1')
    if type(price) is not int or price <= 0 or type(cash) not in (int,float) or not math.isfinite(cash) or cash < 0:
        raise ValueError('paper buying power or ask unavailable')
    timing = entry_timing_context(quote, price, bars.get(candidate, []), decided_at)
    if cash < price:
        journal_event(root, 'v2_execution_gate', symbol=symbol, permitted=False, reason='insufficient_buying_power',
                      cycle_id=cycle_id, entry_timing=timing)
        return 'wait'
    if etf7 and stop_inside_spread(quote):
        journal_event(root, 'v2_execution_gate', symbol=symbol, permitted=False, reason='stop_inside_spread',
                      cycle_id=cycle_id, entry_timing=timing)
        return 'wait'
    budget = None
    if etf7:
        size_mult = oversold_basis.get('size_mult', 1) if source in V2_SOURCES else 1
        inverse_mult = ((INVERSE['late_size_pct'] if inverse_late(market_view(root), symbol_index(root, symbol))
                         else INVERSE['size_pct']) / 100 if symbol in short_codes else 1)
        budget = buy_budget(source, size_mult, inverse_mult)
    qty = min(max_qty, int(cash // price), quantity_room(owned),
              budget // price if budget is not None else max_qty)
    if source == 'oversold_v2_reentry':
        qty = min(qty, int(oversold_basis['reentry_qty']))
    sizing = None
    if etf7 and source in ('jev', 'watchlist_strength'):  # sized by holdings and market direction (user 2026-11-17)
        import position_sizing
        from etf_universe import SHORT as short_names
        held = owned.get(symbol, 0)
        factor, sizing = position_sizing.buy_factor(settings, market_view(root), stop_symbol_index(root, symbol),
                                                    symbol in {ETF7_SYMBOLS[n] for n in short_names},
                                                    average_price(symbol) * held if held else 0, SPLIT_SYMBOL_KRW)
        qty = int(qty * factor)
    if careful:  # a careful re-entry buys smaller (user 2026-11-18)
        qty = int(qty * CAREFUL['size'] / 100)
    signal_add = etf7 and symbol in owned and source == 'jev'
    jev_pick = (comparison or {}).get('candidate') or {}
    jev_conf = jev_pick.get('confidence') if symbols.get(jev_pick.get('choice')) == symbol else None  # a slot's buy is not Jev's call
    if signal_add:
        avg = average_price(symbol)
        passed = add_reason(avg, price, quote.get('bidp'), jev_conf, last_buy_confidence(db, symbol, day_iso()), SPLIT_STEP)
        if ADD_CONF_SIZING and passed is None:
            journal_event(root, 'v2_execution_gate', permitted=False, reason='add_not_better', symbol=symbol, confidence=jev_conf, cycle_id=cycle_id)
            shadow('add_not_better')
            return 'wait'
        qty = int(qty * add_size_factor(jev_conf, ETF7_JEV_ENTRY_MIN))
        qty = min(qty, max(0, int((SPLIT_SYMBOL_KRW - avg * owned[symbol]) // price)))
        if qty and not scale_in_projected_safe(avg, owned[symbol], price, qty, quote.get('bidp'), plans.get(symbol),
                                                market_view(root), stop_symbol_index(root, symbol), symbol in protected, symbol):
            journal_event(root, 'v2_execution_gate', permitted=False, reason='projected_exit', symbol=symbol, cycle_id=cycle_id)
            shadow('projected_exit')
            return 'wait'
    if qty < 1:
        note_single_order_limit(root, datetime.now(KST).date(), symbol, source, price, budget, cycle_id, sizing)
        if signal_add:  # the held position is at its per-symbol amount
            shadow('single_order_limit')
        return 'wait'
    if execute_orders:
        ensure_trading_open(root, day)
        if etf7 and ((modes['decision'] and not fresh_model_time(usage.get('response_time'), datetime.now(KST)))
                     or (modes['jev'] and not fresh_model_time(comparison.get('response_time'), datetime.now(KST)))):
            journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='stale_model_signal', cycle_id=cycle_id)
            return 'wait'
        if etf7 and not fresh_etf7_bars(bars, datetime.now(KST)):
            journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='stale_market_bars', cycle_id=cycle_id)
            return 'wait'
        if signal_add:
            with db:
                inserted = db.execute('INSERT OR IGNORE INTO jev_add_signals VALUES (?,?)',
                                      (symbol, comparison['response_time'])).rowcount
            if inserted != 1:
                return 'wait'
        deadline_ms = (etf7_signal_deadline(bars, usage.get('response_time'),
                                            comparison.get('response_time') if comparison else None,
                                            datetime.now(KST), modes=modes)
                       if etf7 else None)
        if etf7 and deadline_ms is None:
            journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='signal_or_session_invalid', cycle_id=cycle_id)
            return 'wait'
        def buy_signal_current():
            try:
                ensure_trading_open(root, day)
                observed = datetime.now(KST)
                return (read_modes(root) == modes and math.floor(observed.timestamp() * 1000) < deadline_ms
                        and (not modes['decision'] or fresh_model_time(usage.get('response_time'), observed))
                        and (not modes['jev'] or fresh_model_time(comparison.get('response_time'), observed))
                        and fresh_etf7_bars(bars, observed))
            except (OSError, TypeError, ValueError):
                return False
        if etf7 and not signal_add and source != 'oversold_v2_add':
            from etf_universe import SHORT
            # before sending: a crash after the fill must not leave it without a plan
            if source == 'new_listing':
                save_exit_plan(root, symbol, cycle_id, 'ipo')
            elif source in ('oversold', 'oversold_v2', 'oversold_v2_reentry'):
                save_exit_plan(root, symbol, cycle_id, 'oversold', target=oversold_basis['target_pct'], basis=oversold_basis)
            else:
                save_exit_plan(root, symbol, cycle_id, *entry_plan(root, read, symbol, symbol in {ETF7_SYMBOLS[n] for n in SHORT}),
                               source=source)
        try:
            order_id, number = submit(db, broker, account, day, symbol, 'BUY', qty, price,
                                      cycle_id=cycle_id, etf7=etf7,
                                      signal_check=buy_signal_current if etf7 else None, deadline_ms=deadline_ms,
                                      allowed=set(symbols.values()) if etf7 else None)
        except PaperOrderGuardRefused as exc:
            journal_event(root, 'v2_execution_gate', symbol=symbol, permitted=False, reason='order_guard_refused',
                          cycle_id=cycle_id, entry_timing=timing, **guard_note(exc))
            if 'reconciliation' in str(exc):
                # The shared guard holds an unresolved order this journal does not; retrying every minute hides it.
                raise ValueError('order guard requires reconciliation') from exc
            if source in V2_SOURCES:  # the guard's limits will not change within the day: not chased every minute
                v2_sent(source, symbol)
            if source == 'oversold_v2_reentry':
                import v2_reentry
                v2_reentry.clear(root, symbol)
            return 'wait'
        except PaperOrderNotSent:
            journal_event(root, 'v2_entry_gate', choice='WAIT', permitted=False, reason='signal_or_session_invalid', cycle_id=cycle_id)
            return 'wait'
        # observation (improvement report 11/17-3): the decision's confidence and why an add passed
        why = (passed or 'rule_off') if signal_add else 'first' if symbol not in owned else None
        journal_event(root, 'submitted', symbol=symbol, limit=price, qty=qty, source=source, cycle_id=cycle_id,
                      entry_timing=timing, **entry_context(quote, price), **({'sizing': sizing} if sizing else {}),
                      **({'confidence': jev_conf} if type(jev_conf) in (int, float) else {}), **({'add_reason': why} if why else {}),
                      **({'careful_reentry': careful} if careful else {}))
        if source == 'jev' and type(jev_conf) in (int, float):  # the bar the next add must beat (add_reason)
            with db:
                db.execute('INSERT INTO buy_confidence VALUES (?,?,?)', (symbol, datetime.now(KST).isoformat(), float(jev_conf)))
        if source in V2_SOURCES:  # one order a day per code; a gate that refused before sending is tried again next minute
            v2_sent(source, symbol)
        if source == 'oversold_v2_reentry':  # sent: its watch ends (a new stop starts the next one)
            import v2_reentry
            v2_reentry.clear(root, symbol)
        if source == 'oversold_v2_add':  # sent: the plan now counts both halves (a refused add keeps the first plan)
            update_v2_plan(root, symbol, price, cycle_id, held=owned.get(symbol, 0), added=qty)
    return 'buy' if execute_orders else 'would_buy'


def run(configs, runtime, *, execute_orders=False, once=False, websocket_wakeup=False, shared_default=False):
    root = Path(runtime) / ('nhplug/shared-etf-paper' if shared_default else 'nhplug/isolated-paper')
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if shared_default:
        config = shared_default_config(configs, runtime, MCPServerAdapter)
        digest = account_hash(paper_account(config))
    else:
        digest = json.loads((root / 'account.json').read_text())['account_sha256']
        if digest in reserved_hashes(runtime):
            raise ValueError('selected account now belongs to a swing strategy')
        config = selected_config(configs, digest)
    if execute_orders and not shared_default:
        policy = NHOrderGuard(config.args).paper_policy()
        if not isinstance(policy, dict) or policy.get('_account_independent') is not True:
            raise ValueError('independent account policy is not registered')
    account = paper_account(config)
    read = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])
    if shared_default:
        if '--guarded-paper' in config.args:
            raise ValueError('unexpected guarded paper launcher configuration')
        broker_config = config.model_copy(update={'args': [*config.args, '--guarded-paper']})
    else:
        broker_config = config
    broker = MCPServerAdapter('nhplug_paper_isolated', broker_config)
    with (root/'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = connect_journal(root/'orders.sqlite3', digest)
        stop = wake = None
        if websocket_wakeup:
            import threading
            from nh_ws_monitor import watch, wake_on_data
            stop, wake = threading.Event(), threading.Event()
            for mode in ('quote', 'paper_notice'):
                threading.Thread(target=watch, args=(mode, stop, lambda event: wake_on_data(wake, event)), daemon=True).start()
        read_failures = 0
        from session_times import until_open
        if wait := until_open(datetime.now(KST)):  # started pre-open (trading-day-start 08:55): ready at the bell
            time.sleep(wait)
        try:
            while True:
                now = datetime.now(KST)
                refresh_clock(root)
                if not in_observation_session(now) or (root/'STOP').exists():
                    settle_before_stop(db, broker, account, root, execute_orders)
                    break
                try:
                    status = cycle(db, read, broker, account, root, execute_orders and order_session(now), now, etf7=shared_default)
                    read_failures = 0
                except ValueError as exc:
                    # A failed quote read happens before any order is sent; one bad minute must not end the runner and
                    # its stop-loss watch (11/12 12:00 it did, for 14 minutes). Anything else, or 5 in a row, still stops it.
                    read_failures += 1
                    if str(exc) not in TRANSIENT_READ_ERRORS or read_failures >= TRANSIENT_READ_LIMIT:
                        raise
                    journal_event(root, 'v2_error', reason='transient_read_failure', detail=str(exc), cycle_id=uuid.uuid4().hex)
                    status = 'read_retry'
                print(json.dumps({'status':status, 'time':now.isoformat()}), flush=True)
                if once:
                    break
                if wake is None:
                    time.sleep(60)
                else:
                    # Coalesce bursts and preserve a 30-second minimum between REST cycles.
                    time.sleep(30)
                    wake.wait(30)
                    wake.clear()
        finally:
            if stop is not None:
                stop.set()
            db.close()




class RecordedBars:
    """Read adapter for the secondary Jev runner: a 1-minute bar read is served from the shared runner's latest
    market_context record when it is at most MAX_AGE seconds old and has that symbol, instead of asking NH again
    for the same bars (user request 2026-11-12). Everything else, and any miss, goes to NH unchanged."""
    MAX_AGE = 75

    def __init__(self, read, shared_root):
        self.read, self.path, self._seen = read, Path(shared_root) / 'events.jsonl', (None, None)

    def latest(self):
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return None
        if self._seen[0] != mtime:
            event = None
            with self.path.open('rb') as stream:
                start = max(0, stream.seek(0, 2) - 400_000)
                stream.seek(start)
                lines = stream.read().splitlines()[1 if start else 0:]  # a mid-file start drops its partial first line
                for line in reversed(lines):
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get('event') in ('market_context', 'decision') and isinstance(item.get('market_bars'), dict):
                        event = item
                        break
            self._seen = (mtime, event)
        return self._seen[1]

    def call_tool(self, tool, params, **kwargs):
        fields = params.get('input') or {} if isinstance(params, dict) else {}
        if tool == 'call_api' and params.get('operationId') == 'krstockQuotePeriod' and fields.get('gubun') == '5' and fields.get('xtick') == '1':
            event, now = self.latest(), datetime.now(KST)
            try:
                fresh = event is not None and 0 <= (now - datetime.fromisoformat(event['time'])).total_seconds() <= self.MAX_AGE
            except (KeyError, TypeError, ValueError):
                fresh = False
            code = fields.get('iem_cd')
            names = {v: k for k, v in ETF7_SYMBOLS.items()}
            rows = (event['market_bars'].get(names.get(code, code)) if fresh else None) or None
            if rows:
                day = now.strftime('%Y%m%d')
                return {'status': 'ok', 'source': 'shared_runner_record',
                        'data': {'rsp_cd': '00000', 'Output_0': {'iem_cd': code}, 'Output_1': [{**row, 'bsop_date': day} for row in rows]}}
        return self.read.call_tool(tool, params, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true', help='Persist selected account hash only')
    parser.add_argument('--run', action='store_true', help='Run the selected account')
    parser.add_argument('--execute', action='store_true', help='Allow paper orders')
    parser.add_argument('--once', action='store_true', help='Run one cycle')
    parser.add_argument('--websocket-wakeup', action='store_true', help='Experimental paper-only WebSocket wakeup')
    parser.add_argument('--shared-default', action='store_true', help='Use explicitly assigned default paper account')
    args = parser.parse_args()
    runtime = get_runtime_root()
    if args.run:
        if args.initialize:
            parser.error('initialize and run are separate operations')
        run(load_agent_config().mcp_servers, runtime, execute_orders=args.execute, once=args.once, websocket_wakeup=args.websocket_wakeup, shared_default=args.shared_default)
        return
    if args.execute or args.once or args.websocket_wakeup or args.shared_default:
        parser.error('--execute and --once require --run')
    digest, selected = select(load_agent_config().mcp_servers, runtime, datetime.now(KST))
    if args.initialize:
        path = runtime / 'nhplug/isolated-paper/account.json'
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            old = json.loads(path.read_text())
            if old != {'account_sha256': digest}:
                raise ValueError('existing selection differs; no overwrite')
        else:
            with os.fdopen(fd, 'w') as f:
                json.dump({'account_sha256': digest}, f)
                f.flush()
                os.fsync(f.fileno())
    policy = NHOrderGuard(selected.args).paper_policy()
    print(json.dumps({'account_sha256': digest, 'status': 'empty_as_of_today',
        'policy_scope': 'independent' if policy and policy.get('_account_independent') else 'shared_or_unregistered',
        'orders_sent': False}))


if __name__ == '__main__':
    main()
