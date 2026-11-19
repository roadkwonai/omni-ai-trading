"""Unwired, fail-closed primitives for guarded live NH orders (buys up to ORDER_QTY_MAX).

The caller must independently fetch each supplied snapshot directly from NH and
must enforce these methods on every transport path. This module submits nothing.
A guard only orders the symbols it was built with (default: 114800 alone); ownership,
adoption and sell replacement are kept per symbol, while the daily loss latch, capital,
pending-order and 5-new-symbols-per-day checks stay account-wide (6 on a long-term account's first investment day).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from src.tools.nhplug_calendar import check_session
from src.live.halt import halt_flag_set
from src.tools.private_db import private_file

KST = ZoneInfo('Asia/Seoul')
SYMBOL = '114800'
LIMIT = 20_000
MAX_PRICE = 2_000_000  # hard ceiling for one share (user decision 2026-11-12); the account setting may only lower it
# User decision 2026-11-12: like the paper order guard, no daily buy count; at most 5 distinct symbols bought per day
# (re-buying a symbol already bought today does not count). Replaces the earlier one-buy-per-day limit.
DAILY_NEW_SYMBOLS = 5
# User decision 2026-11-18: a long-term account may buy all of its engine's ETFs (vibe_longterm.ASSETS) on its first
# investment day, so the first purchase is not split over two days; every later day and every other account keep 5.
LONGTERM_FIRST_DAY_NEW_SYMBOLS = 6
SELL_FLOOR_PCT = 3  # a sell limit more than this below the current bid is refused (bad-price guard)
# User decision 2026-11-12: a live account may size by amount, so an order may carry several shares; the account's
# max_order_qty can only lower this buy ceiling, and the capital/cash checks apply to price x quantity.
ORDER_QTY_MAX = 1_000
# User decision 2026-11-17: the live capital is the account's own setting, chosen in the dashboard within the money in
# the account (and every buy is still checked against the broker's cash and total assets below). CAPITAL_MAX is only a
# typo ceiling; callers that pass no capital (the older inverse path) keep DEFAULT_CAPITAL.
CAPITAL_MAX = 1_000_000_000
DEFAULT_CAPITAL = 1_000_000


class LiveOrderBlocked(ValueError):
    """The transport must not be called."""


def verify_order_market(op, symbol, price, now, quote, previous_quote):
    """Recheck the directly fetched KRX quotes at reservation and before transport."""
    if op not in ('krstockOrderCashBuy', 'krstockOrderCashSell'):
        raise LiveOrderBlocked('operation unsupported')
    sell = op == 'krstockOrderCashSell'
    for snapshot in (previous_quote, quote):
        data = snapshot.get('data') if isinstance(snapshot, dict) else None
        row = data.get('Output_0') if isinstance(data, dict) else None
        if not isinstance(row, dict) or row.get('iem_cd') != symbol:
            raise LiveOrderBlocked('quote symbol mismatch or bid unavailable for sell' if sell else 'quote symbol mismatch')
    # 5 s for sells too: NH's quote clock runs 1~2 s ahead of this Mac, and 0 s refused every live sell from 12:11 on
    # 2026-11-17 (the mirror could not follow the paper stop). The quote must still be no older than 120 s.
    session = check_session(now, quote, previous_quote, require_advance=not sell,
                            session_end=time(15, 18) if sell else time(15),
                            future_tolerance_seconds=5)
    if not session.get('allowed'):
        raise LiveOrderBlocked('market data stale or market closed')
    current = quote['data']['Output_0']
    if type(price) is not int or not 0 < price <= MAX_PRICE:
        raise LiveOrderBlocked('limit price invalid')
    if sell:
        bid = current.get('bidp')
        if type(bid) is not int or bid <= 0:
            raise LiveOrderBlocked('quote symbol mismatch or bid unavailable for sell')
        if price * 100 < bid * (100 - SELL_FLOOR_PCT):
            raise LiveOrderBlocked('sell limit too far below current bid')
    else:
        ask = current.get('askp')
        if type(ask) is not int or ask <= 0 or price > ask:
            raise LiveOrderBlocked('limit exceeds current ask or ask unavailable')


def _loss(snapshot, realized_only=False):
    if not isinstance(snapshot, dict) or snapshot.get('rsp_cd') not in ('00000', '00166') or snapshot.get('rsp_msg', '').strip().rstrip('.') != '조회가 완료되었습니다':
        raise LiveOrderBlocked('loss response invalid')
    data = snapshot.get('Output_0')
    if not isinstance(data, dict):
        raise LiveOrderBlocked('loss fields unavailable')
    try:
        raw = [data[k] for k in ('sby_pls_amt', 'sdr_xps', 'eal_pls_amt')]
        if any(isinstance(x, bool) or not isinstance(x, (str, int, float, Decimal)) for x in raw):
            raise ValueError
        realized, fees, unrealized = [Decimal(str(x)) for x in raw]
        if not all(x.is_finite() for x in (realized, fees, unrealized)) or fees < 0:
            raise ValueError
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise LiveOrderBlocked('loss fields invalid') from exc
    # realized_only: a long-term account's latch ignores the holdings' paper loss (user 2026-11-17 "장기 전용 보호")
    return max(-realized, 0) + fees + (0 if realized_only else max(-unrealized, 0))


def _rows(snapshot, account, day):
    if not isinstance(snapshot, dict) or snapshot.get('_pagination_complete') is not True or snapshot.get('_account') != account or snapshot.get('_day') != day:
        raise LiveOrderBlocked('order pages or identity unverified')
    code, message = snapshot.get('rsp_cd'), snapshot.get('rsp_msg')
    if not isinstance(message, str) or (code, message.strip().rstrip('.')) not in {
        ('00166', '조회가 완료되었습니다'), ('00000', '조회가 완료되었습니다'),
        ('11512', '데이터가 존재하지 않습니다'),
    }:
        raise LiveOrderBlocked('broker order query did not succeed')
    rows = snapshot.get('Output_0')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise LiveOrderBlocked('order rows invalid')
    for row in rows:
        counts = [row.get(k) for k in ('orr_qty', 'tot_cns_qty', 'ny_cns_qty', 'can_qty')]
        if any(type(v) is not int or v < 0 for v in counts):
            raise LiveOrderBlocked('order quantities invalid')
        # NH acknowledgement rows are not terminal orders and cannot reconcile an intent.
        if sum(counts[1:]) != counts[0] and not (row.get('orr_rjt_rsn_cd_nm') == '확인' and counts == [1, 0, 0, 0]):
            raise LiveOrderBlocked('order quantities invalid')
    return rows


class LiveOrderGuard:
    def __init__(self, account: str, root: Path, symbols=(SYMBOL,)):
        if not isinstance(account, str) or len(account) != 11 or not account.isascii() or not account.isdigit():
            raise LiveOrderBlocked('invalid live account')
        if isinstance(symbols, str) or not symbols or any(not isinstance(c, str) or not re.fullmatch(r'[0-9][0-9A-Z]{5}', c) for c in symbols):
            raise LiveOrderBlocked('invalid live symbols')
        self.account = account
        self.symbols = frozenset(symbols)
        self.scope = hashlib.sha256(('nh-live:' + account).encode()).hexdigest()
        self.root = Path(root)
        self._pre_send_reservations = set()

    def _db(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / 'live-orders.sqlite3'
        private_file(path)  # src.tools.private_db: never os.open an existing database
        db = sqlite3.connect(path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS live_halts (scope TEXT NOT NULL, day TEXT NOT NULL, PRIMARY KEY(scope,day))')
        db.execute('''CREATE TABLE IF NOT EXISTS live_intents (
            fingerprint TEXT PRIMARY KEY, scope TEXT NOT NULL, day TEXT NOT NULL, operation TEXT NOT NULL,
            symbol TEXT NOT NULL, qty INTEGER NOT NULL, order_number INTEGER, state TEXT NOT NULL,
            filled INTEGER NOT NULL DEFAULT 0)''')
        db.execute('CREATE TABLE IF NOT EXISTS live_adoptions (scope TEXT NOT NULL, day TEXT NOT NULL, symbol TEXT NOT NULL, qty INTEGER NOT NULL, PRIMARY KEY(scope,day,symbol))')
        db.execute('CREATE TABLE IF NOT EXISTS sell_replacements (predecessor TEXT PRIMARY KEY, successor TEXT UNIQUE NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sell_cancellations (fingerprint TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS live_pre_send_aborts (fingerprint TEXT PRIMARY KEY, scope TEXT NOT NULL, time TEXT NOT NULL)')
        return db

    def _bought(self, db, day):
        """Symbols bought on `day` (YYYYMMDD) for the daily new-symbol cap; a buy proven never sent is none (the live mirror
        shows the same count in its plan)."""
        return {row[0] for row in db.execute("SELECT DISTINCT symbol FROM live_intents WHERE scope=? AND day=? AND operation='krstockOrderCashBuy' AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts)", (self.scope, day))}

    def _symbol(self, code):
        if not isinstance(code, str) or code not in self.symbols:
            raise LiveOrderBlocked('account or symbol mismatch')
        return code

    def _owned(self, db, symbol=SYMBOL):
        bought = db.execute("SELECT COALESCE(SUM(filled),0) FROM live_intents WHERE scope=? AND symbol=? AND operation='krstockOrderCashBuy' AND state='terminal'", (self.scope, symbol)).fetchone()[0]
        sold = db.execute("SELECT COALESCE(SUM(filled),0) FROM live_intents WHERE scope=? AND symbol=? AND operation='krstockOrderCashSell' AND state='terminal'", (self.scope, symbol)).fetchone()[0]
        adopted = db.execute('SELECT COALESCE(SUM(qty),0) FROM live_adoptions WHERE scope=? AND symbol=?', (self.scope, symbol)).fetchone()[0]
        return bought + adopted - sold

    def owned(self, symbol):
        """Shares of `symbol` this guard's ledger holds (terminal fills plus explicit adoptions)."""
        with closing(self._db()) as db:
            return self._owned(db, self._symbol(symbol))

    def adopt_existing(self, *, now, balance, sellable, quantity=1, symbol=SYMBOL):
        """Record explicitly requested holdings after complete broker reads."""
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise LiveOrderBlocked('clock unavailable')
        symbol = self._symbol(symbol)
        if not isinstance(balance, dict) or balance.get('_pagination_complete') is not True or balance.get('_account') != self.account:
            raise LiveOrderBlocked('live balance completeness unverified')
        if balance.get('rsp_cd') not in ('00000', '00166') or str(balance.get('rsp_msg','')).strip().rstrip('.') != '조회가 완료되었습니다':
            raise LiveOrderBlocked('live balance query failed')
        rows = balance.get('Output_1')
        if not isinstance(rows, list) or any(not isinstance(x,dict) or type(x.get('rsdl_qty')) is not int or x['rsdl_qty'] < 0 for x in rows):
            raise LiveOrderBlocked('holdings invalid')
        if type(quantity) is not int or quantity <= 0 or sum(x['rsdl_qty'] for x in rows if x.get('iem_cd') == symbol) != quantity:
            raise LiveOrderBlocked('requested holdings do not match broker')
        if (not isinstance(sellable,dict) or sellable.get('_account') != self.account or sellable.get('_symbol') != symbol
                or sellable.get('rsp_cd') not in ('00000','00166','XA102') or type(sellable.get('sll_pbl_qty')) is not int or sellable['sll_pbl_qty'] < 1):
            raise LiveOrderBlocked('sellable quantity unverified')
        day=now.astimezone(KST).strftime('%Y%m%d')
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM live_intents WHERE scope=? AND state!='terminal'",(self.scope,)).fetchone():
                raise LiveOrderBlocked('unresolved order prevents adoption')
            owned = self._owned(db, symbol)
            if owned == quantity:
                return
            if owned < 0 or owned > quantity:
                raise LiveOrderBlocked('ownership ledger conflict')
            db.execute('INSERT INTO live_adoptions VALUES (?,?,?,?) ON CONFLICT(scope,day,symbol) DO UPDATE SET qty=qty+excluded.qty',
                       (self.scope,day,symbol,quantity-owned))

    def observe_loss(self, pnl, now, limit=LIMIT, realized_only=False):
        """Latch the day's halt once the loss reaches the account's own limit (accounts budget.daily_loss_krw, user setting);
        it can only tighten the approved LIMIT."""
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise LiveOrderBlocked('clock unavailable')
        day = now.astimezone(KST).strftime('%Y%m%d')
        loss = _loss(pnl, realized_only)
        cap = min(limit, LIMIT) if type(limit) is int and limit > 0 else LIMIT
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if loss >= cap:
                db.execute('INSERT OR IGNORE INTO live_halts VALUES (?,?)', (self.scope, day))
            halted = db.execute('SELECT 1 FROM live_halts WHERE scope=? AND day=?', (self.scope, day)).fetchone() is not None
        return {'loss': str(loss), 'halted': halted}

    def adopt_order(self, *, now, history, order_number):
        """Explicitly adopt one verified outstanding one-share cash buy for cancellation."""
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None or type(order_number) is not int or order_number <= 0:
            raise LiveOrderBlocked('invalid order identity')
        day = now.astimezone(KST).strftime('%Y%m%d')
        rows = _rows(history, self.account, day)
        matches = [row for row in rows if row.get('itg_orr_no') == order_number]
        if len(matches) != 1:
            raise LiveOrderBlocked('order identity ambiguous')
        row = matches[0]
        if (row.get('iem_cd') not in self.symbols or row.get('sby_dit_cd_nm') != '현금매수'
                or [row.get(k) for k in ('orr_qty','tot_cns_qty','ny_cns_qty','can_qty')] != [1,0,1,0]
                or row.get('orr_rjt_rsn_cd_nm') != '정상'):
            raise LiveOrderBlocked('not an outstanding one-share cash buy')
        fingerprint = hashlib.sha256(f'adopt:{self.scope}:{day}:{order_number}'.encode()).hexdigest()
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM live_intents WHERE scope=? AND state!='terminal'", (self.scope,)).fetchone():
                raise LiveOrderBlocked('unresolved order blocks adoption')
            if db.execute('SELECT 1 FROM live_intents WHERE scope=? AND day=? AND order_number=?', (self.scope,day,order_number)).fetchone():
                raise LiveOrderBlocked('order already recorded')
            db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)',
                       (fingerprint,self.scope,day,'krstockOrderCashBuy',row['iem_cd'],1,order_number,'acknowledged',0))
        return fingerprint

    def reserve(self, op, fields, *, now, accounts, history, pnl, previous_quote, quote, balance=None, sellable=None, replacement_of=None,
                capital=DEFAULT_CAPITAL, order_max=DEFAULT_CAPITAL, new_symbols=DAILY_NEW_SYMBOLS, loss_limit=LIMIT,
                realized_only=False):
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise LiveOrderBlocked('clock unavailable')
        now = now.astimezone(KST)
        day = now.strftime('%Y%m%d')
        if op not in ('krstockOrderCashBuy', 'krstockOrderCashSell', 'krstockOrderCancel') or not isinstance(fields, dict):
            raise LiveOrderBlocked('operation unsupported')
        if halt_flag_set('nhplug'):
            raise LiveOrderBlocked('live kill switch active')
        if fields.get('act_no') != self.account:
            raise LiveOrderBlocked('account or symbol mismatch')
        symbol = self._symbol(fields.get('iem_cd'))
        entries = accounts.get('Output_0') if isinstance(accounts, dict) and accounts.get('rsp_cd') == '00000' else None
        matches = [a for a in entries if isinstance(a, dict) and a.get('acct_no') == self.account and a.get('acct_type') == '01'] if isinstance(entries, list) else []
        if len(matches) != 1:
            raise LiveOrderBlocked('live account identity not proven')
        if op == 'krstockOrderCashBuy' and self.observe_loss(pnl, now, loss_limit, realized_only)['halted']:
            raise LiveOrderBlocked('daily loss latch active')
        rows = _rows(history, self.account, day)
        cancel = op == 'krstockOrderCancel'
        if cancel:
            if set(fields) != {'act_no', 'iem_cd', 'org_mkt_orr_no', 'all_pat_dit_cd'}:
                raise LiveOrderBlocked('unsupported cancellation fields')
            number = fields.get('org_mkt_orr_no')
            if type(number) is not int or number <= 0 or fields.get('all_pat_dit_cd') != '1':
                raise LiveOrderBlocked('only whole-order cancellation supported')
            targets = [r for r in rows if r.get('itg_orr_no') == number and r.get('iem_cd') == symbol]
            if (len(targets) != 1 or targets[0].get('ny_cns_qty', 0) < 1 or targets[0].get('can_qty') != 0
                    or targets[0].get('orr_qty') != targets[0].get('tot_cns_qty', -1) + targets[0]['ny_cns_qty']):
                raise LiveOrderBlocked('pending original order not verified')  # a partly filled order may still be cancelled
            qty = targets[0]['orr_qty']  # reconcile compares the cancel intent with the original order's quantity
        else:
            if set(fields) != {'act_no', 'iem_cd', 'orr_qty', 'orr_pr', 'nmn_pr_tp_cd', 'rmt_mkt_cd',
                               'sor_mkt_sli_yn', 'orr_cnd_dit_cd', 'ssl_nmn_pr_dit_cd'}:
                raise LiveOrderBlocked('unsupported order fields')
            number = None
            qty = fields.get('orr_qty')
            if (type(qty) is not int or qty < 1 or (op == 'krstockOrderCashBuy' and qty > ORDER_QTY_MAX)
                    or fields.get('nmn_pr_tp_cd') != '01' or fields.get('rmt_mkt_cd') != 'KRX' or fields.get('sor_mkt_sli_yn') != 'N' or fields.get('orr_cnd_dit_cd') != '00' or fields.get('ssl_nmn_pr_dit_cd') != '00'):
                raise LiveOrderBlocked('invalid cash KRX limit order or buy quantity above ORDER_QTY_MAX')
            price = fields.get('orr_pr')
            if type(price) is not int or not 0 < price <= MAX_PRICE:
                raise LiveOrderBlocked('limit price invalid')
            if any(r['ny_cns_qty'] for r in rows):
                raise LiveOrderBlocked('broker has outstanding orders')
            verify_order_market(op, symbol, price, now, quote, previous_quote)
        if op == 'krstockOrderCashBuy':
            if not isinstance(balance, dict) or balance.get('_pagination_complete') is not True or balance.get('_account') != self.account:
                raise LiveOrderBlocked('live balance completeness unverified')
            if balance.get('rsp_cd') not in ('00000', '00166') or not isinstance(balance.get('rsp_msg'), str) or balance['rsp_msg'].strip().rstrip('.') != '조회가 완료되었습니다':
                raise LiveOrderBlocked('live balance query did not succeed')
            holdings, summary = balance.get('Output_1'), balance.get('Output_0')
            if not isinstance(holdings, list) or not isinstance(summary, dict):
                raise LiveOrderBlocked('live balance unavailable')
            if any(not isinstance(r, dict) or type(r.get('rsdl_qty')) is not int or r['rsdl_qty'] < 0 for r in holdings):
                raise LiveOrderBlocked('unverified holdings')
            cash = summary.get('orr_pbl_amt1')
            assets = summary.get('tot_aet_amt')
            invested = summary.get('tot_byn_amt')
            cost = price * qty
            limit = min(capital, CAPITAL_MAX) if type(capital) is int and capital > 0 else 0
            if type(cash) is not int or type(assets) is not int or cash < cost or assets < 0:
                raise LiveOrderBlocked('available cash unverified or insufficient')
            if type(invested) is not int or invested < 0 or invested + cost > limit or cost > min(cash, assets):
                raise LiveOrderBlocked('capital limit exceeded')
            # the account's per-order cap (user request 2026-11-13) can only lower the capital
            if cost > min(order_max if type(order_max) is int and order_max > 0 else 0, limit):
                raise LiveOrderBlocked('single order limit exceeded')
        fingerprint = hashlib.sha256(json.dumps([self.scope, day, op, fields], sort_keys=True, allow_nan=False).encode()).hexdigest()
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if halt_flag_set('nhplug'):
                raise LiveOrderBlocked('live kill switch active')
            halted = db.execute('SELECT 1 FROM live_halts WHERE scope=? AND day=?', (self.scope, day)).fetchone() is not None
            if halted and op == 'krstockOrderCashBuy':
                raise LiveOrderBlocked('daily loss latch active')
            if op == 'krstockOrderCashSell':
                latest = db.execute("SELECT fingerprint FROM live_intents WHERE scope=? AND symbol=? AND operation='krstockOrderCashSell' AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts) ORDER BY rowid DESC LIMIT 1", (self.scope, symbol)).fetchone()
                if latest and db.execute('SELECT 1 FROM sell_cancellations WHERE fingerprint=?', (latest[0],)).fetchone() and replacement_of != latest[0]:
                    raise LiveOrderBlocked('verified sell cancellation requires explicit replacement')
            if replacement_of is not None:
                if op != 'krstockOrderCashSell' or not isinstance(replacement_of, str):
                    raise LiveOrderBlocked('invalid replacement request')
                eligible = db.execute("SELECT 1 FROM live_intents JOIN sell_cancellations USING(fingerprint) WHERE fingerprint=? AND scope=? AND symbol=? AND operation='krstockOrderCashSell' AND state='terminal' AND filled=0", (replacement_of, self.scope, symbol)).fetchone()
                if not eligible or db.execute('SELECT 1 FROM sell_replacements WHERE predecessor=?', (replacement_of,)).fetchone():
                    raise LiveOrderBlocked('replacement requires unused verified sell cancellation')
                fingerprint = hashlib.sha256((fingerprint + ':replacement:' + replacement_of).encode()).hexdigest()
            while row := db.execute('SELECT state FROM live_intents WHERE fingerprint=?', (fingerprint,)).fetchone():
                # A finished (filled or cancelled) order with the same fields is not a duplicate: the same quantity at the
                # same price later in the day is a new order (4th review). An unresolved one is still blocked below, and so
                # is a buy proven never sent (a pre-send abort is not a finished order; only a sell may follow one).
                aborted = db.execute('SELECT 1 FROM live_pre_send_aborts WHERE fingerprint=? AND scope=?', (fingerprint, self.scope)).fetchone()
                if row[0] == 'terminal' and not aborted:
                    fingerprint = hashlib.sha256((fingerprint + ':after-terminal').encode()).hexdigest()
                    continue
                if op != 'krstockOrderCashSell' or not aborted:
                    raise LiveOrderBlocked('duplicate intent')
                fingerprint = hashlib.sha256((fingerprint + ':after-pre-send-abort').encode()).hexdigest()
            pending = db.execute("SELECT operation,order_number,symbol FROM live_intents WHERE scope=? AND state!='terminal'", (self.scope,)).fetchall()
            if cancel:
                if pending != [('krstockOrderCashBuy', number, symbol)] and pending != [('krstockOrderCashSell', number, symbol)]:
                    raise LiveOrderBlocked('cancel requires one known pending order')
            elif pending:
                raise LiveOrderBlocked('unresolved order blocks submission')
            if op == 'krstockOrderCashBuy':
                bought = self._bought(db, day)
                cap = min(new_symbols, DAILY_NEW_SYMBOLS) if type(new_symbols) is int and new_symbols > 0 else 0
                # realized_only marks a long-term account (nh_guarded_live: engine 'longterm'); its first investment day has
                # no buy intent (filled or not; a buy proven never sent is none, as in `bought`) or adoption on an earlier
                # day. A tighter account setting still binds.
                if realized_only is True and cap == DAILY_NEW_SYMBOLS and not db.execute(
                        "SELECT 1 FROM live_intents WHERE scope=? AND day<? AND operation='krstockOrderCashBuy' "
                        "AND fingerprint NOT IN (SELECT fingerprint FROM live_pre_send_aborts) UNION ALL SELECT 1 FROM live_adoptions WHERE scope=? AND day<?", (self.scope, day, self.scope, day)).fetchone():
                    cap = LONGTERM_FIRST_DAY_NEW_SYMBOLS
                if symbol not in bought and len(bought) >= cap:
                    raise LiveOrderBlocked('daily new symbol limit')
            if op == 'krstockOrderCashSell':
                actual = balance.get('Output_1') if (isinstance(balance,dict) and balance.get('_pagination_complete') is True
                    and balance.get('_account') == self.account and balance.get('rsp_cd') in ('00000','00166')
                    and isinstance(balance.get('rsp_msg'),str) and balance['rsp_msg'].strip().rstrip('.') == '조회가 완료되었습니다') else None
                if (self._owned(db, symbol) < qty or not isinstance(actual,list)
                        or any(not isinstance(r,dict) or type(r.get('rsdl_qty')) is not int or r['rsdl_qty'] < 0 for r in actual)
                        or sum(r['rsdl_qty'] for r in actual if r.get('iem_cd') == symbol) < qty or not isinstance(sellable, dict)
                        or sellable.get('_account') != self.account or sellable.get('_symbol') != symbol
                        or sellable.get('rsp_cd') not in ('00000', '00166', 'XA102')
                        or type(sellable.get('sll_pbl_qty')) is not int or sellable['sll_pbl_qty'] < qty):
                    raise LiveOrderBlocked('owned and sellable quantity unverified')
            db.execute('INSERT INTO live_intents VALUES (?,?,?,?,?,?,?,?,?)', (fingerprint, self.scope, day, op, symbol, qty, number, 'uncertain', 0))
            if replacement_of is not None:
                db.execute('INSERT INTO sell_replacements VALUES (?,?)', (replacement_of, fingerprint))
        self._pre_send_reservations.add(fingerprint)
        return fingerprint

    def abort_before_send(self, fingerprint):
        """Resolve only an intent proven by the transport to precede any order send."""
        if fingerprint not in self._pre_send_reservations:
            raise LiveOrderBlocked('pre-send reservation is not owned by this process')
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT operation FROM live_intents WHERE fingerprint=? AND scope=? AND state='uncertain' AND order_number IS NULL", (fingerprint, self.scope)).fetchone()
            if row is None or row[0] not in ('krstockOrderCashBuy', 'krstockOrderCashSell'):
                raise LiveOrderBlocked('pre-send abort intent identity unavailable')
            db.execute('INSERT INTO live_pre_send_aborts VALUES (?,?,?)', (fingerprint, self.scope, datetime.now(ZoneInfo('Asia/Seoul')).isoformat()))
            db.execute("UPDATE live_intents SET state='terminal',filled=0 WHERE fingerprint=? AND scope=?", (fingerprint, self.scope))
            db.execute('DELETE FROM sell_replacements WHERE successor=?', (fingerprint,))
        self._pre_send_reservations.discard(fingerprint)

    def mark_send_attempted(self, fingerprint):
        self._pre_send_reservations.discard(fingerprint)

    def acknowledge(self, fingerprint, response):
        self.mark_send_attempted(fingerprint)
        output = response.get('Output_0') if isinstance(response, dict) else None
        number = output.get('mkt_orr_no') if isinstance(output, dict) else None
        if type(number) is not int or number <= 0:
            raise LiveOrderBlocked('uncertain submission; reconcile without retry')
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT operation FROM live_intents WHERE fingerprint=? AND scope=? AND state='uncertain'", (fingerprint, self.scope)).fetchone()
            if row is None:
                raise LiveOrderBlocked('intent unavailable')
            codes = {'krstockOrderCashBuy': ('00048', '매수'), 'krstockOrderCashSell': ('00047', '매도'),
                     'krstockOrderCancel': ('00192', '취소')}
            code, word = codes[row[0]]
            message = response.get('rsp_msg')
            if response.get('rsp_cd') != code or not isinstance(message, str) or word not in message or '완료' not in message:
                raise LiveOrderBlocked('unverified broker business response; reconcile without retry')
            db.execute("UPDATE live_intents SET state='acknowledged', order_number=CASE WHEN operation='krstockOrderCancel' THEN order_number ELSE ? END WHERE fingerprint=?", (number, fingerprint))

    def reconcile(self, fingerprint, history):
        with closing(self._db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            intent = db.execute('SELECT day,operation,symbol,qty,order_number,state FROM live_intents WHERE fingerprint=? AND scope=?', (fingerprint, self.scope)).fetchone()
            if intent is None or intent[4] is None or intent[5] != 'acknowledged':
                raise LiveOrderBlocked('intent identity unavailable')
            day, op, symbol, qty, number, _ = intent
            rows = _rows(history, self.account, day)
            matched = [r for r in rows if r.get('itg_orr_no') == number and r.get('iem_cd') == symbol]
            if len(matched) != 1:
                raise LiveOrderBlocked('order identity ambiguous')
            row = matched[0]
            if (row['orr_qty'] != qty or row['ny_cns_qty'] != 0
                    or row['tot_cns_qty'] + row['can_qty'] != qty):
                raise LiveOrderBlocked('order not terminal')
            if op != 'krstockOrderCancel':
                side = {'krstockOrderCashBuy':'현금매수', 'krstockOrderCashSell':'현금매도'}[op]
                if row.get('sby_dit_cd_nm') != side:
                    raise LiveOrderBlocked('order side mismatch')
            db.execute("UPDATE live_intents SET state='terminal',filled=? WHERE fingerprint=?", (row['tot_cns_qty'], fingerprint))
            if op == 'krstockOrderCashSell' and row['tot_cns_qty'] == 0 and row['can_qty'] == qty:
                db.execute('INSERT OR IGNORE INTO sell_cancellations VALUES (?)', (fingerprint,))
            # A terminal original order also resolves its known cancellation request.
            db.execute("UPDATE live_intents SET state='terminal' WHERE scope=? AND day=? AND operation='krstockOrderCancel' AND order_number=? AND state IN ('acknowledged','uncertain')", (self.scope, day, number))
            return {'filled': row['tot_cns_qty'], 'cancelled': row['can_qty']}
