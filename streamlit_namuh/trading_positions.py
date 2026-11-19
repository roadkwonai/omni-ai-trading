"""Engine-ledger holdings and confirmed stop-loss fills."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

import engine_compare as ec
from engine_roles import ledger_root
from sqlite_ro import ro_connect

KST = ZoneInfo('Asia/Seoul')
STOP_REASONS = {'stop_loss': '손절', 'trailing_stop': '추적 손절', 'breakeven_stop': '본전 손절'}


def grouped_symbols(info):
    """Keep holdings and today's confirmed sells together once per symbol."""
    groups = {}
    for row in info.get('holdings') or []:
        groups[row['symbol']] = {'symbol': row['symbol'], 'holding': row, 'stops': []}
    for row in info.get('sells', info.get('stops')) or []:
        group = groups.setdefault(row['symbol'], {'symbol': row['symbol'], 'holding': None, 'stops': []})
        group['stops'].append(row)
    def stop_time(row):
        try:
            at = datetime.fromisoformat(row.get('time') or '')
            return at.timestamp() if at.tzinfo is not None else float('-inf')
        except (ValueError, TypeError, OverflowError):
            return float('-inf')
    for group in groups.values():
        group['stops'].sort(key=stop_time, reverse=True)
    return list(groups.values())


def group_label(group, names):
    code = group['symbol']
    parts = [f"{names.get(code, code)} ({code})"]
    if group['holding']:
        parts.append(f"보유 {group['holding']['qty']:,}주")
    if group['stops']:
        latest = group['stops'][0]
        suffix = f" 외 {len(group['stops']) - 1}건" if len(group['stops']) > 1 else ''
        try:
            at = datetime.fromisoformat(latest.get('time') or '')
            stamp = at.astimezone(KST).strftime('%H:%M') if at.tzinfo is not None else '시각 미확인'
        except (TypeError, ValueError, OverflowError):
            stamp = '시각 미확인'
        parts.append(f"오늘 {latest.get('outcome', '손절')} {latest['qty']:,}주 · {stamp}{suffix}")
    return ' · '.join(parts)


def _today(stamp, now):
    try:
        at = datetime.fromisoformat(stamp)
        return at.tzinfo is not None and at.astimezone(KST).date() == now.date()
    except (TypeError, ValueError):
        return False



def _recent(stamp, now):
    try:
        at = datetime.fromisoformat(stamp)
        if at.tzinfo is None:
            return False
        at = at.astimezone(KST)
        return now.date() - timedelta(days=6) <= at.date() <= now.date() and at <= now
    except (TypeError, ValueError, OverflowError):
        return False


def snapshot(runtime, now=None):
    """Jev ledger holdings and the last week's sells, recomputed only when the ledger or the journal changes or the day
    turns: the sell reasons scan the whole events.jsonl (39 MB), which a 2-second cache repeated on nearly every rerun."""
    try:
        root = ledger_root(Path(runtime), 'jev')
        stamps = tuple((f.stat().st_mtime_ns, f.stat().st_size) if f.exists() else None
                       for f in (root / 'orders.sqlite3', root / 'orders.sqlite3-wal', root / 'events.jsonl'))
    except (OSError, TypeError):
        stamps = None
    # without `now` the result holds for the whole day: no fill is stamped later than the moment it is read
    day_end = datetime.now(KST).replace(hour=23, minute=59, second=59, microsecond=0)
    return _snapshot(runtime, (now or day_end).astimezone(KST), stamps)


@st.cache_data(max_entries=8, show_spinner=False)
def _snapshot(runtime, now, stamps):  # stamps only keys the cache (no leading underscore: that would drop it from the key)
    result = {}
    for engine in ('jev',):  # the intraday vibe book is frozen since 2026-11-17
        empty = {'status': 'unavailable', 'holdings': [], 'stops': [], 'sells': [], 'recent_stops': [], 'unknown_sells': 0}
        try:
            root = ledger_root(Path(runtime), engine)
            db_path, events_path = root / 'orders.sqlite3', root / 'events.jsonl'
            if not db_path.is_file():
                result[engine] = empty
                continue
            with ro_connect(db_path) as db:
                rows = db.execute('SELECT order_id,symbol,side,quantity,price,time FROM fills ORDER BY rowid').fetchall()
                links = dict(db.execute('SELECT order_id,cycle_id FROM decision_order_links').fetchall())
            needed = {links.get(order_id) for order_id, _, side, _, _, time in rows
                      if side == 'SELL' and _recent(time, now)}
            cycles = ec._cycles(events_path, needed - {None}) if events_path.is_file() else {}
            holdings, stops, sells, recent_stops, unknown = {}, [], [], [], 0
            for order_id, symbol, side, qty, price, stamp in rows:
                if type(qty) is not int or qty <= 0 or side not in ('BUY', 'SELL'):
                    raise ValueError('invalid fill')
                old = holdings.get(symbol, {'qty': 0, 'avg': 0.0})
                held, avg, _, pnl, _ = ec.apply_fill(old['qty'], old['avg'], side, qty, price)
                if side == 'SELL' and qty > old['qty']:
                    raise ValueError('sell exceeds holdings')
                if held:
                    holdings[symbol] = {'symbol': symbol, 'qty': held, 'avg': avg}
                else:
                    holdings.pop(symbol, None)
                if side == 'SELL' and _recent(stamp, now):
                    events = [e for e in cycles.get(links.get(order_id), [])
                              if e.get('symbol') == symbol]
                    risk = next((e for e in events if e.get('event') == 'v2_risk_exit'), {})
                    sell = next((e for e in events if e.get('event') == 'sell_submitted'), {})
                    reason = risk.get('reason') or sell.get('trigger')
                    outcome = ('익절' if pnl is not None and pnl > 0 else '본전 매도' if pnl == 0 else
                               '손절' if pnl is not None and reason in STOP_REASONS else
                               '손실 매도' if pnl is not None else '매도')
                    item = {'symbol': symbol, 'time': stamp, 'qty': qty, 'price': price,
                            'reason': STOP_REASONS.get(reason), 'outcome': outcome, 'pnl': pnl}
                    if _today(stamp, now):
                        sells.append(item)
                    if outcome == '손절':
                        recent_stops.append(item)
                        if _today(stamp, now):
                            stops.append(item)
                    if _today(stamp, now) and (not reason or reason in ('risk', 'protective')):
                        unknown += 1
            result[engine] = {'status': 'ok', 'holdings': list(holdings.values()),
                              'stops': list(reversed(stops)), 'sells': list(reversed(sells)),
                              'recent_stops': sorted(recent_stops, key=lambda item: datetime.fromisoformat(item['time']), reverse=True),
                              'unknown_sells': unknown}
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            result[engine] = empty
    return result


snapshot.clear = _snapshot.clear
