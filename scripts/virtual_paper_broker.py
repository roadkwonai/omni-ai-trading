"""Virtual NH paper broker: limit orders fill against the live quote; nothing is sent to any broker.

For a strategy without a stock-capable NH paper account (vibe-trading; user decision 2026-11-12 "가상 체결로 운용").
The paper runner's own code runs unchanged through this object and writes its normal journal (orders.sqlite3), which
the live mirror (nh_live_follow) and the dashboards read. Quotes come from the read-only NH adapter.

Fill model (optimistic, documented): a marketable limit (buy >= ask, sell <= bid) fills in full at the quoted
ask/bid; otherwise it rests and is re-checked on each order-history read until cancelled. No queue position,
partial fills or taxes; a fee (share of the traded amount) is charged only when given (vibe_longterm: 0.015%).
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
DONE = '조회가 완료되었습니다'
QUOTE_TTL = 30  # seconds a quote is reused for balance valuation


def shared_guard_limits():
    """The shared paper account's limits as saved on the dashboard (accounts.paper_limits), so a virtual book runs under
    the same numbers as the real paper guard; read on every order."""
    from accounts import load, paper_limits
    limits = paper_limits(load().get('paper-shared'))
    return {'order_krw': limits['order_max_krw'], 'daily_buy_krw': limits['daily_buy_krw'],
            'daily_new_symbols': limits['daily_new_symbols'], 'held_krw': limits['held_max_krw']}
ABORT = {'status': 'error', 'error_type': 'NHPreSendCheckFailed', 'order_safety': {'state': 'aborted_no_send', 'resubmit_allowed': False}}


def refused(reason):
    return {'status': 'error', 'error_type': 'NHOrderBlocked', 'error': f'virtual broker: {reason}'}


def page(block, rows, **extra):
    return {'status': 'ok', 'data': {'rsp_cd': '00000', 'rsp_msg': DONE, block: rows, '_nh_pagination': {'complete': True}, **extra}}


RATE_LIMIT_WAIT = (0.5, 1.0)  # seconds before each retry of a rate-limited quote (NH 429, 2026-11-18 long-term quote failures)


def rate_limited(response):
    """True for the NH MCP rate-limit error (HTTP 429 → category rate_limit / IGW42902): several processes share one app
    key and a burst of quotes is refused for a moment."""
    text = str((response or {}).get('error') or '') if isinstance(response, dict) else ''
    return 'rate_limit' in text or 'IGW42902' in text or '유량' in text


class VirtualBroker:
    def __init__(self, path, read, cash=10_000_000, clock=None, limits=None, fee=0.0):
        self.fee = fee
        from kr_swing_v5_forward import fresh_quote
        self.read, self.fresh_quote = read, fresh_quote
        self.clock = clock or (lambda: datetime.now(KST))
        self.quotes = {}
        self.limits = limits or {}
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS account (name TEXT PRIMARY KEY, value REAL NOT NULL)')
        self.db.execute('''CREATE TABLE IF NOT EXISTS orders (number INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
            symbol TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, price INTEGER NOT NULL, filled INTEGER NOT NULL,
            cancelled INTEGER NOT NULL, fill_price REAL, time TEXT NOT NULL)''')
        if 'note' not in {r[1] for r in self.db.execute('PRAGMA table_info(orders)')}:  # why a resting order has not filled
            self.db.execute('ALTER TABLE orders ADD COLUMN note TEXT')
        self.db.execute("INSERT OR IGNORE INTO account VALUES ('cash', ?)", (float(cash),))
        self.db.commit()

    def quote(self, symbol):
        response = self.read.call_tool('get_stock_price', {'stock_code': symbol})
        for wait in RATE_LIMIT_WAIT:  # a refused burst clears in a moment; anything else fails at once
            if not rate_limited(response):
                break
            time.sleep(wait)
            response = self.read.call_tool('get_stock_price', {'stock_code': symbol})
        if not isinstance(response, dict) or response.get('status') != 'ok':
            raise ValueError('quote unavailable')
        quote = self.fresh_quote(response.get('data') or {}, symbol, self.clock())  # when it arrived: a slow answer is not a future quote
        bid, ask = quote.get('bidp'), quote.get('askp')
        if type(bid) is not int or type(ask) is not int or not 0 < bid <= ask:
            raise ValueError('invalid bid/ask')
        self.quotes[symbol] = (time.time(), bid, ask)
        return bid, ask

    def cash(self):
        return self.db.execute("SELECT value FROM account WHERE name='cash'").fetchone()[0]

    def deposit(self, krw):
        """Money paid into the virtual account (적립식 contributions of the long-term engine)."""
        if type(krw) is not int or krw <= 0:
            raise ValueError('deposit must be a positive whole KRW amount')
        with self.db:
            self.db.execute("UPDATE account SET value=value+? WHERE name='cash'", (krw,))

    def holdings(self):
        book = {}
        for symbol, side, filled, price in self.db.execute('SELECT symbol,side,filled,fill_price FROM orders WHERE filled>0 ORDER BY number'):
            qty, avg = book.get(symbol, (0, 0.0))
            if side == 'BUY':
                book[symbol] = (qty + filled, (avg * qty + price * filled) / (qty + filled))
            else:
                book[symbol] = (qty - filled, avg if qty - filled else 0.0)
        return {s: v for s, v in book.items() if v[0] > 0}

    def fill(self, number, symbol, side, qty, price):
        """Fill a resting or new order when marketable against a fresh quote."""
        try:
            bid, ask = self.quote(symbol)
        except ValueError as exc:
            self.note(number, f'시세 없음: {exc}')
            raise
        if side == 'BUY' and price < ask:
            return self.note(number, f'매도호가 {ask:,}원 > 주문가 {price:,}원')
        if side == 'SELL' and price > bid:
            return self.note(number, f'매수호가 {bid:,}원 < 주문가 {price:,}원')
        at = ask if side == 'BUY' else bid
        with self.db:
            if side == 'BUY' and qty * at * (1 + self.fee) > self.cash():
                return self.note(number, '가상 현금 부족')
            self.db.execute('UPDATE orders SET filled=?, fill_price=? WHERE number=?', (qty, at, number))
            self.db.execute("UPDATE account SET value=value+? WHERE name='cash'",
                            ((-qty * at * (1 + self.fee)) if side == 'BUY' else qty * at * (1 - self.fee),))
        return True

    def note(self, number, text):
        with self.db:
            self.db.execute('UPDATE orders SET note=? WHERE number=?', (text, number))
        return False

    def call_tool(self, tool, params, order_intent_id=None, pre_reserve_check=None, pre_send_check=None, order_guard_deadline_ms=None, **_):
        if tool != 'call_api' or not isinstance(params, dict):
            return refused('unsupported tool')
        op, fields = params.get('operationId'), params.get('input') or {}
        try:
            if op == 'krstockInquiryDailyOrderExecution':
                return self.history(fields.get('orr_dt'))
            if op == 'krstockInquiryBalance':
                return self.balance()
            if op == 'krstockInquirySellableQuantity':
                held = self.holdings().get(fields.get('iem_cd'), (0, 0))[0]
                return {'status': 'ok', 'data': {'rsp_cd': '00000', 'rsp_msg': DONE, 'Output_0': {'iem_cd': fields.get('iem_cd'), 'sll_pbl_qty': held}}}
            if op in ('krstockOrderCashBuy', 'krstockOrderCashSell'):
                for check in (pre_reserve_check, pre_send_check):
                    if check is not None and check() is not True:
                        return ABORT
                if order_guard_deadline_ms is not None and time.time() * 1000 >= order_guard_deadline_ms:
                    return ABORT
                return self.order('BUY' if op.endswith('Buy') else 'SELL', fields)
            if op == 'krstockOrderCancel':
                return self.cancel(fields)
        except ValueError as exc:  # raised before any state change: nothing was "sent"
            return refused(str(exc))
        return refused('unsupported operation')

    def order(self, side, fields):
        symbol, qty, price = fields.get('iem_cd'), fields.get('orr_qty'), fields.get('orr_pr')
        if not isinstance(symbol, str) or type(qty) is not int or qty <= 0 or type(price) is not int or price <= 0:
            raise ValueError('invalid order fields')
        if side == 'SELL' and self.holdings().get(symbol, (0, 0))[0] < qty:
            raise ValueError('sell exceeds virtual holdings')
        if side == 'BUY' and qty * price * (1 + self.fee) > self.cash():  # the fee too, or it is accepted but never fills
            raise ValueError('insufficient virtual cash')
        now = self.clock()
        limits = self.limits() if callable(self.limits) else self.limits
        if side == 'BUY' and limits:
            day = now.strftime('%Y%m%d')
            spent = self.db.execute("SELECT COALESCE(SUM(qty*price),0) FROM orders WHERE day=? AND side='BUY'", (day,)).fetchone()[0]
            bought = {r[0] for r in self.db.execute("SELECT DISTINCT symbol FROM orders WHERE day=? AND side='BUY'", (day,))}
            if qty * price > limits.get('order_krw', float('inf')):
                raise ValueError('single order limit exceeded')
            if spent + qty * price > limits.get('daily_buy_krw', float('inf')):
                raise ValueError('daily buy limit exceeded')
            if symbol not in bought and len(bought) >= limits.get('daily_new_symbols', float('inf')):
                raise ValueError('daily new symbol limit exceeded')
            held = sum(q * avg for q, avg in self.holdings().values())
            pending = self.db.execute("SELECT COALESCE(SUM((qty-filled)*price),0) FROM orders WHERE side='BUY' AND cancelled=0 AND filled<qty").fetchone()[0]
            if held + pending + qty * price > limits.get('held_krw', float('inf')):
                raise ValueError('held amount limit exceeded')
        with self.db:
            number = self.db.execute('INSERT INTO orders (day,symbol,side,qty,price,filled,cancelled,fill_price,time) VALUES (?,?,?,?,?,0,0,NULL,?)',
                                     (now.strftime('%Y%m%d'), symbol, side, qty, price, now.isoformat())).lastrowid
        try:
            self.fill(number, symbol, side, qty, price)
        except ValueError:
            pass  # accepted but unfilled; re-checked on the next history read
        word, code = ('매수', '00048') if side == 'BUY' else ('매도', '00047')
        return {'status': 'ok', 'data': {'rsp_cd': code, 'rsp_msg': f'{word}주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': number}}}

    def cancel(self, fields):
        number = fields.get('org_mkt_orr_no')
        with self.db:
            changed = self.db.execute('UPDATE orders SET cancelled=qty-filled WHERE number=? AND filled<qty AND cancelled=0', (number,)).rowcount
        if changed != 1:
            raise ValueError('no open virtual order to cancel')
        return {'status': 'ok', 'data': {'rsp_cd': '00192', 'rsp_msg': '취소주문이 완료되었습니다', 'Output_0': {'mkt_orr_no': number}}}

    def history(self, day):
        for number, symbol, side, qty, price in self.db.execute(
                'SELECT number,symbol,side,qty,price FROM orders WHERE day=? AND filled=0 AND cancelled=0', (day,)).fetchall():
            try:
                self.fill(number, symbol, side, qty, price)
            except ValueError:
                pass
        rows = [{'itg_orr_no': n, 'iem_cd': s, 'sby_dit_cd_nm': '현금매수' if side == 'BUY' else '현금매도', 'orr_qty': q,
                 'tot_cns_qty': f, 'ny_cns_qty': q - f - c, 'can_qty': c, 'cns_avg_uit_pr': fp or 0, 'note': note}
                for n, s, side, q, f, c, fp, note in self.db.execute(
                    'SELECT number,symbol,side,qty,filled,cancelled,fill_price,note FROM orders WHERE day=? ORDER BY number', (day,))]
        return page('Output_0', rows)

    def balance(self):
        rows = []
        for symbol, (qty, avg) in sorted(self.holdings().items()):
            cached = self.quotes.get(symbol)
            if not cached or time.time() - cached[0] >= QUOTE_TTL:
                try:
                    self.quote(symbol)
                    cached = self.quotes[symbol]
                except ValueError:
                    pass  # valuation only: the runner's stop loss reads its own fresh quote
            bid = cached[1] if cached else avg
            rows.append({'iem_cd': symbol, 'rsdl_qty': qty, 'phs_pr': round(avg, 2), 'now_pr': bid,
                         'eal_pls_amt': round((bid - avg) * qty), 'pft_rt': round((bid / avg - 1) * 100, 2)})
        cash = int(self.cash())
        return page('Output_1', rows, Output_0={'orr_pbl_amt1': cash, 'tot_aet_amt': cash + sum(r['now_pr'] * r['rsdl_qty'] for r in rows)})
