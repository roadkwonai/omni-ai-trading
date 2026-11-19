"""NXT after-market close-bet strategy on virtual fills only (user decision 2026-11-12). Never sends an order.

User's plan: trade NXT only as a closing bet on a strong market day, reading KOSPI night futures, Nasdaq/S&P500
futures, oil and gold futures with the news, market analysis, volume, trading value and investor flows; buy in the
NXT after-market and sell the next day for 1-2%; a swing position may also be sold in the NXT after-market. NH's
mock server rejects NXT orders (14280, checked 2026-11-12), so for now every buy and sell is a virtual fill against
the live NXT/KRX quote and the whole session is kept as learning data (see docs/nxt.md).

Sessions (Asia/Seoul):
  evening  15:40-19:58  snapshots every 10 minutes; one entry decision in 19:30-19:50; swing exit signals
  morning  08:00-10:05  exits of yesterday's virtual buys (NXT pre-market, then KRX); outcome labels 08:05/09:05/10:00
Data: ~/.vibe-trading/nhplug/nxt-virtual/ events.jsonl (audit trail), nxt.sqlite3 (same events, queryable),
virtual-account.sqlite3 (orders and fills). `--audit` cross-checks them. STOP in that folder ends a session.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from config_store import write_json
import api_calls  # noqa: F401,E402 - counts this process's external API calls (dashboard 'API 연동')

KST = ZoneInfo('Asia/Seoul')
ROOT = Path.home() / '.vibe-trading/nhplug/nxt-virtual'
SHARED = Path.home() / '.vibe-trading/nhplug/shared-etf-paper'
FUTURES = {'nasdaq': 'NQ=F', 'sp500': 'ES=F', 'oil': 'CL=F', 'gold': 'GC=F'}
# Naver's FUT quote stops at the 15:35 day close; TradingView's scanner keeps updating through the KRX night session.
TV_SCAN = 'https://scanner.tradingview.com/futures/scan'
TV_KOSPI200 = 'KRX:K2I1!'
TV_COLUMNS = ('close', 'change', 'time', 'last_bar_update_time', 'update_mode')
# Initial virtual values (no money at risk); change them only after reviewing the learning data.
BOOT_WINDOWS = {'morning': ('07:50', '10:05'), 'evening': ('15:30', '19:58')}  # when a start (scheduled or at boot) runs a session
SETTINGS = {
    'after_start': '15:40', 'after_end': '19:58', 'entry_start': '19:30', 'entry_end': '19:50', 'snapshot_minutes': 10,
    'morning_start': '08:00', 'premarket_end': '08:50', 'krx_open': '09:00', 'exit_deadline': '10:00',
    'label_times': ('08:05', '09:05', '10:00'),
    'max_positions': 3, 'size_krw': 1_000_000, 'single_share_max_krw': 2_000_000, 'cash_krw': 10_000_000,
    'max_candidates': 20,
    # market gate: a strong day only
    'us_futures_min': 0.0, 'night_futures_min': 0.0, 'oil_max': 3.0, 'gold_max': 1.5,
    # stock gate
    'change_min': 2.0, 'change_max': 15.0, 'close_position_min': 0.7, 'vol_rate_min': 150.0, 'value_min_mil': 30_000,
    'near_high_pct': 3.0, 'spread_max_pct': 0.5,
    # next-day exits
    'half_pct': 1.0, 'all_pct': 2.0, 'stop_pct': 2.0,
    # swing positions: sell signal in the NXT after-market
    'swing_exit_futures': -0.7,
}
ETF_CODES = {'069500', '122630', '114800', '229200', '233740', '251340', '494310'}  # NH: "NXT 거래불가종목"


def hm(now):
    return now.strftime('%H:%M')


# ── market data ──────────────────────────────────────────────────────────────
def futures():
    """{name: {'pct', 'price', 'previous'}} against the previous settlement; a failed ticker is left out."""
    import yfinance as yf
    out = {}
    for name, ticker in FUTURES.items():
        try:
            with api_calls.track('yahoo', 'yfinance/fast_info (해외 선물)'):
                info = yf.Ticker(ticker).fast_info
                last, previous = float(info.last_price), float(info.previous_close)
            if last > 0 and previous > 0:
                out[name] = {'pct': round((last / previous - 1) * 100, 2), 'price': last, 'previous': previous}
        except Exception:  # network or Yahoo change: that input is simply missing this time
            continue
    return out


def _tradingview_kospi200():
    import requests
    body = {'symbols': {'tickers': [TV_KOSPI200]}, 'columns': list(TV_COLUMNS)}
    row = requests.post(TV_SCAN, json=body, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8).json()['data'][0]['d']
    return dict(zip(TV_COLUMNS, row))


def night_futures(now, fetch=None):
    """KOSPI200 front-month futures (TradingView, ~20 min delayed): change vs the day-session close.
    'night' only when the current session bar opened in 18:00-06:00 and was updated within 40 minutes."""
    try:
        data = (fetch or _tradingview_kospi200)()
        opened = datetime.fromtimestamp(int(data['time']), KST)
        updated = datetime.fromtimestamp(int(data['last_bar_update_time']), KST)
        pct, price = round(float(data['change']), 2), float(data['close'])
    except Exception:
        return {'available': False}
    # the trading-day bar opens at 18:00 the evening before, so after 15:30 it still 'opened at night'; require the
    # bar to be this evening's (11/13 15:35~16:35 recorded the day move as night futures)
    evening = opened.hour >= 18 or opened.hour < 6
    night = evening and now - opened < timedelta(hours=12)
    fresh = now - updated < timedelta(minutes=40)
    # 06:00~08:45 the bar still holds the night session that has ended (last trade in night hours): label it 'night'
    # (a closed session's close for the premarket brief) without making it available to the gate
    ended = evening and not night and (updated.hour >= 18 or updated.hour < 7)
    return {'available': night and fresh, 'session': 'night' if night or ended else 'day', 'pct': pct, 'price': price,
            'session_open': opened.isoformat(), 'traded_at': updated.isoformat(), 'delay': data.get('update_mode')}


def index_view(context):
    """{KOSPI|KOSDAQ: regime, today's flow, foreign+institution and program net buying (100m KRW)} from market_context."""
    view = {}
    for key, item in ((context or {}).get('indices') or {}).items():
        investors, program = item.get('investors_100m') or {}, item.get('program_100m') or {}
        view[key] = {'regime': (item.get('regime') or {}).get('label'), 'flow': item.get('flow'),
                     'change_pct': item.get('change_pct'), 'foreign': investors.get('외국인'), 'institution': investors.get('기관'),
                     'program': program.get('전체'), 'breadth': item.get('breadth')}
    return view


def macro(now, context=None):
    from market_context import load
    context = context if context is not None else load()
    return {'time': now.isoformat(), 'futures': futures(), 'night': night_futures(now),
            'index': index_view(context), 'context_time': (context or {}).get('time')}


def market_gate(m, index, s=SETTINGS):
    """Failure codes for a closing bet in this stock's market (empty list = strong enough)."""
    fails = []
    view = (m.get('index') or {}).get(index) or {}
    if view.get('regime') != '상승장':
        fails.append('regime_not_rising')
    if view.get('flow') != '강세':
        fails.append('today_not_strong')
    flows = [v for v in (view.get('foreign'), view.get('institution')) if isinstance(v, (int, float))]
    if len(flows) < 2 or sum(flows) <= 0:
        fails.append('flows_not_supporting')
    fut = m.get('futures') or {}
    us = [fut[k]['pct'] for k in ('nasdaq', 'sp500') if k in fut]
    if len(us) < 2:
        fails.append('us_futures_unavailable')
    elif min(us) < s['us_futures_min']:
        fails.append('us_futures_down')
    night = m.get('night') or {}
    if night.get('available') and night['pct'] < s['night_futures_min']:
        fails.append('night_futures_down')
    if 'oil' in fut and fut['oil']['pct'] > s['oil_max']:
        fails.append('oil_spike')
    if 'gold' in fut and fut['gold']['pct'] > s['gold_max']:
        fails.append('gold_spike')
    return fails


# ── stocks ───────────────────────────────────────────────────────────────────
def candidates(shared=SHARED, limit=SETTINGS['max_candidates']):
    """NXT-eligible stock candidates (ETFs excluded): today's movers and the Jev watchlist, strongest first."""
    found = {}
    for name in ('movers.json', 'watchlist.json'):
        try:
            data = json.loads((Path(shared) / name).read_text())
        except (OSError, ValueError):
            continue
        rows = data.get('rows') or data.get('stocks') or {}
        for code, row in rows.items():
            if code not in ETF_CODES and isinstance(row, dict) and isinstance(row.get('change_pct'), (int, float)):
                found.setdefault(code, {'code': code, 'name': row.get('name'), 'index': row.get('index'),
                                        'change_pct': row['change_pct'], 'value_mil': row.get('value_mil')})
    return sorted(found.values(), key=lambda r: -r['change_pct'])[:limit]


def market_of(index):
    return 'KOSDAQ' if index and 'KOSDAQ' in str(index).upper() else 'KOSPI'


def quote(read, code, market):
    """NH current price for KRX or NXT; None when NH says the stock is not traded there (or no price)."""
    response = read.call_tool('call_api', {'operationId': 'krstockQuoteCurrentPrice', 'input': {'market_cd': market, 'iem_cd': code}})
    data = response.get('data') if isinstance(response, dict) and response.get('status') == 'ok' else None
    output = (data or {}).get('Output_0')
    return output if isinstance(output, dict) and type(output.get('stck_prpr')) is int and output['stck_prpr'] > 0 else None


def features(krx, nxt):
    """Learning features of one stock at decision time (KRX regular session + NXT after-market)."""
    close, prev, high, low = krx['stck_prpr'], krx.get('stck_prdy_clpr'), krx.get('stck_hgpr'), krx.get('stck_lwpr')
    bid, ask = nxt.get('bidp'), nxt.get('askp')
    f = {'krx_close': close, 'change_pct': round((close / prev - 1) * 100, 2) if prev else None,
         'close_position': round((close - low) / (high - low), 3) if high and low and high > low else None,
         'vol_rate': krx.get('vol_rate'), 'value_mil': krx.get('acml_tr_pbmn'), 'd5_high': krx.get('d5_hgpr'),
         'd20_high': krx.get('d20_hgpr'), 'nxt_price': nxt['stck_prpr'], 'nxt_bid': bid, 'nxt_ask': ask,
         'nxt_vs_close_pct': round((nxt['stck_prpr'] / close - 1) * 100, 2), 'nxt_value_mil': nxt.get('acml_tr_pbmn'),
         'nxt_volume': nxt.get('acml_vol'), 'nxt_time': nxt.get('hoga_bsop_hour')}
    f['spread_pct'] = round((ask - bid) / bid * 100, 3) if type(bid) is int and type(ask) is int and bid > 0 else None
    f['near_5d_high_pct'] = round((1 - close / f['d5_high']) * 100, 2) if f['d5_high'] else None
    return f


def stock_gate(f, s=SETTINGS):
    fails = []
    if f['change_pct'] is None or not s['change_min'] <= f['change_pct'] <= s['change_max']:
        fails.append('change_out_of_range')
    if f['close_position'] is None or f['close_position'] < s['close_position_min']:
        fails.append('weak_close')
    if not isinstance(f['vol_rate'], (int, float)) or f['vol_rate'] < s['vol_rate_min']:
        fails.append('volume_not_high')
    if not isinstance(f['value_mil'], (int, float)) or f['value_mil'] < s['value_min_mil']:
        fails.append('value_too_small')
    if f['near_5d_high_pct'] is None or f['near_5d_high_pct'] > s['near_high_pct']:
        fails.append('below_5d_high')
    if f['nxt_vs_close_pct'] < 0:
        fails.append('nxt_below_close')
    if f['spread_pct'] is None or f['spread_pct'] > s['spread_max_pct']:
        fails.append('spread_too_wide')
    return fails


# ── store ────────────────────────────────────────────────────────────────────
class Journal:
    """events.jsonl (append-only audit trail) and nxt.sqlite3 (the same events, queryable for learning)."""

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.root / 'nxt.sqlite3', timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, day TEXT NOT NULL, event TEXT NOT NULL, symbol TEXT, data TEXT NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS events_day ON events(day, event)')
        self.db.commit()

    def write(self, event, now, **data):
        row = {'time': now.isoformat(timespec='seconds'), 'event': event, **data}
        line = json.dumps(row, ensure_ascii=False, default=str)
        with open(self.root / 'events.jsonl', 'a') as f:
            f.write(line + '\n')
        with self.db:
            self.db.execute('INSERT INTO events (time,day,event,symbol,data) VALUES (?,?,?,?,?)',
                            (row['time'], now.strftime('%Y%m%d'), event, data.get('symbol'), line))
        return row

    def last(self, event, day=None):
        sql, args = 'SELECT data FROM events WHERE event=?', [event]
        if day:
            sql, args = sql + ' AND day=?', args + [day]
        row = self.db.execute(sql + ' ORDER BY id DESC LIMIT 1', args).fetchone()
        return json.loads(row[0]) if row else None

    def rows(self, event, day):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM events WHERE event=? AND day=? ORDER BY id', (event, day))]


def broker(read, root=ROOT, clock=None):
    """VirtualBroker whose fills use the NXT quote in NXT sessions and the KRX quote in the regular session."""
    from virtual_paper_broker import VirtualBroker

    class NxtVirtualBroker(VirtualBroker):
        def quote(self, symbol):
            now = self.clock()
            market = 'KRX' if SETTINGS['krx_open'] <= hm(now) < '15:30' else 'NXT'
            q = quote(self.read, symbol, market)
            bid, ask = (q or {}).get('bidp'), (q or {}).get('askp')
            if type(bid) is not int or type(ask) is not int or not 0 < bid <= ask:
                raise ValueError('invalid bid/ask')
            self.quotes[symbol] = (time.time(), bid, ask)
            return bid, ask

    return NxtVirtualBroker(Path(root) / 'virtual-account.sqlite3', read, cash=SETTINGS['cash_krw'], clock=clock)


def order(book, side, code, qty, price):
    op = 'krstockOrderCashBuy' if side == 'BUY' else 'krstockOrderCashSell'
    result = book.call_tool('call_api', {'operationId': op, 'input': {'iem_cd': code, 'orr_qty': qty, 'orr_pr': price,
                                                                       'rmt_mkt_cd': 'NXT', 'sor_mkt_sli_yn': 'N'}})
    number = ((result.get('data') or {}).get('Output_0') or {}).get('mkt_orr_no') if result.get('status') == 'ok' else None
    filled = number is not None and book.db.execute('SELECT filled, fill_price FROM orders WHERE number=?', (number,)).fetchone()
    if number is not None and not (filled and filled[0]):  # IOC: an unfilled virtual order never rests (no double sell later)
        book.call_tool('call_api', {'operationId': 'krstockOrderCancel', 'input': {'org_mkt_orr_no': number}})
    return {'number': number, 'filled': filled[0] if filled else 0, 'fill_price': filled[1] if filled else None,
            'error': None if number else result.get('error')}


# ── sessions ─────────────────────────────────────────────────────────────────
# 11/17 18:11 a SIGTERM restart left the evening session without nxt_stopped. The handler only raises a flag: the cycle
# in progress finishes (no half-written journal line), the pause ends at once and the session records why it stopped.
TERMINATED = threading.Event()


def terminate(signum, frame):
    TERMINATED.set()


def stop_fields():
    return {'reason': 'terminated'} if TERMINATED.is_set() else {}


def mark_running(journal, session, now):
    write_json(journal.root / 'session.json', {'pid': os.getpid(), 'session': session, 'started': now.isoformat()}, indent=None)  # running() reads it from other processes


def running(root):
    """The session that is running now ({'pid', 'session', 'started'}), or None."""
    try:
        data = json.loads((Path(root) / 'session.json').read_text())
        os.kill(data['pid'], 0)
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def decide(read, journal, book, now, m, s=SETTINGS, next_open=None):
    """The one entry decision of the evening: every candidate's features and failure codes, buys for those passing."""
    from nh_paper_isolated import next_session_open
    day = now.strftime('%Y%m%d')
    open_next = (next_open or next_session_open)(now.date())
    held = set(book.holdings())
    bought = 0
    for c in candidates():
        krx = quote(read, c['code'], 'KRX')
        nxt = quote(read, c['code'], 'NXT')
        if not krx or not nxt:
            journal.write('nxt_decision', now, symbol=c['code'], name=c['name'], passed=False,
                          reasons=['not_nxt_eligible' if krx else 'quote_unavailable'])
            continue
        f = features(krx, nxt)
        reasons = market_gate(m, market_of(c['index']), s) + stock_gate(f, s)
        if not open_next:
            reasons.append('market_closed_next_day')
        if c['code'] in held:
            reasons.append('already_held')
        if not reasons and len(held) + bought >= s['max_positions']:
            reasons.append('position_limit')
        qty = 0
        if not reasons:
            ask = f['nxt_ask']
            qty = s['size_krw'] // ask or (1 if ask <= s['single_share_max_krw'] else 0)
            if not qty:
                reasons.append('price_too_high')
        journal.write('nxt_decision', now, symbol=c['code'], name=c['name'], index=market_of(c['index']),
                      passed=not reasons, reasons=reasons, features=f, decision_price=f['nxt_ask'])
        if not reasons:
            result = order(book, 'BUY', c['code'], qty, f['nxt_ask'])
            journal.write('nxt_order', now, symbol=c['code'], side='BUY', qty=qty, price=f['nxt_ask'], **result)
            bought += 1 if result['filled'] else 0
    journal.write('nxt_decision_done', now, day=day, bought=bought, next_session_open=open_next)


def swing_signals(read, journal, now, m, s=SETTINGS):
    """Swing positions of the shared paper runner that the NXT after-market would sell (shadow only: paper cannot trade NXT)."""
    from nh_paper_isolated import exit_plans
    fut = m.get('futures') or {}
    night = m.get('night') or {}
    worst = min([fut[k]['pct'] for k in ('nasdaq', 'sp500') if k in fut] + ([night['pct']] if night.get('available') else []), default=None)
    for code in exit_plans(SHARED):
        if code in ETF_CODES:
            continue
        nxt = quote(read, code, 'NXT')
        if not nxt:
            continue
        sell = worst is not None and worst <= s['swing_exit_futures']
        journal.write('nxt_swing_signal', now, symbol=code, sell=sell, worst_futures_pct=worst, nxt_bid=nxt.get('bidp'))


def evening(read, journal, now_fn, s=SETTINGS, sleep=time.sleep):
    book = broker(read, journal.root, clock=now_fn)
    journal.write('nxt_started', now_fn(), session='evening')
    mark_running(journal, 'evening', now_fn())
    last_snapshot, failures = None, 0
    while True:
        now = now_fn()
        if hm(now) >= s['after_end'] or (journal.root / 'STOP').exists() or TERMINATED.is_set():
            break
        try:
            day = now.strftime('%Y%m%d')
            due = last_snapshot is None or now - last_snapshot >= timedelta(minutes=s['snapshot_minutes'])
            in_window = s['entry_start'] <= hm(now) < s['entry_end'] and not journal.last('nxt_decision_done', day)
            if due or in_window:
                m = macro(now)
                journal.write('nxt_snapshot', now, macro=m)
                last_snapshot = now
                if in_window:
                    decide(read, journal, book, now, m, s)
                    swing_signals(read, journal, now, m, s)
            failures = 0
        except Exception as exc:  # one bad cycle (network, NH) is journaled; ten in a row end the session
            failures += 1
            journal.write('nxt_error', now, session='evening', error=f'{type(exc).__name__}: {exc}'[:300])
            if failures >= 10:
                break
        sleep(30)
    journal.write('nxt_stopped', now_fn(), session='evening', **stop_fields())
    (journal.root / 'session.json').unlink(missing_ok=True)


def label(read, journal, now, decision_day, at):
    """Outcome of every candidate evaluated at the last entry decision, bought or not (the learning label)."""
    market = 'NXT' if hm(now) < SETTINGS['premarket_end'] else 'KRX'
    late = now.hour * 60 + now.minute - int(at[:2]) * 60 - int(at[3:])
    flag = {'late_min': late} if late > 1 else {}  # a restart can take the label minutes after its slot; keep it but mark it
    done = {o['symbol'] for o in journal.rows('nxt_outcome', now.strftime('%Y%m%d'))
            if o.get('at') == at and o.get('decision_day') == decision_day}
    missing = 0
    for d in journal.rows('nxt_decision', decision_day):
        base = d.get('decision_price')
        if not base or d['symbol'] in done:
            continue
        q = quote(read, d['symbol'], market)
        if q:
            journal.write('nxt_outcome', now, symbol=d['symbol'], decision_day=decision_day, at=at, market=market,
                          price=q['stck_prpr'], ret_pct=round((q['stck_prpr'] / base - 1) * 100, 2), passed=d.get('passed'), **flag)
        else:
            missing += 1
    return missing  # unread quotes are retried on the next pass instead of dropping the label


def exits(book, journal, now, s=SETTINGS, deadline=False):
    """Next-day exits of the virtual buys: +1% half, +2% all, -2% stop; everything left at the deadline."""
    for code, (qty, avg) in book.holdings().items():
        bid, _ = book.quote(code)
        pnl = (bid / avg - 1) * 100
        sold_half = any(r.get('symbol') == code and r.get('reason') == 'take_profit_half'
                        for r in journal.rows('nxt_exit', now.strftime('%Y%m%d')))
        reason, size = None, qty
        if deadline:
            reason = 'deadline'
        elif pnl <= -s['stop_pct']:
            reason = 'stop_loss'
        elif pnl >= s['all_pct']:
            reason = 'take_profit_all'
        elif pnl >= s['half_pct'] and not sold_half and qty > 1:
            reason, size = 'take_profit_half', qty // 2
        if reason:
            result = order(book, 'SELL', code, size, bid)
            journal.write('nxt_exit', now, symbol=code, reason=reason, qty=size, price=bid, avg=round(avg, 2),
                          pnl_pct=round(pnl, 2), **result)


def morning(read, journal, now_fn, s=SETTINGS, sleep=time.sleep, previous_day=None):
    book = broker(read, journal.root, clock=now_fn)
    start = now_fn()
    decision_day = previous_day or (journal.last('nxt_decision_done') or {}).get('day')
    journal.write('nxt_started', start, session='morning', decision_day=decision_day)
    mark_running(journal, 'morning', start)
    labelled, failures = set(), 0
    # NH drops some quotes in a burst of label reads; the last slot gets a few minutes to retry them
    grace = (datetime.strptime(s['exit_deadline'], '%H:%M') + timedelta(minutes=5)).strftime('%H:%M')
    labels_done = lambda now: not decision_day or decision_day == now.strftime('%Y%m%d') or labelled >= set(s['label_times'])
    while True:
        now = now_fn()
        if ((hm(now) > s['exit_deadline'] and (labels_done(now) or hm(now) > grace)) or (journal.root / 'STOP').exists()
                or TERMINATED.is_set()):
            break
        if s['premarket_end'] <= hm(now) < s['krx_open']:  # NXT pauses, KRX is in its opening auction
            sleep(20)
            continue
        try:
            if decision_day and decision_day != now.strftime('%Y%m%d'):
                for at in s['label_times']:
                    if at <= hm(now) and at not in labelled and not label(read, journal, now, decision_day, at):
                        labelled.add(at)
            exits(book, journal, now, s, deadline=hm(now) >= s['exit_deadline'])
            failures = 0
            if hm(now) >= s['exit_deadline'] and labels_done(now):
                break
        except Exception as exc:
            failures += 1
            journal.write('nxt_error', now, session='morning', error=f'{type(exc).__name__}: {exc}'[:300])
            if failures >= 10:
                break
        sleep(30)
    journal.write('nxt_stopped', now_fn(), session='morning', open_positions=len(book.holdings()), **stop_fields())
    (journal.root / 'session.json').unlink(missing_ok=True)


# ── audit ────────────────────────────────────────────────────────────────────
def audit(root=ROOT):
    """Cross-check the trail, the queryable copy and the virtual book; problems are listed, never repaired."""
    root = Path(root)
    problems, report = [], {}
    try:
        lines = [json.loads(l) for l in (root / 'events.jsonl').read_text().splitlines() if l.strip()]
    except OSError:
        return {'ok': False, 'problems': ['no events.jsonl yet']}
    db = sqlite3.connect(f'file:{root / "nxt.sqlite3"}?mode=ro', uri=True)
    if len(lines) != db.execute('SELECT count(*) FROM events').fetchone()[0]:
        problems.append('events.jsonl and nxt.sqlite3 counts differ')
    days = sorted({l['time'][:10] for l in lines})
    live = running(root)
    for day in days:
        rows = [l for l in lines if l['time'].startswith(day)]
        count = lambda e: sum(1 for l in rows if l['event'] == e)
        report[day] = {e: count(e) for e in ('nxt_snapshot', 'nxt_decision', 'nxt_order', 'nxt_exit', 'nxt_outcome', 'nxt_error')}
        report[day]['terminated'] = sum(1 for l in rows if l['event'] == 'nxt_stopped' and l.get('reason') == 'terminated')
        if count('nxt_decision') and not count('nxt_snapshot'):
            problems.append(f'{day}: decisions without a market snapshot')
        open_sessions = count('nxt_started') - count('nxt_stopped')
        if live and day == days[-1] and open_sessions == 1:
            report[day]['running'] = live['session']  # started, not finished yet: not a problem
        elif open_sessions:
            problems.append(f'{day}: a session did not stop cleanly')
    finished = {l['time'][:10] for l in lines if l['event'] == 'nxt_stopped' and l.get('session') == 'morning'
                and l['time'][11:16] >= SETTINGS['exit_deadline']}
    for day_, decision_day in {(l['time'][:10], l.get('decision_day')) for l in lines
                               if l['event'] == 'nxt_started' and l.get('session') == 'morning' and l.get('decision_day')}:
        if day_ not in finished:
            continue  # the morning is still running (or was cut short and restarted)
        decided = {l['symbol'] for l in lines if l['event'] == 'nxt_decision' and l.get('decision_price')
                   and l['time'][:10].replace('-', '') == decision_day}
        for at in SETTINGS['label_times']:
            got = {l['symbol'] for l in lines if l['event'] == 'nxt_outcome' and l.get('at') == at and l.get('decision_day') == decision_day}
            if decided - got:
                problems.append(f'{decision_day} {at}: no label for {sorted(decided - got)}')
    book = root / 'virtual-account.sqlite3'
    if book.exists():
        v = sqlite3.connect(f'file:{book}?mode=ro', uri=True)
        unfilled = v.execute('SELECT count(*) FROM orders WHERE filled<qty AND cancelled=0').fetchone()[0]
        if unfilled:
            problems.append(f'{unfilled} virtual orders neither filled nor cancelled')
        orders = v.execute('SELECT count(*) FROM orders').fetchone()[0]
        journaled = sum(1 for l in lines if l['event'] in ('nxt_order', 'nxt_exit') and l.get('number'))
        if orders != journaled:
            problems.append(f'virtual orders {orders} != journaled orders {journaled}')
        holdings = v.execute("SELECT symbol, SUM(CASE side WHEN 'BUY' THEN filled ELSE -filled END) FROM orders GROUP BY symbol "
                             "HAVING SUM(CASE side WHEN 'BUY' THEN filled ELSE -filled END) != 0").fetchall()
        last_morning = max((l['time'] for l in lines if l['event'] == 'nxt_stopped' and l.get('session') == 'morning'), default='')
        last_buy = max((l['time'] for l in lines if l['event'] == 'nxt_order'), default='')
        if holdings and last_morning > last_buy:
            problems.append(f'positions left after the morning exit: {holdings}')
    return {'ok': not problems, 'problems': problems, 'days': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--session', choices=('auto', 'evening', 'morning'), default='auto')
    parser.add_argument('--audit', action='store_true')
    args = parser.parse_args()
    if args.audit:
        print(json.dumps(audit(), ensure_ascii=False, indent=1))
        return
    from src.config.loader import load_agent_config
    from src.tools.mcp import MCPServerAdapter
    now_fn = lambda: datetime.now(KST)
    now = now_fn()
    if now.weekday() >= 5:
        return
    session = args.session
    if session == 'auto':
        # launchd also starts this at boot (RunAtLoad, 11/17): the Mac rebooted at 08:07 on 11/13 and 08:08 on 11/16 and
        # the 07:55 run never came, so the morning labels of the 11/13 decisions are missing. Outside both windows: nothing.
        clock = hm(now)
        if BOOT_WINDOWS['morning'][0] <= clock < BOOT_WINDOWS['morning'][1]:
            session = 'morning'
        elif BOOT_WINDOWS['evening'][0] <= clock < BOOT_WINDOWS['evening'][1]:
            session = 'evening'
        else:
            return
    read = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
    signal.signal(signal.SIGTERM, terminate)  # launchd stop or restart: record the stop, then leave
    (evening if session == 'evening' else morning)(read, Journal(), now_fn, sleep=TERMINATED.wait)


if __name__ == '__main__':
    main()
