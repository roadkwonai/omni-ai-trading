"""The long-term engine on a live account (user 2026-11-17, docs/longterm-live-account-plan.md).

A live account whose accounts.json engine is 'longterm' follows vibe_longterm's plan of the day, not the Jev mirror:
the first cycle after the engine's 08:45 decision sizes the day's orders with the engine's own plan_orders() on THIS
account's capital and holdings (this account's strategy modules, the first deployment spread over the engine's deploy_weeks, the monthly band, the monthly sell and daily buy caps),
then sends them one at a time through nh_guarded_live.execute (LiveOrderGuard: capital, price cap, per-order limit,
5 new symbols a day, no new order while one is open, HALT). Sells come first.

Long-term protection (user choice "장기 전용 보호"): no per-symbol stop; the daily loss latch counts only today's realized
loss and fees (accounts.live_loss_realized_only). Orders go out only with --execute, the account's automation switch
and its live-order switch; otherwise the day's plan is recorded (DRY_RUN) and nothing is sent.
Log: nhplug/longterm-live.jsonl. Day state per account: nhplug/longterm-live/state-<key>.json.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import api_calls  # noqa: F401 - counts this process's external API calls (dashboard 'API 연동')
import longterm_dca
import longterm_watchlist
import vibe_longterm as L
import yeo_method

KST = ZoneInfo('Asia/Seoul')
LOG = 'longterm-live.jsonl'
START, END = '09:40', '14:30'  # new orders: the engine trades its virtual book from 09:40
STOP_AT = '14:40'  # open orders are still reconciled or cancelled until then
CHECK_INTERVAL_SECONDS = 60
QUOTE_RETRY_WAIT = (0.5, 1.0)  # a quote refused for a moment (NH 429 when processes burst, 2026-11-18 09:40) is asked again
CASH = {'BUY': 'krstockOrderCashBuy', 'SELL': 'krstockOrderCashSell'}
UNSENT = ('planned', 'dry_run')
COMMITS = {'plan': 'book', 'dca': 'dca'}  # an order's source → the day state that becomes real once the order is sent
# guard refusals that clear on their own (HALT, the buy pause, open orders, a stale or moved quote, a broker read
# failure): the order stays planned and is tried again until END. Any other refusal (a limit, the price cap,
# ownership, the loss latch, an uncertain send) is final for the day.
TRANSIENT = frozenset({
    'live kill switch active', 'new live buys are paused', 'broker has outstanding orders', 'unresolved order blocks submission',
    'market data stale or market closed', 'limit exceeds current ask or ask unavailable', 'sell limit too far below current bid',
    'quote symbol mismatch', 'quote symbol mismatch or bid unavailable for sell',
    'strategy or market data expired or changed before send',  # aborted before the order request (AISignalBeforeSend)
    'broker rate limit', 'broker request failed', 'sellable unavailable'})


def state_path(root, key):
    return Path(root) / 'longterm-live' / f'state-{key}.json'


def load_state(root, key):
    try:
        data = json.loads(state_path(root, key).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(root, key, state):
    from config_store import write_json
    path = state_path(root, key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(path, state)


def to_orders(planned, held, quotes, res=None):
    """Signed KRW per asset (an engine ETF key or a 관심목록 code) → whole-share orders at the bid (sell) or ask (buy, the
    fee on top), sells first. res: the account's reservoir ETF (L.reservoir_code)."""
    orders = []
    for k, krw in sorted(planned.items(), key=lambda item: item[1]):
        code, (bid, ask) = L.code_of(k, res), quotes[k]
        side, price = ('SELL', bid) if krw < 0 else ('BUY', ask)
        qty = min(int(abs(krw) // price), held.get(code, 0)) if side == 'SELL' else int(krw // (price * (1 + L.FEE)))
        if qty > 0:
            orders.append({'key': k, 'code': code, 'side': side, 'qty': qty, 'price': price, 'krw': round(krw),
                           'status': 'planned', 'source': 'plan'})
    return orders


def filled_on(root, key, day, side):
    """{code: shares filled} on `day` for one side ('BUY' or 'SELL') in this live account, from the recorded fills (live_fills)."""
    from live_fills import rows
    out = {}
    for r in rows(root, key):
        if r['time'][:10] == day and r['side'] == side:
            out[r['symbol']] = out.get(r['symbol'], 0) + r['qty']
    return out


def settle_sold(root, key, before, book):
    """The book with the month's sell total (버핏 월 매도 상한) counting only what filled of the last live day's plan sells:
    a sell counts when sent (so the day's later plan never oversells), and a part that did not fill is taken back the next
    day, as the virtual book counts fills (user 2026-11-18)."""
    sold, day = book.get('sold') or {}, before.get('day') or ''
    sent = [o for o in before.get('orders') or [] if o.get('sent') and o['side'] == 'SELL' and 'plan' in o['source'] and o['key'] != 'RES']
    if not before.get('execute') or not sent or sold.get('month') != day[:7]:
        return book
    filled = filled_on(root, key, day, 'SELL')
    unfilled = 0
    for o in sent:  # one plan sell per code a day: its fills are the code's fills
        got = min(o['sent']['qty'], filled.get(o['code'], 0))
        filled[o['code']] = filled.get(o['code'], 0) - got
        unfilled += (o['sent']['qty'] - got) * o['sent']['price']
    return {**book, 'sold': {**sold, 'krw': max(0, sold['krw'] - unfilled)}} if unfilled else book


def yeo_owed(root, key, before, yeo):
    """{asset key: KRW} the last live day's 여운봉 step and rebalance buys did not buy (refused, cut, cancelled unfilled),
    read from that day's orders against its fills: the whole day's book was made real at its first send, so the method
    buys the rest first on the next plan (review 2026-11-18). {} for a dry day or another strategy."""
    if not yeo or not before.get('execute') or not before.get('day'):
        return {}
    filled = filled_on(root, key, before['day'], 'BUY')
    keys = set(yeo_method.keys(yeo))
    rows = [{**o, 'filled': min(o['qty'], filled.get(o['code'], 0))} for o in before.get('orders') or []
            if 'plan' in o.get('source', '') and o['key'] in keys]
    return yeo_method.unbought(rows, steps=yeo_method.step_buys((before.get('book') or {}).get('yeo'), before['day']))


def guard_capital(capital, summary, rows):
    """The capital the plan may fill: the account's capital less the holdings' unrealized loss, because the guard refuses a
    buy once what the holdings cost (tot_byn_amt) plus the order passes the capital, while the plan values them at the
    price. A gain is not added: the guard would still stop at the cost."""
    cost, held = summary.get('tot_byn_amt'), [r for r in rows if type(r.get('rsdl_qty')) is int and r['rsdl_qty'] > 0]
    if type(cost) is not int or not held or any(type(r.get('now_pr')) not in (int, float) for r in held):
        return capital  # a price missing: no correction rather than a wrong one
    return capital - max(0, cost - sum(r['rsdl_qty'] * r['now_pr'] for r in held))


def values_of(held, quotes, res=None):
    """KRW per held key: the engine's ETFs ('RES': the account's reservoir ETF res), and any other quoted key held (a 여운봉
    매매법 ETF)."""
    out = {k: held.get(L.code_of(k, res), 0) * quotes[k][0] for k in L.ASSETS}
    return out | {L.key_of(c): q * quotes[L.key_of(c)][0] for c, q in held.items() if L.key_of(c) in yeo_method.EXTRA and L.key_of(c) in quotes}


def capital_logged(key, capital):
    """This account's 운용금 now is the newest one the settings history recorded (accounts kind, <key>.budget.capital_krw):
    입금할 때 takes only a 운용금 change saved through config_store as a deposit (docs/longterm-dca-deposit-plan.md 3-1)."""
    import sqlite3
    import config_store
    from sqlite_ro import ro_connect
    try:
        with ro_connect(config_store.history_path()) as db:
            row = db.execute("SELECT new FROM changes WHERE kind='accounts' AND key=? ORDER BY rowid DESC LIMIT 1",
                             (f'{key}.budget.capital_krw',)).fetchone()
    except (OSError, sqlite3.Error):
        return False
    try:
        return row is not None and json.loads(row[0]) == capital
    except (TypeError, ValueError):
        return False


def take_deposit(cfg, dca, record, key, day):
    """(the 적립식 state, notes) after 입금할 때 reads this account's 운용금 (longterm_dca.take_deposit); unchanged otherwise."""
    if not cfg or cfg.get('frequency') != 'deposit':
        return dca, []
    capital = record['budget']['capital_krw']
    seen = (dca or {}).get('capital_seen')
    logged = seen == capital or type(seen) not in (int, float) or capital_logged(key, capital)
    new, note = longterm_dca.take_deposit(cfg, dca, capital, day, logged=logged)
    return new, [note] if note else []


def dca_want(day_state, plan, yeo=None):
    """What this account's 적립식 buys: its strategy's invested part (L.dca_targets: a 여운봉 account its pair, else the
    day's target without KOFR)."""
    return L.dca_targets(L.today_want(plan, (day_state.get('info') or {}).get('want')), yeo)


def dca_orders(cfg, day_state, now, plan, held, quotes, cash, capital, yeo=None, stories=None, res=None):
    """적립식 for a live account merged into the day's unsent orders: (the new unsent orders, new 적립식 state, notes,
    why nothing of it goes out or None). No deposit is simulated: it spends the account's free cash within its capital,
    less today's unsent buys (not in the broker cash yet), on the engine's target of the day (the chosen 관심목록 codes
    with it; the caller takes the 관심목록 holdings out of the capital). A part under one share is carried to the next
    contribution. An ETF with an unsent order still gets one order (the guard refuses a second identical order as a
    duplicate), and sells go first. A contribution whose parts are all carried is recorded at once; one whose orders still come to no whole share (netted
    against the engine's unsent sell) is not recorded: the 적립식 state stays as it was."""
    before = day_state.get('dca') or {}
    unsent = [o for o in day_state.get('orders') or [] if o['status'] in UNSENT]
    values = values_of(held, quotes, res)
    claimed = sum(o['qty'] * o['price'] for o in unsent if o['side'] == 'BUY')
    room = max(0, min(cash, capital - sum(values.values())) - claimed)
    want = dca_want(day_state, plan, yeo)
    planned, new_state, notes = longterm_dca.plan(cfg, before, now, plan.get('indicators') or {}, plan.get('regime'), want,
                                                  values, room, shares={L.key_of(c): q for c, q in held.items()},
                                                  prices={k: q[1] for k, q in quotes.items()}, yeo=yeo is not None,
                                                  stories=stories)
    net, sources = {}, {}
    for k, krw, source in [(o['key'], o['krw'], o['source']) for o in unsent] + [(k, v, 'dca') for k, v in planned.items()]:
        net[k] = net.get(k, 0) + krw
        sources.setdefault(k, set()).update(source.split('+'))
    orders = to_orders(net, held, quotes, res)
    for o in orders:
        o['source'] = '+'.join(s for s in COMMITS if s in sources[o['key']])
    if any('dca' in o['source'] for o in orders):
        return orders, new_state, notes, None
    if planned:  # no whole share once netted: nothing bought, nothing recorded
        return orders, before, notes, 'no whole share at the current price or nothing held'
    short = (longterm_dca.due_today(cfg, before, now, plan.get('indicators') or {}, plan.get('regime'), want, {k: q[1] for k, q in quotes.items()})
             and new_state.get('last_contribution') != now.date().isoformat())
    # plan's own reason when it is not money (no ETF to buy, a missing quote); money keeps its code
    why = next((n for n in notes if '건너뜀' in n and '여유 자금' not in n), 'dca: not enough free money')
    return orders, new_state, notes, why if short else None


def day_orders(plan, stories, book, held, quotes, cash, capital, day, modules=None, yeo=None, res=None):
    """(orders, info, new book state) for one live account: the engine's plan_orders() on this account's money, with the
    account's own master modules (modules, 계좌별 전략 2026-11-18; None = the engine's of the day), or the 여운봉 저수지 매매법
    (yeo: its setting; its own ETFs must be in quotes).
    held {code: qty}; quotes {asset key: (bid, ask)}; the account's long-term NAV is its holdings plus the cash it may
    still use under its capital. Orders are whole shares at the bid (sell) or ask (buy), sells first."""
    values = values_of(held, quotes, res)
    if yeo is not None:  # its ETFs not held yet still count (zero) so the method sees every key it trades
        values |= {k: 0.0 for k in yeo_method.keys(yeo) if k in quotes and k not in values}
    invested = sum(values.values())
    nav = max(invested, min(capital, invested + max(cash, 0)))
    free = max(0, nav - invested)
    if nav <= 0:
        return [], {'nav': 0, 'why': '운용금 없음'}, book
    modules = [] if yeo is not None else list(plan['active'] if modules is None else modules)
    if yeo is not None:  # the steps and the 70 : 30 rebalance on this account's money (L.yeo_orders, the engine's same rule)
        want, target, why, notes, planned, new_book = L.yeo_orders(plan, yeo, book, values, nav, free, day)
    else:
        want, target, why, notes, planned, new_book = L.plan_orders(plan, modules, book, stories, values, nav, free, day)
    orders = to_orders(planned, held, quotes, res)
    info = {'nav': round(nav), 'invested': round(invested), 'weights': {k: round(v / nav, 4) for k, v in values.items()},
            'want': want, 'target': target, 'why': L.why_label(why), 'notes': [text for _, text in notes], 'modules': modules}
    return orders, info, new_book


def sends_today(guard, day, code, side):
    """How many orders the guard let out today for this code and side (a sell cancelled unfilled excluded: it may be sent
    again as its replacement; an intent aborted before sending excluded)."""
    op = 'krstockOrderCashSell' if side == 'SELL' else 'krstockOrderCashBuy'
    with closing(guard._db()) as db:
        return db.execute("SELECT COUNT(*) FROM live_intents i WHERE scope=? AND day=? AND symbol=? AND operation=? "
                          "AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts) "
                          "AND NOT (filled=0 AND state='terminal' AND fingerprint IN (SELECT fingerprint FROM sell_cancellations))",
                          (guard.scope, day.replace('-', ''), code, op)).fetchone()[0]


def open_order(guard):
    """The guard still has an order of this account to reconcile (an open or uncertain intent)."""
    with closing(guard._db()) as db:
        return db.execute("SELECT 1 FROM live_intents WHERE scope=? AND state!='terminal'", (guard.scope,)).fetchone() is not None


def cycle(reader, account, credentials, record, key, *, now, execute_orders, may_cancel, root, engine_root=L.ROOT, auto=True):
    """One pass for one long-term live account: reconcile, plan the day once, then send the next planned order. auto False
    (the account's 자동매매 off): only this executor's own open orders are reconciled and cancelled, nothing is planned."""
    from accounts import DEFAULT_BUDGET, LIVE_PRICE_HARD_CAP, live_limits
    from live_fills import record as record_fills
    from nh_guarded_live import execute
    from nh_live_broker import collect_snapshots
    from nh_live_follow import bid_ask, cancelled_sell, pending_step
    from src.tools.nhplug_live_guard import ORDER_QTY_MAX, LiveOrderBlocked, LiveOrderGuard
    hhmm, day = now.strftime('%H:%M'), now.date().isoformat()
    from session_times import shifted  # the exchange clock (수능 day 2026-11-19 opens 10:00)
    start, end = shifted(START, now.date()), shifted(END, now.date())
    if hhmm < start:
        return {'action': 'WAIT', 'reason': f'before {start}'}
    plan = L.read_state(engine_root, ('plan',)).get('plan') or {}
    if plan.get('day') != day:
        return {'action': 'WAIT', 'reason': 'no engine plan today (08:45 decision missing)'}
    info = {'regime': plan.get('regime'), 'engine_mode': plan.get('mode')}  # 'mode' is the log's execute/dry_run
    state = load_state(root, key)
    # a dry plan of today is made again once live orders are on (a dry day never sends anything)
    fresh = state.get('day') != day or (execute_orders and not state.get('execute'))
    cfg = L.load_settings(engine_root)['dca'].get(key)
    yeo = L.yeo_settings(L.load_settings(engine_root), key)  # 여운봉 저수지 매매법: its own ETFs are quoted and ordered too
    yeo_keys = [k for k in yeo_method.keys(yeo) if k not in L.ASSETS] if yeo else []
    cfg = cfg and longterm_watchlist.for_dca(cfg, engine_root)
    dca_due = bool(cfg and cfg['on'] and hhmm >= longterm_dca.start_time(cfg) and longterm_dca.auto_look(cfg, now)
                   and state.get('dca_check') != day and 'dca_next' not in state)
    alert_due = bool(cfg and not cfg['on'] and cfg.get('alert') and hhmm >= longterm_dca.start_time(cfg) and longterm_dca.auto_look(cfg, now)
                     and state.get('alert_check') != day and state.get('day') == day)  # 적립 알림 (2026-11-20): after the day's plan
    sendable = execute_orders and any(o['status'] == 'planned' for o in state.get('orders') or [])
    idle = 'today done' if state.get('execute') else 'plan only (dry run)'
    guard = LiveOrderGuard(account, root, symbols=set(L.CODE_KEY) | set(longterm_dca.sleeve(cfg) if cfg else ()) | {L.code_of(k) for k in yeo_keys})
    if not auto and not open_order(guard):
        return {'action': 'WAIT', 'reason': 'automation off'}
    if hhmm <= end and not (fresh or sendable or dca_due or alert_due) and not open_order(guard):
        return {'action': 'HOLD', 'reason': idle, **info}  # nothing to reconcile, plan, check or send: no broker reads
    snapshots = collect_snapshots(reader, account, now)
    record_fills(root, key, snapshots['history'], now)
    from live_fills import record_balance
    record_balance(root, key, snapshots['balance'], now)  # account totals for Discord and the dashboard (user 2026-11-18)
    pending = pending_step(reader, guard, credentials, account, snapshots, info, may_cancel)
    if pending is not None:
        return pending
    if not auto:
        return {'action': 'WAIT', 'reason': 'automation off', **info}
    if hhmm > end:
        return {'action': 'HOLD', 'reason': f'order window closed ({end})', **info}
    rows = [r for r in snapshots['balance']['Output_1'] if isinstance(r, dict)]
    held = {r['iem_cd']: r['rsdl_qty'] for r in rows if isinstance(r.get('iem_cd'), str) and type(r.get('rsdl_qty')) is int and r['rsdl_qty'] > 0}
    cash = (snapshots['balance'].get('Output_0') or {}).get('orr_pbl_amt1')
    cash, capital = cash if type(cash) is int else 0, guard_capital(record['budget']['capital_krw'], snapshots['balance'].get('Output_0') or {}, rows)
    # the 관심목록 sleeve (outside the engine's ETFs, the 적립식's own) at the balance's price: inside the capital, never the
    # engine's to trade; the guard's capital check counts every holding as well
    outside = sum(r['rsdl_qty'] * next((v for v in (r.get('now_pr'), r.get('phs_pr')) if type(v) in (int, float) and v > 0), 0)
                  for r in rows if r.get('iem_cd') in held and r['iem_cd'] not in L.CODE_KEY and r['iem_cd'] not in {L.code_of(k) for k in yeo_keys})
    res = L.reservoir_code(L.load_settings(engine_root), key, record['budget']['capital_krw'], held)  # 저수지 ETF (11-19)
    if fresh or dca_due or alert_due:
        quotes = {}
        def ask_again(code):
            quote = bid_ask(reader, code)
            for wait in QUOTE_RETRY_WAIT:
                if quote is not None:
                    break
                time.sleep(wait)
                quote = bid_ask(reader, code)
            return quote
        for k in [*L.ASSETS, *yeo_keys]:
            if (quote := ask_again(L.code_of(k, res))) is None:
                return {'action': 'WAIT', 'reason': f'quote unavailable {L.code_of(k, res)}', **info}
            quotes[k] = quote
        for code in longterm_dca.sleeve(cfg) if dca_due else ():  # a chosen code without a quote is not bought today
            if code not in quotes and (quote := bid_ask(reader, code)) is not None:
                quotes[code] = quote
    execution = 'PLANNED' if execute_orders else 'DRY_RUN'
    if fresh:
        stories = (L.read_state(engine_root, ('story',)).get('story') or {}).get('stories') or {}
        book, dca = state.get('book') or {}, state.get('dca') or {}
        if state.get('day') != day:  # the last live day against its fills
            book = settle_sold(root, key, state, book)
            if owed := yeo_owed(root, key, state, yeo):
                book = {**book, 'yeo_owed': owed}
        dca, deposit = take_deposit(cfg, dca, record, key, day)  # 입금할 때: a raised 운용금 is the 적립식's, not the strategy's
        waiting = longterm_dca.reserved(dca)  # the 적립식's money: waiting for a drawdown step, carried parts under one share
        settings = L.load_settings(engine_root)  # 계좌별 전략: this account's master modules; regime, stories and indicators are the engine's
        orders, plan_info, new_book = day_orders(plan, stories, book, held, quotes, cash - waiting,
                                                 capital - outside - waiting, day, L.book_modules(settings, key, plan), yeo, res)
        plan_info['strategy'] = '이 계좌 전략: ' + L.book_strategy_label(settings, key)
        plan_info['reservoir'] = res
        if deposit:
            plan_info['deposit'] = deposit
        if not execute_orders:
            for order in orders:
                order['status'] = 'dry_run'
        state = {'day': day, 'execute': execute_orders, 'orders': orders, 'info': plan_info, 'book': book, 'dca': dca,
                 'dca_alert': state.get('dca_alert') or {}}
        if 'sold' in new_book:  # the month's sell room counts the sells as they are sent (below), not as planned
            prior = book.get('sold') or {}
            new_book['sold'] = dict(prior) if prior.get('month') == day[:7] else {'month': day[:7], 'krw': 0}
        if execute_orders:  # the engine's book (first-deployment steps, monthly band, sell caps) moves once an order is sent
            state['book_next' if orders else 'book'] = new_book
        save_state(root, key, state)
        return {'action': 'PLAN', 'orders': orders, **plan_info, **info, 'execution': execution}
    if alert_due and not sendable:  # 적립 알림: what the 적립식 would buy today, logged for Discord; never an order
        prices = {k: q[1] for k, q in quotes.items()}
        if not longterm_dca.ready(cfg, plan.get('indicators') or {}, dca_want(state, plan, yeo), prices, now):
            return {'action': 'WAIT', 'reason': 'dca alert: automatic time waits for a price at or under yesterday close', **info}
        planned, new_alert, notes = longterm_dca.alert(cfg, state.get('dca_alert') or {}, now, plan.get('indicators') or {}, plan.get('regime'),
                                                       dca_want(state, plan, yeo), prices)
        state['dca_alert'], state['alert_check'] = new_alert, day
        save_state(root, key, state)
        rows = [{'key': k, 'code': L.code_of(k, res), 'name': L.asset_name(k), 'krw': round(v), 'price': prices[k],
                 'qty': int(v // (prices[k] * (1 + L.FEE)))} for k, v in planned.items() if prices.get(k)]
        rows = [r for r in rows if r['qty'] > 0]
        if rows:
            return {'action': 'DCA_ALERT', 'orders': rows, 'notes': notes, **info, 'execution': 'ALERT'}
        return {'action': 'HOLD', 'reason': 'dca alert: not a buy day', 'notes': notes, **info}
    if dca_due and not longterm_dca.ready(cfg, plan.get('indicators') or {}, dca_want(state, plan, yeo),
                                          {k: q[1] for k, q in quotes.items()}, now):
        return {'action': 'WAIT', 'reason': 'dca: automatic time waits for a price at or under yesterday close', **info}
    if dca_due:  # 적립식, once a day at its time: what is short of money or under one share waits for the next trading day
        state['dca'], deposit = take_deposit(cfg, state.get('dca') or {}, record, key, day)  # a 운용금 raised during the day
        merged, new_dca, notes, skipped = dca_orders(cfg, state, now, plan, held, quotes, cash, capital - outside, yeo,
                                                     (L.read_state(engine_root, ('story',)).get('story') or {}).get('stories'), res)
        for order in merged:
            order['status'] = 'planned' if state['execute'] else 'dry_run'
        state['orders'] = [o for o in state['orders'] if o['status'] not in UNSENT] + merged
        if any('dca' in o['source'] for o in merged):  # recorded as made once one of its orders is sent
            state['dca_next'] = new_dca
        elif state['execute']:
            state['dca'] = new_dca
        state['dca_check'] = day
        save_state(root, key, state)
        if skipped:  # said once: the rest of the day reads nothing from the broker for it
            return {'action': 'SKIP', 'reason': skipped, 'notes': notes, **info}
        return {'action': 'DCA', 'orders': merged, 'notes': deposit + notes, **info, 'execution': execution}
    order = next((o for o in state.get('orders') or [] if o.get('status') == 'planned'), None)
    if order is None or not execute_orders:
        return {'action': 'HOLD', 'reason': idle, **info}
    recorded = sum(1 for o in state['orders'] if o['code'] == order['code'] and o['side'] == order['side'] and o['status'] == 'sent')
    if sends_today(guard, day, order['code'], order['side']) > recorded:  # sent before a stop that lost the state save
        commit_sent(state, order)
        order['sent'] = {'qty': order['qty'], 'price': order['price']}  # the planned size: the sent one was not saved
        if order['side'] == 'SELL' and 'plan' in order['source'] and order['key'] != 'RES' and 'sold' in state['book']:
            state['book']['sold']['krw'] += order['qty'] * order['price']  # the planned size: the sent one was not saved
        save_state(root, key, state)
        return {'action': order['side'], 'symbol': order['code'], 'execution': 'SENT_BEFORE_RESTART', **info}
    quote = bid_ask(reader, order['code'])
    if quote is None:
        return {'action': 'WAIT', 'reason': f"quote unavailable {order['code']}", **info}
    code, sell = order['code'], order['side'] == 'SELL'
    price = quote[0] if sell else quote[1]
    owned = guard.owned(code) if sell else 0
    # sized to what the guard accepts, so an order is cut rather than refused whole: a sell only of the shares the
    # guard's ledger owns; a buy within ORDER_QTY_MAX, the account's per-order limit and its 1-share price cap
    qty = (min(order['qty'], held.get(code, 0), owned) if sell else
           min(order['qty'], int(abs(order['krw']) // (price * (1 + L.FEE))), ORDER_QTY_MAX, min(live_limits(record=record)['order_max_krw'], capital) // price))
    cap = min(record['budget'].get('order_price_max_krw') or DEFAULT_BUDGET['live']['order_price_max_krw'], LIVE_PRICE_HARD_CAP)
    result = {'action': order['side'], 'symbol': code, 'qty': qty, 'price': price, **info}
    if not sell:
        result['quote_gap_audit'] = {
            'symbol': code,
            'ask': price,
            'observed_at': datetime.now(KST).isoformat(),
            'source': 'NH get_stock_price',
            'previous_close': None,
            'previous_close_date': None,
            'gap_pct': None,
            'reason': 'previous_close_unverified',
            'note': '주문 계획 직전 조회 호가. 보호 경로에서 주문 전 호가를 다시 확인합니다.',
        }
    if not sell and price > cap:
        order.update(status='skipped', reason='share price above the account price cap')
    elif sell and held.get(code) and not owned:
        order.update(status='skipped', reason='held shares are not in the guard ledger (adopting them needs operator approval)')
    elif qty <= 0:
        order.update(status='skipped', reason='no whole share at the current price or nothing held')
    else:
        fields = {'act_no': account, 'iem_cd': code, 'orr_qty': qty, 'orr_pr': price, 'nmn_pr_tp_cd': '01',
                  'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00', 'rmt_mkt_cd': 'KRX', 'sor_mkt_sli_yn': 'N'}
        try:
            replaces = cancelled_sell(guard, code) if sell else None  # a sell cancelled after 60 s unfilled is re-sent as its replacement
            kwargs = {'replacement_of': replaces}
            if not sell:
                kwargs['quote_audit'] = result['quote_gap_audit']
            result['intent'] = execute(reader, guard, credentials, CASH[order['side']], fields, **kwargs)
        except LiveOrderBlocked as exc:  # a refusal that clears on its own is retried until END; any other is final
            retry = str(exc) in TRANSIENT
            order.update(status='planned' if retry else 'blocked', reason=str(exc))
            result.update(action='WAIT' if retry else order['side'], execution='RETRY' if retry else 'BLOCKED', reason=str(exc))
        else:
            commit_sent(state, order)
            order['sent'] = {'qty': qty, 'price': price}  # what went out (the plan's size may have been cut)
            if sell and 'plan' in order['source'] and order['key'] != 'RES' and 'sold' in state['book']:
                state['book']['sold']['krw'] += qty * price
            result['execution'] = 'ACKNOWLEDGED_NOT_FILLED'
    if order['status'] == 'skipped':
        result.update(action='SKIP', reason=order['reason'])
    save_state(root, key, state)
    return result


def commit_sent(state, order):
    """An order is out: it and the day state it carries (the book, the 적립식) are real now."""
    order['status'] = 'sent'
    for source in order['source'].split('+'):
        if (pending_state := state.pop(f'{COMMITS[source]}_next', None)) is not None:
            state[COMMITS[source]] = pending_state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True, help='accounts.json key of a live account set to 장기투자')
    parser.add_argument('--execute', action='store_true', help='order through nh_guarded_live (separate user approval)')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from accounts import load, resolve
    from nh_guarded_live import ENV
    from nh_live_follow import configured_accounts
    from src.config.loader import load_agent_config
    from src.config.paths import get_runtime_root
    from src.tools.mcp import MCPServerAdapter

    root = get_runtime_root() / 'nhplug'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)

    def current():
        record = load().get(args.account)
        ok = record and record.get('kind') == 'live' and not record.get('invalid') and record.get('engine') == 'longterm'
        return record if ok else None

    record = current()
    if not record:
        raise SystemExit('live account unavailable, invalid or not set to 장기투자')
    reader = MCPServerAdapter('nhplug_readonly', load_agent_config().mcp_servers['nhplug_readonly'])
    linked = reader.call_tool('list_accounts', {})
    rows = ((linked.get('data') or {}).get('Output_0') if isinstance(linked, dict) and linked.get('status') == 'ok' else None) or []
    account = resolve({args.account: record}, rows).get(args.account)
    if not account:
        raise SystemExit('live account not linked at the broker')
    values = dotenv_values(ENV, interpolate=False)
    configured = configured_accounts(values)
    if args.execute and (account not in configured or not values.get('APP_KEY') or not values.get('APP_SECRET')):
        raise SystemExit('--execute needs an account listed in .env and the app key')
    # one lock per account whatever the mode (a dry and an armed loop share state-<key>.json), the Jev mirror's execute
    # lock for this account: two loops never run on one account
    primary = configured[0] if configured else None
    name = 'live-auto.lock' if account == primary else f'live-follow-exec-{args.account}.lock'
    with (root / name).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        last = None
        while True:
            now = datetime.now(KST)
            live_now = False
            try:
                record = current()
                if record is None:
                    raise SystemExit('live account is no longer set to 장기투자')
                live_now = bool(args.execute and record['auto'] is True and record.get('live_orders') is True)
                result = cycle(reader, account, values, record, args.account, now=now, execute_orders=live_now,
                               may_cancel=args.execute, root=root, auto=record['auto'] is True)  # off: its open orders only
            except Exception as exc:  # refusals are logged; an unknown send outcome stays an uncertain intent in the guard
                from src.tools.nhplug_live_guard import LiveOrderBlocked
                result = {'action': 'BLOCK', 'error_type': type(exc).__name__,
                          **({'reason': str(exc)} if isinstance(exc, LiveOrderBlocked) else {})}
            event = {'time': now.isoformat(), 'account': args.account, 'engine': 'longterm',
                     'mode': 'execute' if live_now else 'dry_run',
                     'armed': bool(args.execute), **result}
            fold = (event.get('action'), event.get('reason'))
            if fold != last or event.get('action') not in ('WAIT', 'HOLD'):  # one line per change, not per idle minute
                with (root / LOG).open('a') as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, default=str) + '\n')
                print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
            last = fold
            if args.once or now.strftime('%H:%M') > __import__('session_times').shifted(STOP_AT, now.date()):
                break
            time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == '__main__':
    main()
