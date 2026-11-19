"""Paper runner for the vibe-trading strategy on the separate (isolated) NH paper account.

vibe_advisor.py writes vibe-advice.json every 15 minutes (symbols, sides, quantities chosen by the vibe-trading
agent). This runner checks it every minute and executes each decision once through the same guarded order path
as the Jev runner, while enforcing on its own: stop loss (strategy settings), 3,000,000 KRW per order,
6,000,000 KRW buys per day, 5 new symbols per day, and no new buys after a 300,000 KRW daily loss
(user decision 2026-11-11). Journal: ~/.vibe-trading/nhplug/vibe-paper. Never runs alongside the legacy
isolated runner (shared account lock).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import queue
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tunables import setting
from nh_paper_isolated import (KST, ETF7_SYMBOLS, PaperOrderGuardRefused, PaperOrderNotSent, connect_journal,
                               ensure_trading_open, etf7_protective_deadline, fresh_quote, journal_event,
                               owned_positions, paper_pages, reconcile, settle_before_stop, submit, verify_positions,
                               market_view, stop_symbol_index, buy_settings_cycle, cycle_delay,
                               premarket_input_audit, entry_context)

ADVICE_MAX_AGE = timedelta(minutes=20)
TECHNICAL_START_AGE_MIN, TECHNICAL_MAX_AGE_MIN = 5, 7  # new-buy technical bar: age at the decision start / at the order
ACCOUNT_STATE_MAX_AGE = timedelta(minutes=1)
ORDER_KRW = 3_000_000
DAILY_BUY_KRW = 6_000_000
DAILY_NEW_SYMBOLS = 5
DAILY_LOSS_KRW = 300_000
# Review 2026-11-12: the agent scores the rest of today, so positions now end with the day like the Jev runner's:
# take profit at the shared take_profit_all_pct, no new buys from 14:30, everything sold from 14:40. A symbol is
# bought up to the shared per-symbol budget (3,000,000 KRW), not again on every fresh advice.
ENTRY_CUTOFF, FLATTEN_AT = '14:30', '14:40'  # refreshed every cycle from the shared settings (session_times)
SYMBOL_KRW = 3_000_000
TRANCHE_KRW = 1_000_000  # one buy tranche when nothing is saved (accounts.json paper-shared first_buy_krw)
OVERSOLD_PLANS = 'oversold-plans.json'  # 낙폭과대 반등 positions: {code: {target_price, stop_price, opened, hold_days}}
TRANSIENT_LIMIT = setting('read_failure_limit')  # consecutive failed cycles before the runner stops
BALANCE = {'bnc_bse_cd': '5', 'ltg_aot_dit_cd': '9', 'aet_bse': '2', 'qut_dit_cd': 'UNT', 'aly_qut_cd': '1'}
VIBE_CARRY_FILE = 'vibe-overnight.json'
_APPLIED_SCALE = None
_QUOTE_SNAPSHOTS = None


def _write_quote_snapshots(pending):
    while True:
        root, symbol, snapshot = pending.get()
        try:
            from config_store import write_json
            write_json(Path(root) / 'fast-exit-quotes' / f'{symbol}.json', snapshot, indent=None)
        except (OSError, TypeError, ValueError):
            pass  # Observational snapshots must never affect the trading loop.
        finally:
            pending.task_done()


def start_quote_snapshots():
    global _QUOTE_SNAPSHOTS
    if _QUOTE_SNAPSHOTS is None:
        try:
            pending = queue.Queue(maxsize=100)
            threading.Thread(target=_write_quote_snapshots, args=(pending,), daemon=True).start()
            _QUOTE_SNAPSHOTS = pending
        except Exception:
            _QUOTE_SNAPSHOTS = None


def record_protective_quote(root, symbol, quote, observed_at):
    try:
        if _QUOTE_SNAPSHOTS is None or not re.fullmatch(r'\d{6}', symbol):
            return
        snapshot = {'symbol': symbol, 'observed_at': observed_at.isoformat(), 'quote': {
            key: quote.get(key) for key in ('iem_cd', 'market_status', 'hoga_bsop_hour', 'bidp', 'askp', 'bidp1', 'bidp_rsqn1')}}
        _QUOTE_SNAPSHOTS.put_nowait((root, symbol, snapshot))
    except Exception:
        pass


def scale_settings(root):
    """Read and record the applied scale settings without blocking position protection on a log error."""
    global _APPLIED_SCALE
    from strategy_settings import read_settings
    settings, status = read_settings(Path(root).parent / 'shared-etf-paper')
    if status == 'invalid':
        settings = {**settings, 'scale_signal_add_on': 0, 'scale_step_add_on': 0, 'scale_profit_rebuy_on': 0}
    applied = {k: settings.get(k, default) for k, default in (
        ('scale_signal_add_on', 1), ('scale_step_add_on', 1), ('scale_profit_rebuy_on', 1), ('scale_interval_min', 5), ('scale_max_adds', 2))}
    import nh_paper_isolated
    nh_paper_isolated.SCALE_MAX_ADDS = int(applied['scale_max_adds'])  # the shared add checks (scale_in_history) read it
    nh_paper_isolated.ADD_CONF_SIZING = bool(settings.get('add_conf_sizing_on', 1))
    nh_paper_isolated.ADD_SIZE_PCT = float(settings.get('add_size_pct', 50))
    # the new-listing slot (ipo_buy) runs the Jev runner's ipo_candidate, which reads these module values
    nh_paper_isolated.IPO_DAYS = int(settings.get('ipo_days', 2))
    nh_paper_isolated.IPO_NEAR_HIGH_PCT = float(settings.get('ipo_near_high_pct', 3.0))
    nh_paper_isolated.IPO_DAILY_MAX = int(settings.get('ipo_daily_max', 2))
    nh_paper_isolated.IPO_TIME_STOP_MIN = int(settings.get('ipo_time_stop_min', 60))
    nh_paper_isolated.RISING_CHECK = bool(settings.get('rising_check', 1))
    applied['scale_policy_version'] = 2
    state = (str(root), applied, status)
    if state != _APPLIED_SCALE:
        try:
            journal_event(root, 'strategy_settings', settings=applied, status=status)
        except Exception:
            pass  # reporting failure must not stop protection or mark the settings as recorded
        else:
            _APPLIED_SCALE = state
    return settings


def quantity_room(owned):
    limit = setting('paper_max_total_qty')
    return max(0, limit - sum(owned.values())) if limit else math.inf


def limits():
    """The shared paper account's dashboard limits (accounts.json paper-shared), so vibe's book runs under the same
    numbers as the Jev runner; the module constants above are the defaults when nothing is saved."""
    try:
        from accounts import load, paper_limits
        record = load().get('paper-shared') or {}
        saved = paper_limits(record)
        per_symbol = (record.get('budget') or {}).get('per_symbol_krw') or SYMBOL_KRW
        tranche = (record.get('budget') or {}).get('first_buy_krw') or TRANCHE_KRW
    except Exception:
        return {'order': ORDER_KRW, 'daily_buy': DAILY_BUY_KRW, 'new_symbols': DAILY_NEW_SYMBOLS, 'loss': DAILY_LOSS_KRW,
                'symbol': SYMBOL_KRW, 'tranche': TRANCHE_KRW}
    return {'order': saved['order_max_krw'], 'daily_buy': saved['daily_buy_krw'], 'new_symbols': saved['daily_new_symbols'],
            'loss': saved['daily_loss_krw'], 'symbol': per_symbol, 'tranche': tranche}


def load_advice(root, now):
    """Fresh same-day advice record or None."""
    try:
        advice = json.loads((Path(root) / 'vibe-advice.json').read_text())
        at = datetime.fromisoformat(advice['time'])
        if (at.tzinfo is None or at.astimezone(KST).date() != now.astimezone(KST).date()
                or not timedelta(0) <= now - at <= ADVICE_MAX_AGE or not isinstance(advice.get('decisions'), list)):
            return None
        return advice
    except (OSError, ValueError, KeyError, TypeError):
        return None


def executions(db):
    db.execute('CREATE TABLE IF NOT EXISTS advice_executions (advice_id TEXT NOT NULL, idx INTEGER NOT NULL, '
               'outcome TEXT NOT NULL, time TEXT NOT NULL, PRIMARY KEY (advice_id, idx))')
    return {(a, i) for a, i in db.execute('SELECT advice_id, idx FROM advice_executions')}


def mark(db, advice_id, idx, outcome):
    with db:
        db.execute('INSERT OR REPLACE INTO advice_executions VALUES (?,?,?,?)',
                   (advice_id, idx, outcome, datetime.now(KST).isoformat()))


def today_buys(db, day):
    rows = db.execute("SELECT symbol, qty, price FROM intents WHERE day=? AND side='BUY' AND id NOT IN "
                      "(SELECT order_id FROM pre_send_aborts)", (day,)).fetchall()
    return sum(q * p for _, q, p in rows), {s for s, _, _ in rows}


def realized_today(db, day):
    """Realized P&L of today's verified sells against the moving-average cost."""
    books, realized = {}, 0.0
    for stamp, symbol, side, qty, price in db.execute('SELECT time,symbol,side,quantity,price FROM fills ORDER BY rowid'):
        if (not isinstance(symbol, str) or not re.fullmatch(r'\d{6}', symbol) or side not in ('BUY', 'SELL')
                or type(qty) is not int or qty <= 0 or type(price) not in (int, float)
                or not math.isfinite(price) or price <= 0):
            raise ValueError('fill history unavailable')
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError('fill time unavailable')
        book = books.setdefault(symbol, [0, 0.0])
        if side == 'BUY':
            book[1] = (book[1] * book[0] + price * qty) / (book[0] + qty)
            book[0] += qty
        else:
            if qty > book[0]:
                raise ValueError('fill history unavailable')
            if at.astimezone(KST).strftime('%Y%m%d') == day:
                realized += (price - book[1]) * qty
            book[0] -= qty
    return realized


def daily_loss_reason(db, day, owned, rows, loss_limit):
    """Missing or non-finite position P&L cannot authorize additional exposure."""
    try:
        if type(loss_limit) not in (int, float) or not math.isfinite(loss_limit) or loss_limit <= 0:
            return 'daily_loss_unverified'
        pnl = []
        for symbol, qty in owned.items():
            row = rows.get(symbol)
            if (not isinstance(row, dict) or type(row.get('rsdl_qty')) is not int or row['rsdl_qty'] != qty
                    or type(row.get('eal_pls_amt')) not in (int, float)
                    or not math.isfinite(row['eal_pls_amt'])):
                return 'daily_loss_unverified'
            pnl.append(row['eal_pls_amt'])
        realized = realized_today(db, day)
        if not math.isfinite(realized) or not math.isfinite(realized + sum(pnl)):
            return 'daily_loss_unverified'
        return 'daily_loss_limit' if realized + sum(pnl) <= -loss_limit else None
    except (sqlite3.Error, TypeError, ValueError, KeyError, ArithmeticError):
        return 'daily_loss_unverified'


def buy_cooldown_reason(db, root, day, now):
    """Use the shared dashboard switch and round-trip cooldown for this engine's ledger."""
    try:
        from strategy_settings import read_settings
        from nh_paper_isolated import losing_streak
        settings, status = read_settings(Path(root).parent / 'shared-etf-paper')
        switch = settings.get('losing_streak_guard')
        if status == 'invalid' or type(switch) not in (int, float) or switch not in (0, 1):
            return 'losing_streak_unverified'
        return losing_streak(db, day, now) if switch else None
    except (OSError, sqlite3.Error, TypeError, ValueError, KeyError, ArithmeticError):
        return 'losing_streak_unverified'


def buy_loss_check(db, root, day):
    """Recheck the latest verified balance snapshot without a nested broker request."""
    try:
        state = json.loads((Path(root) / 'account-state.json').read_text())
        at = datetime.fromisoformat(state['time'])
        now = datetime.now(KST)
        holdings = state['holdings']
        if (at.tzinfo is None or now.strftime('%Y%m%d') != day
                or at.astimezone(KST).strftime('%Y%m%d') != day
                or not timedelta(0) <= now - at <= ACCOUNT_STATE_MAX_AGE
                or not isinstance(holdings, dict)):
            return False
        owned = owned_positions(db)
        return (set(holdings) == set(owned) and daily_loss_reason(db, day, owned, holdings, limits()['loss']) is None
                and buy_cooldown_reason(db, root, day, now) is None)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ArithmeticError):
        return False


def technical_buy_fresh(advice, symbol, now):
    """A new entry needs the symbol's same-session, recently completed technical bar."""
    try:
        if now.tzinfo is None or now.utcoffset() is None:
            return False
        now = now.astimezone(KST)
        started = datetime.fromisoformat(advice['time'])
        if started.tzinfo is None or started.utcoffset() is None or started.astimezone(KST).date() != now.date():
            return False
        item = advice['technical_basic'][symbol]
        if (not isinstance(item, dict) or item.get('status') not in (None, 'ok')
                or type(item.get('vote')) is not int or item['vote'] not in (0, 1)):
            return False
        raw = item['as_of']
        if isinstance(raw, str) and re.fullmatch(r'\d{2}:\d{2}', raw):
            at = datetime.strptime(started.astimezone(KST).strftime('%Y%m%d') + raw, '%Y%m%d%H:%M').replace(tzinfo=KST)
        else:
            at = datetime.fromisoformat(raw)
            if at.tzinfo is None or at.utcoffset() is None:
                return False
            at = at.astimezone(KST)
        started = started.astimezone(KST)
        # The bar is the last one completed before the run started (11/16: 2-4 minutes earlier); the ~50 s run and the
        # runner's 1-minute poll add up to 2 more, so freshness is judged at the decision's start, with a 7-minute ceiling.
        return (at.date() == now.date() and at <= started and started - at <= timedelta(minutes=TECHNICAL_START_AGE_MIN)
                and timedelta(minutes=1) <= now - at <= timedelta(minutes=TECHNICAL_MAX_AGE_MIN))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def model_buy_check(root, expected_advice, idx, symbol, decision=None, advice_id=None, *, require_technical=False):
    """Reject replaced, altered, expired or mismatched model decisions before sending."""
    try:
        actual = load_advice(root, datetime.now(KST))
        if (not isinstance(expected_advice, dict) or actual != expected_advice
                or (advice_id is not None and advice_id != (actual.get('id') or actual.get('time')))):
            return False
        decisions = actual['decisions']
        return (type(idx) is int and 0 <= idx < len(decisions) and isinstance(decisions[idx], dict)
                and decisions[idx].get('action') == 'BUY' and decisions[idx].get('symbol') == symbol
                and (decision is None or decisions[idx] == decision)
                and (not require_technical or technical_buy_fresh(actual, symbol, datetime.now(KST))))
    except (KeyError, TypeError, ValueError):
        return False


def decision_evidence(advice, idx):
    evidence = {'advice_id': None, 'decision_idx': idx, 'advice_started_at': None, 'decision_available_at': None}
    if not isinstance(advice, dict):
        return evidence
    evidence['advice_id'] = advice.get('id') or advice.get('time')
    evidence['advice_started_at'] = advice.get('time')
    try:
        available = datetime.fromisoformat(advice['available_at'])
        started = datetime.fromisoformat(advice['time'])
        if (available.tzinfo is not None and started.tzinfo is not None
                and started <= available <= datetime.now(KST)):
            evidence['decision_available_at'] = available.isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    return evidence


def quote_of(read, symbol):
    response = read.call_tool('get_stock_price', {'stock_code': symbol})
    if response.get('status') != 'ok':
        error = ValueError('quote unavailable')
        kind = response.get('error_type')
        error.quote_error_type = kind if isinstance(kind, str) and kind in {'TimeoutError', 'ConnectionError', 'OSError'} else 'unknown'
        raise error
    return fresh_quote(response.get('data', {}), symbol, datetime.now(KST))


def protective_quote(read, symbol):
    try:
        return quote_of(read, symbol)
    except ValueError as exc:
        if str(exc) != 'quote unavailable':
            raise
    time.sleep(1)
    return quote_of(read, symbol)


def add_better(db, symbol, held_context, ask, quote, confidence):
    """Why a vibe add may go (nh_paper_isolated.add_reason: confidence_up · cheaper_step · profit_step, 'rule_off' with
    the rule off), or None when it does not beat the last buy's score nor is a step under the average or in profit."""
    if held_context is None:
        return 'no_context'  # the held-entry check below decides
    import nh_paper_isolated
    from nh_paper_isolated import add_reason, day_iso, last_buy_confidence
    _, settings, avg, _ = held_context
    why = add_reason(avg, ask, quote.get('bidp'), confidence, last_buy_confidence(db, symbol, day_iso()),
                     Decimal(str(settings.get('add_step_pct', 0.7))) / 100)
    return why or (None if nh_paper_isolated.ADD_CONF_SIZING else 'rule_off')


def held_buy_check(db, root, symbol, quantity, avg, added, quote, advice_id, settings, bars=None):
    """Recheck a held position before a new signal or a price-step add."""
    from etf_universe import SHORT
    from nh_paper_isolated import averaging_down_blocked, fresh_series, scale_in_allowed, scale_in_projected_safe, scale_in_signal
    if not settings.get('scale_step_add_on' if bars is not None else 'scale_signal_add_on', 1):
        return False
    now = datetime.now(KST)
    advice = load_advice(root, now)
    if not advice or (advice.get('id') or advice.get('time')) != advice_id:
        return False
    signals = advice.get('signals')
    signal = signals.get(symbol) if isinstance(signals, dict) else None
    if (advice.get('grounded') is not True or advice.get('confirmed_by_previous_run') is not True
            or type(signal) not in (int, float) or not math.isfinite(signal) or signal < setting('vibe_buy_score')
            or not any(isinstance(row, dict) and row.get('action') == 'BUY' and row.get('symbol') == symbol
                       for row in advice['decisions'])
            or symbol in oversold_plans(root) or not scale_in_allowed(
                db, root, symbol, quantity, allow_profit_rebuy=bool(settings.get('scale_profit_rebuy_on', 1)))):
        return False
    shared = Path(root).parent / 'shared-etf-paper'
    view, index = market_view(shared), stop_symbol_index(shared, symbol)
    if not index or index not in (view or {}):
        return False
    bid, ask = quote.get('bidp'), quote.get('askp')
    if (type(bid) is not int or type(ask) is not int or min(bid, ask, quantity, added) <= 0
            or type(avg) not in (int, float) or not math.isfinite(avg) or avg <= 0):
        return False
    from engine_exits import defensive_plan
    plan = defensive_plan(settings)
    if not scale_in_projected_safe(avg, quantity, ask, added, bid, plan, view, index, False, symbol):
        return False
    if bars is not None:
        if not fresh_series(bars, now):
            return False
        mode = scale_in_signal(db, root, symbol, quantity, ask, [r['stck_prpr'] for r in bars[:2]], now, settings['add_step_pct'],
                               allow_profit_rebuy=bool(settings.get('scale_profit_rebuy_on', 1)), interval_min=settings.get('scale_interval_min', 5))
        short = symbol in {ETF7_SYMBOLS[name] for name in SHORT}
        if not mode or (mode == 'dip' and settings.get('averaging_down_guard')
                        and averaging_down_blocked(view, symbol, short, index)):
            return False
    return True


def model_position_check(db, root, symbol, expected_advice):
    """Model evidence belongs only to the exact verified position that the advice observed."""
    try:
        advice = load_advice(root, datetime.now(KST))
        if not advice or advice != expected_advice:
            return False
        holdings, fill_ids = advice.get('account_holdings'), advice.get('account_fill_ids')
        if not isinstance(holdings, dict) or not isinstance(fill_ids, dict):
            return False
        qty, fill_id = holdings.get(symbol), fill_ids.get(symbol)
        if (type(qty) not in (int, float) or not math.isfinite(qty) or qty <= 0 or qty != int(qty)
                or not isinstance(fill_id, str) or not fill_id):
            return False
        latest = db.execute('SELECT order_id FROM fills WHERE symbol=? ORDER BY rowid DESC LIMIT 1', (symbol,)).fetchone()
        return owned_positions(db).get(symbol) == qty and latest is not None and latest[0] == fill_id
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        return False


def model_sell_check(db, root, symbol, expected_advice, idx, decision):
    if not model_position_check(db, root, symbol, expected_advice):
        return False
    decisions = expected_advice['decisions']
    return type(idx) is int and 0 <= idx < len(decisions) and decisions[idx] == decision


def carry_position(db, root, symbol, quantity):
    """Identify a confirmed ordinary position without treating an absent oversold plan as proof."""
    from vibe_fast_exit_watch import _open_cycle, _sources
    opened = _open_cycle(db, symbol, quantity)
    if opened is None or not _sources(root, {symbol: opened[1]}):
        raise ValueError('carry_position_unavailable')
    return {'opened': opened[0].isoformat(), 'position_id': opened[2]}


def carry_state(root):
    try:
        data = json.loads((Path(root) / VIBE_CARRY_FILE).read_text())
        if not isinstance(data, dict) or any(not isinstance(key, str) or not isinstance(value, dict)
                                             for key, value in data.items()):
            raise ValueError('carry_state_invalid')
        return data
    except FileNotFoundError:
        return {}


def save_carry(root, symbol, position, now):
    """Persist the verified generation before treating the position as an existing swing."""
    from config_store import write_json
    path = Path(root) / VIBE_CARRY_FILE
    state = carry_state(root)
    previous = state.get(symbol) or {}
    first = previous.get('first_approved_on') if previous.get('position_id') == position['position_id'] else None
    state[symbol] = {**position, 'first_approved_on': first or now.date().isoformat(), 'approved_on': now.date().isoformat()}
    write_json(path, state)


def carry_model_supports(advice, symbol):
    if not isinstance(advice, dict):
        return False
    signal = (advice.get('signals') or {}).get(symbol) if isinstance(advice.get('signals'), dict) else None
    decisions = advice.get('decisions')
    return (advice.get('grounded') is True and advice.get('confirmed_by_previous_run') is True
            and isinstance(decisions, list)
            and not any(isinstance(row, dict) and row.get('symbol') == symbol and row.get('action') == 'SELL'
                        for row in decisions)
            and type(signal) in (int, float) and math.isfinite(signal) and signal >= 0.3)


def carry_reason(*, view, index, short, bid, cost, budget_left, stats, advice, model_ok,
                 first_target_filled, carried, opened, now, settings):
    """Vibe's evidence-based carry checks; no other engine's holding opinion is substituted."""
    from nh_paper_isolated import next_session_open, sessions_held
    try:
        item = (view or {}).get(index) or {}
        regime, tone = item.get('regime'), item.get('today_tone')
        stats = stats or {}
        if not isinstance(stats.get('today'), dict):
            return 'carry_evidence_unavailable'
        price = Decimal(str(bid))
        supported = model_ok and carry_model_supports(advice, opened['symbol'])
        if carried:
            if sessions_held(datetime.fromisoformat(opened['opened']).astimezone(KST).date(), now.date()) + 1 > int(settings['swing_max_days']):
                return 'swing_days_exceeded'
            if not ((regime == '하락장' and tone != '강세') if short else (regime == '상승장' and tone != '약세')):
                return 'regime_not_rising'
        else:
            if (regime, tone) != (('하락장', '약세') if short else ('상승장', '강세')):
                return 'regime_not_rising'
            rise, fall = (list(item.get('advancers_decliners') or []) + [None, None])[:2]
            if not (all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (rise, fall))
                    and (fall if short else rise) >= 2 * (rise if short else fall)):
                return 'breadth_not_broad'
            flows = {**(item.get('investor_net_100m_krw') or {}),
                     '프로그램': (item.get('program_net_100m_krw') or {}).get('전체')}
            sign = -1 if short else 1
            if not all(type(flows.get(k)) in (int, float) and math.isfinite(flows[k]) and sign * flows[k] > 0
                       for k in ('외국인', '기관', '프로그램')):
                return 'flows_not_supporting'
            if not first_target_filled:
                return 'not_first_target'
            if not (type(stats.get('volume_ratio')) in (int, float) and math.isfinite(stats['volume_ratio'])
                    and stats['volume_ratio'] >= 1.5):
                return 'volume_not_high'
            bar = stats.get('today') or {}
            high, low, opn = (Decimal(str(bar[k])) for k in ('high', 'low', 'open'))
            if not (high > low > 0 and low <= opn <= high and low <= price <= high) or price <= opn or (price - low) / (high - low) < Decimal('0.8'):
                return 'weak_close'
        ma5, ma20 = (Decimal(str(stats[k])) for k in ('ma5', 'ma20'))
        if not price > ma5 > ma20 > 0:
            return 'not_uptrend'
        if not supported:
            return 'vibe_not_supportive'
        if not (Decimal(str(cost)) <= Decimal(str(budget_left))):
            return 'overnight_limit'
        return None if next_session_open(now.date()) else 'market_closed_next_day'
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        return 'carry_evidence_unavailable'


def place(db, broker, account, root, day, symbol, side, qty, quote, price, allowed, cycle_id, source='vibe-trading',
          *, sell_trigger=None, signal_check_extra=None, decision_evidence=None):
    """Submit one order on the protective (quote-deadline) path; returns True when the broker acknowledged."""
    book_marker = db.execute('SELECT COALESCE(MAX(rowid), 0) FROM fills').fetchone()[0] if side == 'BUY' else None
    if side == 'SELL':
        response = broker.call_tool('call_api', {'operationId': 'krstockInquirySellableQuantity', 'input': {
            'act_no': account, 'iem_cd': symbol, 'cfd_lon_cd': '00'}})
        data = response.get('data', {}) if isinstance(response, dict) else {}
        available = data.get('Output_0', {}).get('sll_pbl_qty') if isinstance(data, dict) else None
        if (response.get('status') != 'ok' or data.get('rsp_cd') not in ('00000', 'XA102')
                or data.get('Output_0', {}).get('iem_cd') != symbol or type(available) not in (int, float) or available < 1):
            raise ValueError('sellable quantity unavailable')
        qty = min(qty, int(available))
    if type(price) is not int or price <= 0:
        raise ValueError('invalid order price')
    ensure_trading_open(root, day)
    deadline_ms = etf7_protective_deadline(quote, symbol, datetime.now(KST))
    if deadline_ms is None:
        return False

    def current():
        try:
            ensure_trading_open(root, day)
            return (etf7_protective_deadline(quote, symbol, datetime.now(KST)) == deadline_ms
                    and (side != 'BUY' or (db.execute('SELECT COALESCE(MAX(rowid), 0) FROM fills').fetchone()[0] == book_marker
                                           and buy_loss_check(db, root, day)))
                    and (signal_check_extra is None or signal_check_extra()))
        except (OSError, TypeError, ValueError, sqlite3.Error):
            return False
    try:
        submit(db, broker, account, day, symbol, side, qty, price, cycle_id=cycle_id, etf7=True,
               signal_check=current, deadline_ms=deadline_ms, allowed=allowed, sell_trigger=sell_trigger)
    except PaperOrderGuardRefused as exc:
        if 'reconciliation' in str(exc):
            raise ValueError('order guard requires reconciliation') from exc
        journal_event(root, 'v2_execution_gate', permitted=False, reason='order_guard_refused', cycle_id=cycle_id)
        return False
    except PaperOrderNotSent:
        return False
    journal_event(root, 'submitted' if side == 'BUY' else 'sell_submitted', symbol=symbol, limit=price, qty=qty,
                  source=source, cycle_id=cycle_id, **(decision_evidence or {}),
                  **(entry_context(quote, price) if side == 'BUY' else {}))  # range place and NH warnings, observation only
    return True


def write_account_state(root, balance, owned):
    """account-state.json: what the vibe advisor reads as this book's holdings (it refuses a state over 3 minutes old)."""
    rows = {r.get('iem_cd'): r for r in balance['Output_1'] if isinstance(r, dict) and r.get('rsdl_qty')}
    state = {'time': datetime.now(KST).isoformat(), 'cash': balance.get('Output_0', {}).get('orr_pbl_amt1'),
             'holdings': {c: {k: rows[c].get(k) for k in ('iem_nm', 'rsdl_qty', 'phs_pr', 'now_pr', 'eal_pls_amt', 'pft_rt')}
                          for c in owned if c in rows}}
    from config_store import write_json
    write_json(Path(root) / 'account-state.json', state, indent=None)  # atomic: the advisor may read it at any moment


def observe_state(db, broker, account, root):
    """From the market open to the first order minute (no orders): keep account-state.json fresh so the advisor's first runs are not skipped
    (2026-11-17 audit: 09:00/09:05 had no state and 09:10 lost a one-second race)."""
    try:
        balance = paper_pages(broker, 'krstockInquiryBalance', {'act_no': account, **BALANCE}, 'Output_1')
        owned = owned_positions(db)
        verify_positions(balance['Output_1'], owned, etf7=True)
        write_account_state(root, balance, owned)
    except Exception:
        pass  # the first cycle at 09:10 reads and checks it again


@buy_settings_cycle
def cycle(db, read, broker, account, root, now):
    day = now.astimezone(KST).strftime('%Y%m%d')
    pending = db.execute("SELECT id,day,symbol,side,qty,price,status,order_number,created FROM intents WHERE status!='terminal' ORDER BY rowid").fetchall()
    if len(pending) > 1:
        raise ValueError('multiple unresolved submissions')
    if pending:
        return 'reconciled' if reconcile(db, broker, account, pending[0]) else 'pending'
    history = paper_pages(broker, 'krstockInquiryDailyOrderExecution', {'act_no': account, 'orr_dt': day,
                          'orr_mkt_cd': '00', 'ost_cns_dit': '0'}, 'Output_0')
    if any(not isinstance(r, dict) or type(r.get('ny_cns_qty')) is not int or r['ny_cns_qty'] != 0 for r in history['Output_0']):
        raise ValueError('external or ambiguous paper orders')
    balance = paper_pages(broker, 'krstockInquiryBalance', {'act_no': account, **BALANCE}, 'Output_1')
    owned = owned_positions(db)
    verify_positions(balance['Output_1'], owned, etf7=True)
    settings = scale_settings(root)
    rows = {r.get('iem_cd'): r for r in balance['Output_1'] if isinstance(r, dict) and r.get('rsdl_qty')}
    cash = balance.get('Output_0', {}).get('orr_pbl_amt1')
    write_account_state(root, balance, owned)
    allowed = set(ETF7_SYMBOLS.values()) | set(owned)

    # 1. stop loss, take profit and the 14:40 flatten always run first and do not need advice
    flatten = datetime.now(KST).strftime('%H:%M') >= FLATTEN_AT
    rebounds = {c: v for c, v in oversold_plans(root).items() if c in owned}
    protective_wait = False
    carry_candidates = []
    # Existing oversold carry rules remain independent, but their exposure consumes the general carry budget.
    overnight_left = Decimal(str(settings.get('overnight_max_manwon', 0))) * 10_000
    if flatten:
        for code, plan in rebounds.items():
            try:
                already_carries = may_carry(plan, datetime.now(KST).date())
            except (TypeError, ValueError, ArithmeticError):
                overnight_left = Decimal(0)
                continue
            if already_carries:
                amount = (rows.get(code) or {}).get('phs_pr')
                if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
                    overnight_left = Decimal(0)
                else:
                    overnight_left -= Decimal(str(amount)) * owned[code]
    shared_root = Path(root).parent / 'shared-etf-paper'
    view = market_view(shared_root)
    for symbol, quantity in sorted(owned.items()):
        avg = (rows.get(symbol) or {}).get('phs_pr')
        if type(avg) not in (int, float) or avg <= 0:
            raise ValueError('broker average price unavailable')
        try:
            quote = protective_quote(read, symbol)
            bid = quote.get('bidp')
            if type(bid) is not int or bid <= 0:
                raise ValueError('invalid bid')
            record_protective_quote(root, symbol, quote, datetime.now(KST))
            plan = rebounds.get(symbol)
            breakeven = False
            if plan:  # 낙폭과대 반등: its own target and stop, and it may wait past the flatten for its sessions
                stop, take = bid <= plan['stop_price'], bid >= plan['target_price']
                late = flatten and not may_carry(plan, datetime.now(KST).date())
                if plan.get('variant') == 'ipo' and not late:  # new-listing slot: ipo_time_stop_min (user 2026-11-17)
                    from nh_paper_isolated import ipo_time_stop
                    ipo_timed_out = ipo_time_stop({'profile': 'ipo', 'opened': plan.get('opened')}, datetime.now(KST)) is not None
                    late = ipo_timed_out
                half_qty = 0
                if not (stop or take or late) and plan.get('variant') == 'v2' and type(plan.get('half_price')) in (int, float):
                    from nh_paper_isolated import position_since_open
                    position = position_since_open(db, symbol, quantity)
                    half_target = position['bought'] // 2
                    half_qty = min(quantity, max(0, half_target - position['sold']))
                    half_done = half_target > 0 and half_qty == 0
                    if bool(plan.get('half_done')) != half_done:
                        plan = {**plan, 'half_done': half_done}
                        save_oversold_plans(root, {**oversold_plans(root), symbol: plan})
                if (not (stop or take or late) and half_qty > 0 and bid >= plan['half_price']):
                    cycle_id = uuid.uuid4().hex
                    journal_event(root, 'v2_risk_exit', symbol=symbol, reason='take_profit_half', source='vibe-trading', cycle_id=cycle_id)
                    if place(db, broker, account, root, day, symbol, 'SELL', half_qty, quote, bid, allowed, cycle_id, sell_trigger='take_profit'):
                        return 'sell_protective_wait' if protective_wait else 'sell'
                    protective_wait = True
                    continue
            else:
                import engine_exits  # vibe: the current settings' defensive plan, regime-tightened (engine_exits)
                lv = engine_exits.levels(engine_exits.defensive_plan(settings), view, stop_symbol_index(shared_root, symbol), symbol)
                stop, take = engine_exits.stop_hit(avg, bid, lv), engine_exits.target_hit(avg, bid, lv, 'all')
                late = flatten
                if not (stop or take or late) and quantity >= 2 and engine_exits.target_hit(avg, bid, lv, 'half'):
                    from nh_paper_isolated import position_since_open, take_profit_target, verified_take_profit_sold
                    plan = position_since_open(db, symbol, quantity)
                    profit_sold = verified_take_profit_sold(db, symbol, quantity)
                    half_qty = min(quantity, max(0, take_profit_target(plan) - profit_sold)) if profit_sold is not None else 0
                    if profit_sold is None:
                        journal_event(root, 'v2_execution_gate', permitted=False, reason='take_profit_history_unavailable',
                                      symbol=symbol, cycle_id=uuid.uuid4().hex)
                    if half_qty:
                        cycle_id = uuid.uuid4().hex
                        journal_event(root, 'v2_risk_exit', symbol=symbol, reason='take_profit_half', source='vibe-trading', cycle_id=cycle_id)
                        if place(db, broker, account, root, day, symbol, 'SELL', half_qty, quote, bid, allowed, cycle_id, sell_trigger='take_profit'):
                            return 'sell_protective_wait' if protective_wait else 'sell'
                        protective_wait = True
                        continue
                if not (stop or take) and Decimal(str(bid)) <= Decimal(str(avg)):
                    # like the Jev runner (user approval 2026-11-17, replay 11/16 100790): after a half take-profit the
                    # rest leaves at the average price while the latest fill is that sale
                    from nh_paper_isolated import breakeven_protected, position_since_open
                    try:
                        breakeven = breakeven_protected(position_since_open(db, symbol, quantity), None)
                    except ValueError:
                        journal_event(root, 'v2_execution_gate', permitted=False, reason='split_history_unavailable',
                                      symbol=symbol, cycle_id=uuid.uuid4().hex)
                if not (stop or take or late or breakeven):
                    import position_sizing
                    import etf_universe
                    from strategy_settings import read_settings as read_strategy
                    trim = position_sizing.regime_trim(root, db, symbol, quantity, view, stop_symbol_index(shared_root, symbol),
                                                       symbol in {etf_universe.SYMBOLS[n] for n in etf_universe.SHORT},
                                                       read_strategy(shared_root)[0])
                    if trim:  # the market turned against it after the buy: half, once (user 2026-11-17)
                        cycle_id = uuid.uuid4().hex
                        journal_event(root, 'v2_risk_exit', symbol=symbol, reason='regime_reversal', source='vibe-trading', cycle_id=cycle_id)
                        if place(db, broker, account, root, day, symbol, 'SELL', trim, quote, bid, allowed, cycle_id, sell_trigger='risk'):
                            position_sizing.mark_trimmed(root, symbol)
                            return 'sell_protective_wait' if protective_wait else 'sell'
                        protective_wait = True
                        continue
                if not (stop or take or late or breakeven):
                    jev_exit = jev_event_exit(root, symbol, quantity, datetime.now(KST))
                    if jev_exit:
                        cycle_id = uuid.uuid4().hex
                        journal_event(root, 'v2_risk_exit', symbol=symbol, reason='jev_event_sell', source='jev_fast',
                                      cycle_id=cycle_id, **{k: v for k, v in jev_exit.items() if k in ('jev_event_completed_at', 'choice', 'confidence')})
                        if place(db, broker, account, root, day, symbol, 'SELL', quantity, quote, bid, allowed, cycle_id,
                                 source='jev_fast', sell_trigger='model'):
                            return 'sell_protective_wait' if protective_wait else 'sell'
                        protective_wait = True
                        continue
                if late and not (stop or take or breakeven):
                    # All other holdings' immediate protection is checked before researching any carry candidate.
                    carry_candidates.append((symbol, quantity, avg, bid, quote))
                    continue
            if not (stop or take or late or breakeven):
                continue
            cycle_id = uuid.uuid4().hex
            timed_out = bool(plan) and plan.get('variant') == 'ipo' and late and not flatten
            journal_event(root, 'v2_risk_exit', symbol=symbol, source='vibe-trading', cycle_id=cycle_id,
                          reason='stop_loss' if stop else 'take_profit' if take else 'breakeven_stop' if breakeven
                          else 'ipo_time_stop' if timed_out else 'session_flatten')
            if stop and plan and plan.get('variant') == 'v2':  # 선별 v2: buy it again at a held low (v2_reentry, user 2026-11-17)
                import v2_reentry
                v2_reentry.stopped(root, symbol, opened=plan.get('first_opened') or plan['opened'],
                                   hold_days=plan.get('first_hold_days') or plan['hold_days'], qty=quantity,
                                   reentries=int(plan.get('reentries') or 0), limit=settings.get('oversold_v2_reentry_max', 0),
                                   facts={'drop_pct': plan.get('drop_pct')})
            trigger = 'risk' if stop or late or breakeven else 'take_profit'
            if place(db, broker, account, root, day, symbol, 'SELL', quantity, quote, bid, allowed, cycle_id, sell_trigger=trigger):
                return 'sell_protective_wait' if protective_wait else 'sell'
        except ValueError as exc:
            if 'reconciliation' in str(exc) or 'uncertain' in str(exc):
                raise  # an order may exist; stop and reconcile
            detail = {'detail': str(exc)} if re.fullmatch(r'[a-z ]{3,60}', str(exc)) else {}  # internal messages only
            if hasattr(exc, 'quote_error_type'):
                detail['quote_error_type'] = exc.quote_error_type
            journal_event(root, 'v2_risk_exit_error', symbol=symbol, reason=type(exc).__name__, cycle_id=uuid.uuid4().hex, **detail)
        protective_wait = True  # one holding's failed check must not stop the others, nor allow new buys
    if carry_candidates:
        from etf_universe import SHORT
        from nh_paper_isolated import daily_stats, take_profit_progress
        carry_limits = limits()
        profits = [(rows.get(code) or {}).get('eal_pls_amt') for code in owned]
        try:
            realized_carry = realized_today(db, day)
            loss_halt_carry = (not math.isfinite(realized_carry)
                               or not all(type(value) in (int, float) and math.isfinite(value) for value in profits)
                               or realized_carry + sum(profits) <= -carry_limits['loss'])
        except (sqlite3.Error, ValueError, TypeError, ArithmeticError):
            loss_halt_carry = True
        advice = load_advice(root, datetime.now(KST))
        try:
            saved = carry_state(root)
        except (OSError, ValueError, TypeError):
            saved = None
        for symbol, quantity, avg, bid, quote in carry_candidates:
            now_carry = datetime.now(KST)
            cost = Decimal(str(avg)) * quantity
            budget_before = overnight_left
            position, verified, carried = None, None, False
            try:
                if saved is None:
                    raise ValueError('carry_state_invalid')
                if loss_halt_carry:
                    raise ValueError('daily_loss_halt')
                position = carry_position(db, root, symbol, quantity)
                position['symbol'] = symbol
                previous = saved.get(symbol) if isinstance(saved.get(symbol), dict) else {}
                first = previous.get('first_approved_on')
                carried = (previous.get('position_id') == position['position_id'] and isinstance(first, str)
                           and datetime.fromisoformat(first).date() < now_carry.date())
                if datetime.fromisoformat(position['opened']).astimezone(KST).date() < now_carry.date() and not carried:
                    raise ValueError('carry_history_unavailable')
                verified = take_profit_progress(db, symbol, quantity)
                model_ok = bool(advice and model_position_check(db, root, symbol, advice))
                if not verified:
                    reason = 'not_first_target'
                elif not model_ok or not carry_model_supports(advice, symbol):
                    reason = 'vibe_not_supportive'
                else:
                    stats = daily_stats(read, symbol, now_carry)
                    from strategy_settings import read_settings
                    current_settings, settings_status = read_settings(shared_root)
                    if (settings_status == 'invalid' or current_settings != settings
                            or limits() != carry_limits
                            or not model_position_check(db, root, symbol, advice)
                            or owned_positions(db).get(symbol) != quantity or not take_profit_progress(db, symbol, quantity)
                            or etf7_protective_deadline(quote, symbol, datetime.now(KST)) is None
                            or market_view(shared_root) != view):
                        raise ValueError('carry_evidence_changed')
                    now_carry = datetime.now(KST)
                    reason = carry_reason(view=view, index=stop_symbol_index(shared_root, symbol),
                                          short=symbol in {ETF7_SYMBOLS[n] for n in SHORT}, bid=bid,
                                          cost=cost, budget_left=overnight_left, stats=stats, advice=advice,
                                          model_ok=model_ok, first_target_filled=verified, carried=carried,
                                          opened=position, now=now_carry, settings=settings)
                if reason is None:
                    # A failed write cannot silently make an untracked overnight position look approved.
                    save_carry(root, symbol, position, now_carry)
                    overnight_left -= cost
            except ValueError as exc:
                reason = str(exc) if str(exc) in ('carry_position_unavailable', 'carry_state_invalid',
                                                   'carry_history_unavailable', 'carry_evidence_changed', 'daily_loss_halt') else 'carry_evidence_unavailable'
            except (OSError, TypeError, KeyError, AttributeError, sqlite3.Error, ArithmeticError):
                reason = 'carry_evidence_unavailable'
            cycle_id = uuid.uuid4().hex
            try:
                journal_event(root, 'overnight_hold', symbol=symbol, permitted=reason is None,
                              reason=reason or ('swing_continued' if carried else 'vibe_carry_supported'),
                              source='vibe-trading', cycle_id=cycle_id,
                              position_id=(position or {}).get('position_id'), confirmed_first_target=verified,
                              advice_time=advice.get('time') if isinstance(advice, dict) else None,
                              vibe_signal=(advice.get('signals') or {}).get(symbol) if isinstance(advice, dict) and isinstance(advice.get('signals'), dict) else None,
                              market_time=view.get('as_of') if isinstance(view, dict) else None,
                              exposure_krw=float(cost), budget_left_krw=float(budget_before))
            except (OSError, ValueError, TypeError):
                pass
            if reason is not None:
                try:
                    journal_event(root, 'v2_risk_exit', symbol=symbol, reason='session_flatten',
                                  source='vibe-trading', cycle_id=cycle_id)
                except (OSError, ValueError, TypeError):
                    pass
                if place(db, broker, account, root, day, symbol, 'SELL', quantity, quote, bid, allowed,
                         cycle_id, sell_trigger='risk'):
                    return 'sell_protective_wait' if protective_wait else 'sell'
                protective_wait = True
    if protective_wait:
        return 'protective_wait'
    if flatten and not v2_window(settings):
        return 'flattened'
    current = oversold_plans(root)
    if {c: v for c, v in current.items() if c in owned} != current:  # sold ones leave the file (a half sale keeps its flag)
        save_oversold_plans(root, {c: v for c, v in current.items() if c in owned})
    if account == VIRTUAL_ACCOUNT:
        from engine_roles import main_engine
        if main_engine(Path(root).parent) == 'vibe':  # vibe trades the shared paper account; the virtual book only exits
            return 'secondary_exits_only'

    # 2. 선별 v2 (its own window), then the vibe-trading decisions, each at most once, sells before buys, one order per cycle
    advice = load_advice(root, datetime.now(KST))
    spent, symbols_today = today_buys(db, day)
    cap = limits()
    loss_halt = daily_loss_reason(db, day, owned, rows, cap['loss']) or buy_cooldown_reason(db, root, day, datetime.now(KST))
    if settings.get('oversold_v2_on') and not loss_halt:
        status = oversold_v2_buy(db, read, broker, account, root, day, settings, cap, owned, cash, spent, symbols_today, allowed, advice)
        if status:
            return status
    if flatten:
        return 'flattened'
    if not loss_halt and datetime.now(KST).strftime('%H:%M') < ENTRY_CUTOFF:
        status = ipo_buy(db, read, broker, account, root, day, settings, cap, owned, cash, spent, symbols_today, allowed)
        if status:
            return status
    fast = lambda: jev_fast_buy(db, read, broker, account, root, day, advice, settings, cap, owned, cash, spent,
                                symbols_today, allowed, loss_halt)
    if advice is None:
        return fast() or 'wait'
    done = executions(db)
    advice_id = advice.get('id') or advice['time']
    allowed |= {c for c in advice.get('universe', []) if isinstance(c, str) and re.fullmatch(r'\d{6}', c)}
    order = sorted(enumerate(advice['decisions']), key=lambda item: item[1].get('action') != 'SELL')
    if settings.get('oversold_on') and not loss_halt and datetime.now(KST).strftime('%H:%M') < ENTRY_CUTOFF:
        status = oversold_buy(db, read, broker, account, root, day, advice, settings, cap, owned, cash, spent, symbols_today, allowed)
        if status:
            return status
    for idx, decision in order:
        target_id = advice_id
        symbol = decision.get('symbol')
        held_buy = decision.get('action') == 'BUY' and symbol in owned
        bars = None
        if (advice_id, idx) in done:
            if not held_buy:
                continue
            last = db.execute('SELECT order_id FROM fills WHERE symbol=? ORDER BY rowid DESC LIMIT 1', (symbol,)).fetchone()
            if not last:
                continue
            target_id = f'{advice_id}:scale:{last[0]}'
            if (target_id, idx) in done:
                continue
            from nh_etf_paper_runner import valid_bars
            from nh_paper_isolated import minute_bars
            try:
                response = minute_bars(read, symbol)
                bars = valid_bars(response.get('data', {}), symbol, datetime.now(KST)) if response.get('status') == 'ok' else None
            except (ValueError, TypeError, KeyError):
                bars = None
            if not bars:
                continue
        if held_buy:
            requested = decision.get('notional')
            if type(requested) is int and requested > 0:
                decision = {**decision, 'notional': min(cap['tranche'], requested)}
        try:
            status = execute(db, read, broker, account, root, day, target_id, idx, decision, allowed, owned, cash,
                             spent, symbols_today, loss_halt,
                             held_context=(advice_id, settings, (rows.get(symbol) or {}).get('phs_pr'), bars) if held_buy else None,
                             model_sell_advice=advice if decision.get('action') == 'SELL' else None,
                             model_buy_advice=advice if decision.get('action') == 'BUY' else None)
        except ValueError as exc:
            if 'reconciliation' in str(exc) or 'uncertain' in str(exc):
                raise
            mark(db, advice_id, idx, 'error:' + type(exc).__name__)
            journal_event(root, 'v2_execution_gate', permitted=False, reason='decision_error', cycle_id=uuid.uuid4().hex)
            continue
        if status:
            return status
    return fast() or 'wait'


JEV_FAST_MAX_AGE = timedelta(seconds=120)  # a 1-minute Jev result older than this is not acted on


def latest_jev_pick(nhplug, now):
    """The latest 1-minute Jev candidate the Jev runner recorded (no new Jev call): (time, code, confidence), or None
    when the latest one is WAIT, unreadable or older than JEV_FAST_MAX_AGE."""
    from engine_roles import ledger_root
    try:
        with (ledger_root(nhplug, 'jev') / 'events.jsonl').open('rb') as stream:
            start = max(0, stream.seek(0, 2) - 600_000)
            stream.seek(start)
            lines = stream.read().splitlines()[1 if start else 0:]
    except (OSError, TypeError):
        return None
    for line in reversed(lines):
        if b'"jev_shadow"' not in line:
            continue
        try:
            event = json.loads(line)
            candidate = (event.get('jev') or {}).get('candidate') or {}
            at = datetime.fromisoformat(event['time'])
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        code = ETF7_SYMBOLS.get(candidate.get('choice'), candidate.get('choice'))
        confidence = candidate.get('confidence')
        if (not isinstance(code, str) or not re.fullmatch(r'[0-9][0-9A-Z]{5}', code) or type(confidence) not in (int, float)
                or not math.isfinite(confidence) or not timedelta(0) <= now - at <= JEV_FAST_MAX_AGE):
            return None
        return event['time'], code, float(confidence)
    return None


def sizing_factor(root, symbol, held_cost, cap):
    """The shared position sizing for vibe's book too (holdings and market direction, user 2026-11-17)."""
    import position_sizing
    from etf_universe import SHORT, SYMBOLS
    from nh_paper_isolated import market_view, stop_symbol_index
    from strategy_settings import read_settings
    shared = Path(root).parent / 'shared-etf-paper'
    return position_sizing.buy_factor(read_settings(shared)[0], market_view(shared), stop_symbol_index(shared, symbol),
                                      symbol in {SYMBOLS[n] for n in SHORT}, held_cost, cap)[0]


def jev_fast_buy(db, read, broker, account, root, day, advice, settings, cap, owned, cash, spent, symbols_today, allowed, loss_halt):
    """User decision 2026-11-17: between vibe's 5-minute runs, the vibe book also buys Jev's latest 1-minute pick
    (recorded by the Jev runner, no extra call) at the strength-route size, when Jev's confidence meets the Jev entry
    threshold, vibe's latest signal for it is not negative, technical-basic is not short and the chase filter passes.
    Each Jev pick is judged once; the outcome is kept in jev_fast and the order is tagged source='jev_fast'."""
    import vibe_advisor
    now = datetime.now(KST)
    pick = latest_jev_pick(Path(root).parent, now)
    if pick is None:
        return None
    at, code, confidence = pick
    key = f'{at}|{code}'
    db.execute('CREATE TABLE IF NOT EXISTS jev_fast (key TEXT PRIMARY KEY, symbol TEXT NOT NULL, outcome TEXT NOT NULL, time TEXT NOT NULL)')
    if db.execute('SELECT 1 FROM jev_fast WHERE key=?', (key,)).fetchone():
        return None
    signal = (advice or {}).get('signals') if isinstance(advice, dict) else None
    signal = signal.get(code) if isinstance(signal, dict) else None
    threshold = settings.get('entry_confidence', 0.5)
    reason, votes, quote, qty, news = None, {}, None, 0, None
    if now.strftime('%H:%M') >= ENTRY_CUTOFF:
        reason = 'entry_cutoff'
    elif loss_halt:
        reason = loss_halt if isinstance(loss_halt, str) else 'daily_loss_limit'
    elif code in owned:
        reason = 'already_held'  # adds stay with vibe's own split rules
    elif type(threshold) not in (int, float) or confidence < threshold:
        reason = 'low_confidence'
    elif type(signal) in (int, float) and signal < 0:
        reason = 'vibe_disagrees'
    elif vibe_advisor.OPPOSITE.get(code, set()) & set(owned):
        reason = 'opposite_holding'
    elif code not in symbols_today and len(symbols_today) >= cap['new_symbols']:
        reason = 'daily_symbol_limit'
    else:
        # User decision 2026-11-17: like the Jev main entry, no fresh headlines (etf_news, shared cache) means no buy.
        try:
            from etf_news import headlines7
            news = headlines7(now)
        except (OSError, ValueError, SyntaxError):  # ET.ParseError is a SyntaxError
            news = None
    if reason is None and news is None:
        reason = 'news_unavailable'
    elif reason is None:
        votes = vibe_advisor.technical_votes(read, [code], now).get(code) or {}
        if votes.get('vote') not in (0, 1) or type(votes.get('vote')) is not int:
            reason = 'technical_short_or_unavailable'
        else:
            reason = vibe_advisor.chase_reason(votes)  # an unmeasured place also blocks this fast path
    if reason is None:
        quote = quote_of(read, code)
        ask = quote.get('askp')
        if type(ask) is not int or ask <= 0 or type(cash) not in (int, float) or not math.isfinite(cash):
            reason = 'quote_or_cash_unavailable'
        else:
            qty = min(vibe_advisor.STRENGTH_KRW // ask, int(cash // ask), cap['order'] // ask, quantity_room(owned), cap['symbol'] // ask)
            qty = int(qty * sizing_factor(root, code, 0, cap['symbol']))
            if qty < 1:
                reason = 'quantity_or_cash_limit'
            elif spent + qty * ask > cap['daily_buy']:
                reason = 'daily_buy_limit'
    cycle_id = uuid.uuid4().hex
    with db:
        db.execute('INSERT INTO jev_fast VALUES (?,?,?,?)', (key, code, reason or 'sending', now.isoformat()))
    evidence = {'jev_time': at, 'jev_confidence': confidence, 'vibe_signal': signal,
                'vs_vwap_pct': votes.get('vs_vwap_pct'), 'range_position_pct': votes.get('range_position_pct'),
                **({'news': news} if news else {})}
    if reason is not None:
        journal_event(root, 'v2_entry_gate', choice=code, source='jev_fast', permitted=False, reason=reason, cycle_id=cycle_id, **evidence)
        return None
    journal_event(root, 'v2_entry_gate', choice=code, source='jev_fast', permitted=True, reason='entry_conditions_met', cycle_id=cycle_id, **evidence)
    still_latest = lambda: latest_jev_pick(Path(root).parent, datetime.now(KST)) == pick
    sent = place(db, broker, account, root, day, code, 'BUY', qty, quote, quote['askp'], set(allowed) | {code}, cycle_id,
                 source='jev_fast', signal_check_extra=still_latest, decision_evidence=evidence)
    with db:
        db.execute('UPDATE jev_fast SET outcome=? WHERE key=?', ('buy_submitted' if sent else 'not_sent', key))
    return 'buy' if sent else None


def jev_event_exit(root, symbol, quantity, now):
    """User decision 2026-11-17: a fresh 1-minute event-Jev SELL on this exact vibe holding (same position, quantity
    and latest fill, re-checked against the live book; SELL at or above the shared sell confidence) sells it without
    waiting for vibe's next run. Returns the model evidence, or None."""
    from vibe_event_advisor import current_position
    path = Path(root).parent / 'vibe-event-analysis' / 'events.sqlite3'  # = vibe_event_advisor.EVENT_ROOT for the vibe book
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2) as db:
            row = db.execute("SELECT result FROM attempts WHERE day=? AND symbol=? AND status='completed' ORDER BY time DESC LIMIT 1",
                             (now.date().isoformat(), symbol)).fetchone()
        result = json.loads(row[0]) if row else None
        analysis, event = (result or {}).get('analysis') or {}, (result or {}).get('event') or {}
        completed = datetime.fromisoformat(result['completed_at']) if result else None
        if (not result or analysis.get('stance') != 'exit' or analysis.get('status') != 'ok' or event.get('qty') != quantity
                or not timedelta(0) <= now - completed <= JEV_FAST_MAX_AGE or not current_position(root, event, now)):
            return None
        return {'jev_event_completed_at': result['completed_at'], **(result.get('model_evidence') or {})}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return None



def oversold_plans(root):
    try:
        data = json.loads((Path(root) / OVERSOLD_PLANS).read_text())
        return {c: v for c, v in data.items() if isinstance(v, dict) and type(v.get('target_price')) in (int, float)
                and type(v.get('stop_price')) in (int, float)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_oversold_plans(root, plans):
    import config_store
    config_store.write_json(Path(root) / OVERSOLD_PLANS, plans, indent=None)


def may_carry(plan, today):
    """A rebound may stay past the flatten for its sessions (the Jev runner's oversold_may_carry rule: 선별 v2 counts
    KRX sessions through weekends and holidays, the intraday rebound does not cross them)."""
    from nh_paper_isolated import next_session_open, sessions_held
    try:
        opened = datetime.fromisoformat(plan['opened']).astimezone(KST).date()
    except (KeyError, TypeError, ValueError):
        return False
    within = bool(plan.get('hold_days')) and sessions_held(opened, today) < int(plan['hold_days'])
    return within if plan.get('variant') == "v2" else within and next_session_open(today)


def oversold_score(advice, code):
    """vibe-trading's latest raw score for `code` (a rebound does not wait for two runs to agree), or None."""
    item = (advice.get('scores') or {}).get(code) if isinstance(advice.get('scores'), dict) else None
    score = item.get('score') if isinstance(item, dict) else None
    return score if type(score) in (int, float) and math.isfinite(score) else None


def oversold_buy(db, read, broker, account, root, day, advice, settings, cap, owned, cash, spent, symbols_today, allowed):
    """낙폭과대 반등 (user 2026-11-13) for vibe-trading: a watched code that fell far beyond its usual range and stopped
    falling (oversold.scan) is bought when vibe's latest score for it reaches oversold_vibe_min; sized like the Jev
    runner (oversold_size_pct of a tranche) within the shared limits. Returns 'buy' when an order went out."""
    import oversold
    from nh_etf_paper_runner import valid_bars
    from nh_paper_isolated import fresh_series, minute_bars
    names = oversold.universe(Path(root).parent / 'shared-etf-paper')
    def bars_of(code):
        response = minute_bars(read, code)
        rows = valid_bars(response.get('data', {}), code, datetime.now(KST)) if response.get('status') == 'ok' else None
        if not rows or not fresh_series(rows, datetime.now(KST)):
            raise ValueError('oversold bars unavailable')
        return rows
    for row in oversold.scan(read, datetime.now(KST), settings, list(names), held=set(owned), bars_of=bars_of):
        if not (row['triggered'] and row['stabilized'] and row['target_pct'] > 0):
            continue
        cycle_id = uuid.uuid4().hex
        if not oversold.market_ok(row, settings):  # a lone fall (user 2026-11-17): recorded once a day, not bought
            if (datetime.now(KST).date(), row['code']) not in _MARKET_SKIPPED:
                _MARKET_SKIPPED.add((datetime.now(KST).date(), row['code']))
                journal_event(root, 'oversold_signal', symbol=row['code'], engine='vibe', permitted=False, reason='market_not_falling',
                              market_low_pct=row.get('market_low_pct'), cycle_id=cycle_id)
            continue
        score = oversold_score(advice or {}, row['code'])
        ok = score is not None and score >= settings['oversold_vibe_min']
        digest = (advice or {}).get('input_digest')
        journal_event(root, 'oversold_signal', symbol=row['code'], engine='vibe', score=score, permitted=ok,
                      drop_pct=row['drop_pct'], atr_multiple=row['atr_multiple'], cycle_id=cycle_id,
                      advice_id=(advice or {}).get('id'), input_digest=digest,
                      fallback_reason='score_unavailable' if score is None else None,
                      premarket_audit=premarket_input_audit(digest, 'vibe'))
        if not ok:
            continue
        quote = quote_of(read, row['code'])
        ask = quote.get('askp')
        if type(ask) is not int or ask <= 0 or type(cash) not in (int, float):
            continue
        want = int(cap['tranche'] * settings['oversold_size_pct'] / 100)
        qty = min(want // ask, int(cash // ask), cap['order'] // ask, quantity_room(owned), cap['symbol'] // ask)
        if qty < 1 or spent + qty * ask > cap['daily_buy'] or (row['code'] not in symbols_today and len(symbols_today) >= cap['new_symbols']):
            journal_event(root, 'v2_execution_gate', permitted=False, reason='oversold_limit', symbol=row['code'], cycle_id=cycle_id)
            continue
        plan = {'target_price': row['target_price'], 'stop_price': row['stop_price'], 'opened': datetime.now(KST).isoformat(),
                'hold_days': int(settings['oversold_hold_days']), 'drop_pct': row['drop_pct']}
        save_oversold_plans(root, {**oversold_plans(root), row['code']: plan})  # before sending, like the Jev runner's plan
        if place(db, broker, account, root, day, row['code'], 'BUY', qty, quote, ask, allowed | {row['code']}, cycle_id, 'oversold'):
            return 'buy'
    return None


def ipo_buy(db, read, broker, account, root, day, settings, cap, owned, cash, spent, symbols_today, allowed):
    """신규주 슬롯 for vibe-trading (user 2026-11-17, like the Jev runner): the Jev runner's new-listing conditions
    (ipo_candidate), checked every cycle whatever else vibe holds; sized ipo_size_pct of a tranche, ipo_daily_max a day,
    one held at a time (ipo_max_positions), with a fixed stop (ipo_stop_pct) and target (ipo_half_pct, all of it),
    the ipo_time_stop_min time stop and the flatten. Rule-only like the Jev slot. Returns 'buy' when an order went out."""
    from nh_paper_isolated import IPO_DAILY_MAX, ipo_candidate, ipo_entries_today
    now = datetime.now(KST)
    if not settings.get('ipo_slot_on', 1) or not settings.get('ipo_days') or ipo_entries_today(root, now) >= IPO_DAILY_MAX:
        return None
    if sum(1 for c, v in oversold_plans(root).items() if c in owned and v.get('variant') == 'ipo') >= int(settings.get('ipo_max_positions', 1)):
        return None
    code, facts, _ = ipo_candidate(read, root, owned, now)
    if code is None:
        return None
    cycle_id = uuid.uuid4().hex
    journal_event(root, 'new_listing', symbol=code, change_pct=facts['change_pct'], turnover_pct=facts['turnover_pct'],
                  sessions=facts['sessions'], cycle_id=cycle_id)
    quote = quote_of(read, code)
    ask = quote.get('askp')
    if type(ask) is not int or ask <= 0 or type(cash) not in (int, float):
        return None
    want = int(cap['tranche'] * settings.get('ipo_size_pct', 50) / 100)
    qty = min(want // ask, int(cash // ask), cap['order'] // ask, quantity_room(owned), cap['symbol'] // ask)
    if qty < 1 or spent + qty * ask > cap['daily_buy'] or (code not in symbols_today and len(symbols_today) >= cap['new_symbols']):
        journal_event(root, 'v2_execution_gate', permitted=False, reason='ipo_limit', symbol=code, cycle_id=cycle_id)
        return None
    stop, half = float(settings.get('ipo_stop_pct', 2.0)), float(settings.get('ipo_half_pct', 3.0))
    plan = {'variant': 'ipo', 'target_price': round(ask * (1 + half / 100)), 'stop_price': round(ask * (1 - stop / 100)),
            'opened': now.isoformat(), 'hold_days': 0}
    save_oversold_plans(root, {**oversold_plans(root), code: plan})  # before sending, like the other plans
    if place(db, broker, account, root, day, code, 'BUY', qty, quote, ask, allowed | {code}, cycle_id, 'new_listing'):
        return 'buy'
    return None


_V2_SENT = {}  # {day: codes sent by 선별 v2}
_MARKET_SKIPPED = set()  # (day, code) of intraday rebounds skipped for a calm market, journaled once


def v2_window(settings, now=None):
    """Inside the 선별 v2 buy window (it may run past the entry cutoff and the flatten, like the Jev runner's)."""
    from session_times import hhmm
    clock = (now or datetime.now(KST)).strftime('%H:%M')
    return bool(settings.get('oversold_v2_on')) and hhmm(settings['oversold_v2_from_hhmm']) <= clock < hhmm(settings['oversold_v2_until_hhmm'])


def v2_levels(settings, average):
    """Half and final take-profit prices of a v2 position at this average."""
    return {'half_price': round(average * (1 + settings['oversold_v2_half_tp_pct'] / 100)),
            'target_price': round(average * (1 + settings['oversold_v2_tp_pct'] / 100))}


def v2_reentry_buy(db, read, broker, account, root, day, settings, cap, owned, cash, spent, symbols_today, allowed, advice, now):
    """선별 v2 re-buy after its stop (v2_reentry, user 2026-11-17): once the low since the stop has held and vibe's latest
    score does not veto the code, buy back the stopped quantity with a new stop under the buy price. 'buy' when sent."""
    import v2_reentry
    from nh_paper_isolated import fresh_rows, sessions_held
    for code, w in sorted(v2_reentry.watch(root).items()):
        if code in owned:
            continue
        if not v2_reentry.open_for(w, now, settings, sessions_held):
            try:
                if sessions_held(datetime.fromisoformat(w['opened']).astimezone(KST).date(), now.date()) >= int(w['hold_days']):
                    v2_reentry.clear(root, code)
            except (KeyError, TypeError, ValueError):
                v2_reentry.clear(root, code)
            continue
        rows = fresh_rows(read, code, now)
        if not rows or not v2_reentry.bottom(root, code, rows, now, settings):
            continue
        w = v2_reentry.watch(root).get(code) or w
        score = oversold_score(advice or {}, code)
        research = 'veto' if score is not None and score <= settings['oversold_v2_vibe_veto'] else 'normal'
        cycle_id = uuid.uuid4().hex
        left = int(w['hold_days']) - sessions_held(datetime.fromisoformat(w['opened']).astimezone(KST).date(), now.date())
        journal_event(root, 'oversold_v2_signal', symbol=code, reentry=w['reentries'] + 1, low=w.get('low'), low_at=w.get('low_at'),
                      permitted=research != 'veto', research=research, engine='vibe', score=score, hold_days=left,
                      stop_pct=settings['oversold_v2_stop_pct'], cycle_id=cycle_id, advice_id=(advice or {}).get('id'))
        if research == 'veto':
            continue
        quote = quote_of(read, code)
        ask = quote.get('askp')
        if type(ask) is not int or ask <= 0 or type(cash) not in (int, float):
            continue
        qty = min(int(w['qty']), int(cash // ask), cap['order'] // ask, quantity_room(owned), cap['symbol'] // ask)
        if qty < 1 or spent + qty * ask > cap['daily_buy']:
            journal_event(root, 'v2_execution_gate', permitted=False, reason='oversold_limit', symbol=code, cycle_id=cycle_id)
            v2_reentry.clear(root, code)
            continue
        plan = {**v2_levels(settings, ask), 'stop_price': round(ask * (1 - settings['oversold_v2_stop_pct'] / 100)),
                'opened': now.isoformat(), 'hold_days': left, 'drop_pct': (w.get('facts') or {}).get('drop_pct'), 'variant': 'v2',
                'entry_price': ask, 'boost': 1.0, 'added': True, 'reentries': w['reentries'] + 1,
                'first_opened': w['opened'], 'first_hold_days': int(w['hold_days'])}
        save_oversold_plans(root, {**oversold_plans(root), code: plan})  # its plan before sending
        v2_reentry.clear(root, code)  # one send per watch, whatever the broker answers
        if place(db, broker, account, root, day, code, 'BUY', qty, quote, ask, allowed | {code}, cycle_id, 'oversold_v2_reentry'):
            return 'buy'
    return None


def oversold_v2_buy(db, read, broker, account, root, day, settings, cap, owned, cash, spent, symbols_today, allowed, advice=None):
    """낙폭과대 선별 v2 낙주매매 for vibe-trading, the Jev runner's plan (user 2026-11-13): inside the window, first the
    second half of yesterday's v2 position when it trades lower (above its stop), then on a market crash day
    (oversold.v2_gate) a watched code that fell with its market and that vibe's own score does not veto
    (oversold_v2_vibe_veto; a score of 0.5+ sizes it by oversold_v2_boost_pct). Half at +half_tp, the rest at +tp, a
    disaster stop under the first day's low, at most hold_days sessions. Returns 'buy' when an order went out."""
    import oversold
    from nh_paper_isolated import sessions_held
    now = datetime.now(KST)
    status = v2_reentry_buy(db, read, broker, account, root, day, settings, cap, owned, cash, spent, symbols_today, allowed, advice, now)
    if status or not v2_window(settings, now):
        return status
    plans = oversold_plans(root)
    today = now.date().isoformat()
    if today not in _V2_SENT:  # first look today (or after a restart): what this book already sent today
        _V2_SENT[today] = set(oversold.sent_today(Path(root) / 'events.jsonl', today))
    sent = _V2_SENT[today]
    for old in [d for d in _V2_SENT if d != today]:
        _V2_SENT.pop(old)
    half = 0.5 if settings.get('oversold_v2_add_on') else 1.0
    base = int(cap['tranche'] * settings['oversold_v2_size_pct'] / 100)

    def buy(code, quote, ask, qty, plan, source, cycle_id):
        if qty < 1 or spent + qty * ask > cap['daily_buy'] or (code not in symbols_today and len(symbols_today) >= cap['new_symbols']):
            journal_event(root, 'v2_execution_gate', permitted=False, reason='oversold_limit', symbol=code, cycle_id=cycle_id)
            sent.add((source, code))  # the limits will not loosen today
            return None
        if source == 'oversold_v2':
            save_oversold_plans(root, {**oversold_plans(root), code: plan})  # a new position: its plan before sending
        sent.add((source, code))  # one order a day per code and step, whatever the broker answers
        placed = place(db, broker, account, root, day, code, 'BUY', qty, quote, ask, allowed | {code}, cycle_id, source)
        if placed and source == 'oversold_v2_add':  # the second half changes the plan only once it was sent
            save_oversold_plans(root, {**oversold_plans(root), code: plan})
        return 'buy' if placed else None

    if settings.get('oversold_v2_add_on'):
        for code, plan in plans.items():
            if (plan.get('variant') != 'v2' or code not in owned or plan.get('added') or plan.get('half_done')
                    or ('oversold_v2_add', code) in sent):
                continue
            try:
                if sessions_held(datetime.fromisoformat(plan['opened']).astimezone(KST).date(), now.date()) != 1:
                    continue
                quote = quote_of(read, code)
            except (KeyError, TypeError, ValueError):
                continue
            ask = quote.get('askp')
            if type(ask) is not int or not plan['stop_price'] < ask < plan.get('entry_price', 0) or type(cash) not in (int, float):
                continue
            held = owned[code]
            qty = min(int(base * half * plan.get('boost', 1)) // ask, int(cash // ask), cap['order'] // ask, quantity_room(owned),
                      max(0, cap['symbol'] - held * plan['entry_price']) // ask)
            average = (plan['entry_price'] * held + ask * qty) / (held + qty) if qty > 0 else ask
            updated = {**plan, **v2_levels(settings, average), 'added': True, 'add_price': ask,
                       'stop_price': round(average * (1 - settings['oversold_v2_stop_pct'] / 100))}  # under the new average
            status = buy(code, quote, ask, qty, updated, 'oversold_v2_add', uuid.uuid4().hex)
            if status:
                return status
    mine = {c for s, c in sent if s == 'oversold_v2'} | {c for c, p in plans.items() if p.get('variant') == 'v2' and str(p.get('opened', '')).startswith(today)}
    if len(mine) >= settings['oversold_v2_max_new']:
        return None
    try:
        gate = oversold.v2_gate(read, now, settings)
    except Exception:  # a failed read is retried next minute
        return None
    if not (gate['kospi']['pass'] or gate['kosdaq']['pass']):
        return None
    names = oversold.universe(Path(root).parent / 'shared-etf-paper')
    for row in oversold.v2_candidates(read, gate, settings, list(names), held=set(owned) | mine | {c for s, c in sent if s == 'veto'}):
        cycle_id = uuid.uuid4().hex
        score = oversold_score(advice or {}, row['code'])
        research = ('veto' if score is not None and score <= settings['oversold_v2_vibe_veto']
                    else 'boost' if score is not None and score >= 0.5 else 'normal')
        digest = (advice or {}).get('input_digest')
        journal_event(root, 'oversold_v2_signal', symbol=row['code'], drop_pct=row['drop_pct'], market=row['market'],
                      market_low_pct=row['market_low_pct'], rel_drop=row['rel_drop'], stop_pct=row['stop_pct'],
                      hold_days=row['hold_days'], permitted=research != 'veto', research=research, engine='vibe', score=score,
                      cycle_id=cycle_id, advice_id=(advice or {}).get('id'), input_digest=digest,
                      fallback_reason='score_unavailable' if score is None else None,
                      premarket_audit=premarket_input_audit(digest, 'vibe'))
        if research == 'veto':
            sent.add(('veto', row['code']))
            continue
        quote = quote_of(read, row['code'])
        ask = quote.get('askp')
        if type(ask) is not int or ask <= 0 or type(cash) not in (int, float):
            continue
        boost = settings['oversold_v2_boost_pct'] / 100 if research == 'boost' else 1.0
        qty = min(int(base * half * boost) // ask, int(cash // ask), cap['order'] // ask, quantity_room(owned), cap['symbol'] // ask)
        plan = {**v2_levels(settings, ask), 'stop_price': row['stop_price'], 'opened': now.isoformat(), 'hold_days': row['hold_days'],
                'drop_pct': row['drop_pct'], 'variant': 'v2', 'entry_price': ask, 'boost': boost, 'added': not settings.get('oversold_v2_add_on')}
        status = buy(row['code'], quote, ask, qty, plan, 'oversold_v2', cycle_id)
        if status:
            return status
    return None


def execute(db, read, broker, account, root, day, advice_id, idx, decision, allowed, owned, cash, spent, symbols_today, loss_halt, *, held_context=None, model_sell_advice=None, model_buy_advice=None):
    """Handle one decision: returns 'buy'/'sell' when an order went out, else records why not and returns None."""
    symbol, action, requested = decision.get('symbol'), decision.get('action'), decision.get('quantity')
    cycle_id = uuid.uuid4().hex

    def send(side, qty, quote, price, check=None, extra=None):
        advice = model_sell_advice if side == 'SELL' else model_buy_advice
        evidence = {**decision_evidence(advice, idx), **(extra or {})}
        journal_event(root, 'vibe_decision_intent', symbol=symbol, side=side, cycle_id=cycle_id, **evidence)
        mark(db, advice_id, idx, 'sending')  # before transport: a crash after this can never resend the decision
        kwargs = {'sell_trigger': 'model', 'signal_check_extra': check} if side == 'SELL' else ({'signal_check_extra': check} if check else {})
        kwargs['decision_evidence'] = evidence
        if place(db, broker, account, root, day, symbol, side, qty, quote, price, allowed, cycle_id, **kwargs):
            mark(db, advice_id, idx, 'sell_submitted' if side == 'SELL' else 'buy_submitted')
            if side == 'BUY' and type(decision.get('confidence')) in (int, float):  # the bar the next add must beat
                with db:
                    db.execute('INSERT INTO buy_confidence VALUES (?,?,?)', (symbol, datetime.now(KST).isoformat(), float(decision['confidence'])))
            return True
        with db:
            db.execute('DELETE FROM advice_executions WHERE advice_id=? AND idx=?', (advice_id, idx))
        return False
    reason = None
    if symbol not in allowed:
        reason = 'symbol_not_allowed'
    elif action == 'SELL':
        held = owned.get(symbol, 0)
        qty = held if requested == 'ALL' else min(requested, held) if type(requested) is int and requested > 0 else 0
        if qty < 1:
            reason = 'not_held'
        elif (oversold_plans(root).get(symbol) or {}).get('variant') == 'v2':
            reason = 'oversold_v2_hold'  # 선별 v2 sells at its own targets, stop or sessions; vibe's view is recorded only
        else:
            check = lambda: model_sell_check(db, root, symbol, model_sell_advice, idx, decision)
            if not check():
                reason = 'model_position_changed'
            else:
                quote = quote_of(read, symbol)
                if send('SELL', qty, quote, quote.get('bidp'), check):
                    return 'sell'
                reason = 'not_sent'
    elif action == 'BUY':
        confidence = decision.get('confidence')
        quote = quote_of(read, symbol)
        ask = quote.get('askp')
        if type(ask) is not int or ask <= 0 or type(cash) not in (int, float) or not math.isfinite(cash):
            raise ValueError('paper buying power or ask unavailable')
        notional = decision.get('notional')
        wanted = (notional // ask if type(notional) is int and notional > 0
                  else requested if type(requested) is int and requested > 0 else 0)
        cap = limits()
        avg = held_context[2] if held_context else ask
        if type(avg) not in (int, float) or not math.isfinite(avg) or avg <= 0:
            raise ValueError('paper average price unavailable')
        qty = min(wanted, int(cash // ask), cap['order'] // ask, quantity_room(owned),
                  int(max(0, cap['symbol'] - owned.get(symbol, 0) * avg) // ask))
        qty = int(qty * sizing_factor(root, symbol, owned.get(symbol, 0) * avg, cap['symbol']))
        if datetime.now(KST).strftime('%H:%M') >= ENTRY_CUTOFF:
            reason = 'entry_cutoff'
        elif loss_halt:
            reason = loss_halt if isinstance(loss_halt, str) else 'daily_loss_limit'
        elif type(confidence) not in (int, float) or not math.isfinite(confidence):
            reason = 'invalid_score'
        elif qty < 1:
            reason = 'quantity_or_cash_limit'
        elif spent + qty * ask > cap['daily_buy']:
            reason = 'daily_buy_limit'
        elif symbol not in symbols_today and symbol not in owned and len(symbols_today) >= cap['new_symbols']:
            reason = 'daily_symbol_limit'
        elif symbol in owned and held_context is None:
            reason = 'held_entry_wait'
        elif symbol in owned and not (why := add_better(db, symbol, held_context, ask, quote, confidence)):
            reason = 'add_not_better'
        elif symbol in owned:
            original_id, settings, avg, bars = held_context
            import nh_paper_isolated
            qty = max(1, int(qty * nh_paper_isolated.add_size_factor(confidence, setting('vibe_buy_score'))))  # half a tranche (add_size_factor)
            check = lambda: (owned_positions(db) == owned
                             and model_buy_check(root, model_buy_advice, idx, symbol, advice_id=original_id)
                             and held_buy_check(db, root, symbol, owned[symbol], avg, qty, quote, original_id, settings, bars))
            if not check():
                reason = 'held_entry_wait'
            elif send('BUY', qty, quote, ask, check, {'confidence': confidence, 'add_reason': why}):
                return 'buy'
            else:
                reason = 'not_sent'
        else:
            check = lambda: (owned_positions(db) == owned
                             and model_buy_check(root, model_buy_advice, idx, symbol, decision, advice_id,
                                                 require_technical=True))
            if not check():
                # tell a bar that aged out apart from a decision that really changed (both end this decision)
                reason = ('technical_stale' if owned_positions(db) == owned and model_buy_check(
                    root, model_buy_advice, idx, symbol, decision, advice_id) else 'decision_changed')
            elif send('BUY', qty, quote, ask, check, {'confidence': confidence, 'add_reason': 'first'}):
                return 'buy'
            else:
                reason = 'not_sent'
    else:
        reason = 'invalid_action'
    if reason not in ('not_sent', 'held_entry_wait'):  # a pending condition is retried while advice is fresh
        mark(db, advice_id, idx, reason)
        journal_event(root, 'v2_execution_gate', permitted=False, reason=reason, symbol=symbol, cycle_id=cycle_id)
    return None


def run(configs, runtime, once=False):
    from nh_paper_isolated import NHOrderGuard, paper_account, reserved_hashes, selected_config
    from src.tools.mcp import MCPServerAdapter
    runtime = Path(runtime)
    isolated, root = runtime / 'nhplug/isolated-paper', runtime / 'nhplug/vibe-paper'
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    digest = json.loads((isolated / 'account.json').read_text())['account_sha256']
    if digest in reserved_hashes(runtime):
        raise ValueError('selected account now belongs to a swing strategy')
    config = selected_config(configs, digest)
    policy = NHOrderGuard(config.args).paper_policy()
    if not isinstance(policy, dict) or policy.get('_account_independent') is not True:
        raise ValueError('independent account policy is not registered')
    account = paper_account(config)
    read = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])
    broker = MCPServerAdapter('nhplug_paper_isolated', config.model_copy(update={'args': [*config.args, '--guarded-paper']}))
    start_quote_snapshots()
    # Same lock file as the legacy isolated runner: the two can never trade this account at once.
    with (isolated / 'runner.lock').open('a') as lock, (root / 'runner.lock').open('a') as own:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
        loop(connect_journal(root / 'orders.sqlite3', digest), read, broker, account, root, once)


VIRTUAL_ACCOUNT = '00000000000'  # placeholder: the virtual broker has no real account


def run_virtual(configs, runtime, once=False):
    """vibe-trading on the virtual broker (no stock-capable NH paper account; user decision 2026-11-12): the same
    cycle, limits and journal (vibe-paper/orders.sqlite3), with fills simulated against live read-only quotes."""
    from virtual_paper_broker import VirtualBroker, shared_guard_limits
    from nh_paper_isolated import account_hash
    from src.tools.mcp import MCPServerAdapter
    root = Path(runtime) / 'nhplug/vibe-paper'
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    read = MCPServerAdapter('nhplug_readonly', configs['nhplug_readonly'])
    broker = VirtualBroker(root / 'virtual-account.sqlite3', read, limits=shared_guard_limits)
    start_quote_snapshots()
    with (root / 'runner.lock').open('a') as own:
        fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
        loop(connect_journal(root / 'orders.sqlite3', account_hash(VIRTUAL_ACCOUNT)), read, broker, VIRTUAL_ACCOUNT, root, once,
             idle=lambda db: _main_and_flat(root, db))


def _main_and_flat(root, db):
    """vibe is the main engine and this virtual book holds and owes nothing: skip the cycle."""
    from engine_roles import main_engine
    return (main_engine(Path(root).parent) == 'vibe' and not owned_positions(db)
            and not db.execute("SELECT 1 FROM intents WHERE status!='terminal'").fetchone())


def refresh_clock(root):
    """The shared runner's cutoff, flatten and session close (dashboard settings) for this book too."""
    global ENTRY_CUTOFF, FLATTEN_AT
    import nh_paper_isolated
    nh_paper_isolated.refresh_clock(Path(root).parent / 'shared-etf-paper')  # its order checks use the same close
    ENTRY_CUTOFF, FLATTEN_AT = nh_paper_isolated.ENTRY_CUTOFF, nh_paper_isolated.FLATTEN_AT


def in_session(now):
    import nh_paper_isolated
    return nh_paper_isolated.in_session(now)


def loop(db, read, broker, account, root, once, idle=None):
    from session_times import MARKET_OPEN, order_start
    failures = 0
    protective_recheck = False
    try:
        while True:
            started = time.monotonic()
            now = datetime.now(KST)
            refresh_clock(root)
            # observe until the first order minute (a later 장 시작 setting used to end the day at 09:10)
            if now.weekday() < 5 and MARKET_OPEN <= now.strftime('%H:%M') < order_start() and not (root / 'STOP').exists():
                if once:
                    break
                observe_state(db, broker, account, root)
                time.sleep(30)
                continue
            if not in_session(now) or (root / 'STOP').exists():
                settle_before_stop(db, broker, account, root, True)
                break
            try:  # a passing read failure must not leave holdings unwatched for the rest of the day
                status = 'idle_main_on_shared' if idle is not None and idle(db) else cycle(db, read, broker, account, root, now)
                failures = 0
            except ValueError as exc:
                failures += 1
                if 'reconcil' in str(exc) or 'uncertain' in str(exc) or failures >= TRANSIENT_LIMIT:
                    raise
                status = 'error:' + type(exc).__name__
                journal_event(root, 'v2_error', reason='transient_read_failure', detail=str(exc)[:120])
            print(json.dumps({'status': status, 'time': now.isoformat()}), flush=True)
            if once:
                break
            if status in ('protective_wait', 'sell_protective_wait'):
                protective_recheck = True
            elif status not in ('pending', 'reconciled') and not status.startswith('error:'):
                protective_recheck = False
            delay = (1 if protective_recheck and status == 'reconciled' else 30 if protective_recheck else
                     60 if status in ('pending', 'reconciled', 'idle_main_on_shared') or status.startswith('error:') else
                     cycle_delay(started))
            time.sleep(delay)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--virtual', action='store_true', help='simulate fills against live quotes (no paper account)')
    args = parser.parse_args()
    from src.config.loader import load_agent_config
    from src.config.paths import get_runtime_root
    (run_virtual if args.virtual else run)(load_agent_config().mcp_servers, get_runtime_root(), once=args.once)


if __name__ == '__main__':
    main()
