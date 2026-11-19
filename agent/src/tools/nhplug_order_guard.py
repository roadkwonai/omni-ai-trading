"""Durable, fail-closed submission guard for the local NH paper MCP integration."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from decimal import Decimal, InvalidOperation
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config.paths import get_runtime_root
from src.tools.private_db import private_file

ORDERS = {'krstockOrderCashBuy', 'krstockOrderCashSell', 'krstockOrderCancel'}
# One-time NXT paper test (user approval 2026-11-11): a single far-below-market 1-share limit buy of 005930 on NXT in the
# 2026-11-12 NXT pre-market, only while scripts/nxt_paper_test.py holds the one-use token file. Inert on any other day.
NXT_TEST = {'day': '20261112', 'symbol': '005930', 'start': '15:35', 'end': '19:30', 'token': 'paper-nxt-test.json'}
READ_TOOLS = {'list_apis', 'describe_api', 'list_accounts', 'get_stock_price', 'get_stock_balance'}


class NHOrderBlocked(ValueError):
    """No request was sent; prior state or configuration requires reconciliation."""


def nh_guard(server_name, config):
    if server_name.startswith('nhplug_') or any(Path(a).name == 'nhplug_mcp.py' for a in config.args):
        return NHOrderGuard(config.args, server_name=server_name)
    return None



def nxt_test_shape(op, fields, now=None):
    """The approved one-time NXT test order's shape, date and window (the token is checked separately by the guard)."""
    now = now or datetime.now(ZoneInfo('Asia/Seoul'))
    return (op == 'krstockOrderCashBuy' and isinstance(fields, dict) and now.strftime('%Y%m%d') == NXT_TEST['day']
            and NXT_TEST['start'] <= now.strftime('%H:%M') < NXT_TEST['end'] and fields.get('iem_cd') == NXT_TEST['symbol']
            and fields.get('orr_qty') == 1 and fields.get('rmt_mkt_cd') == 'NXT' and fields.get('sor_mkt_sli_yn') == 'N'
            and fields.get('nmn_pr_tp_cd') == '01' and type(fields.get('orr_pr')) is int)

# Shared paper account limits, settable on the dashboard (accounts.json paper-shared.limits, user request 2026-11-13).
# Same keys and bounds as scripts/accounts.py PAPER_LIMITS; anything missing or out of bounds keeps the default.
PAPER_LIMITS = {'order_max_krw': (100_000, 100_000_000, 3_000_000), 'daily_buy_krw': (1_000_000, 1_000_000_000, 6_000_000),
                'held_max_krw': (1_000_000, 5_000_000_000, 21_000_000), 'daily_new_symbols': (1, 50, 5),
                'daily_loss_krw': (10_000, 100_000_000, 300_000)}


def paper_limits():
    """{key: value} for the shared paper account from accounts.json, each within PAPER_LIMITS, else its default."""
    try:
        data = json.loads((get_runtime_root() / 'nhplug' / 'accounts.json').read_text())
        saved = ((data.get('accounts') or {}).get('paper-shared') or {}).get('limits') or {}
    except (OSError, ValueError, AttributeError):
        saved = {}
    out = {}
    for key, (low, high, default) in PAPER_LIMITS.items():
        value = saved.get(key) if isinstance(saved, dict) else None
        out[key] = value if type(value) is int and low <= value <= high else default
    return out


class NHOrderGuard:
    def __init__(self, args, *, server_name=None):
        def option(name):
            if args.count(name) != 1 or args.index(name) + 1 >= len(args):
                raise NHOrderBlocked('NH launcher mode/account configuration is invalid')
            return args[args.index(name) + 1]
        self.mode = option('--mode')
        self.account = option('--account')
        if self.mode not in {'readonly', 'paper'} or not self.account:
            raise NHOrderBlocked('Only readonly and paper NH modes are supported')
        self.scope = hashlib.sha256(('nh-paper:' + self.account).encode()).hexdigest()
        self.path = get_runtime_root() / 'nhplug' / 'orders.sqlite3'
        self.server_name = server_name

    def check_owner(self):
        path = get_runtime_root() / 'nhplug' / 'paper-account-owners.json'
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            raise NHOrderBlocked('Paper account ownership registry is invalid') from exc
        if (not isinstance(data, dict) or set(data) != {'version', 'accounts'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['accounts'], dict)
                or any(not isinstance(scope, str) or len(scope) != 64
                       or any(c not in '0123456789abcdef' for c in scope)
                       or owner != 'nhplug_paper_isolated'
                       for scope, owner in data['accounts'].items())):
            raise NHOrderBlocked('Paper account ownership registry is invalid')
        owner = data['accounts'].get(self.scope)
        if owner is not None and self.server_name != owner:
            raise NHOrderBlocked('Paper account is assigned to a different order runner')

    def paper_policy(self):
        """Resolve an explicitly registered paper scope, otherwise keep legacy policy."""
        root = get_runtime_root() / 'nhplug'
        registry = root / 'paper-policy-scopes.json'
        if registry.exists():
            try:
                data = json.loads(registry.read_text())
                if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('accounts'), dict):
                    raise ValueError('invalid account policy registry')
                selected = data['accounts'].get(self.scope)
                if selected is not None:
                    if selected != {'profile': 'independent', 'krx_session_check': True, 'auto_loss_stop': False}:
                        raise ValueError('invalid independent account policy')
                    from src.config.loader import load_agent_config
                    default = load_agent_config().mcp_servers.get('nhplug_paper')
                    if default is None:
                        raise ValueError('default paper account unavailable')
                    default_guard = NHOrderGuard(default.args)
                    if default_guard.mode != 'paper' or default_guard.scope == self.scope:
                        raise ValueError('default paper account cannot be independent')
                    return {'enabled': True, **selected, '_account_independent': True}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise NHOrderBlocked('Paper account policy is invalid') from exc
        path = root / 'swing-policy.json'
        if not path.exists():
            return None
        try:
            policy = json.loads(path.read_text())
            if not isinstance(policy, dict) or policy.get('enabled') is not True:
                raise ValueError('invalid swing policy')
            policy.pop('_account_independent', None)
            return policy
        except (OSError, ValueError) as exc:
            raise NHOrderBlocked('Swing policy is invalid; orders are blocked') from exc

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        private_file(self.path)  # src.tools.private_db: never os.open an existing database
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            db.execute('''CREATE TABLE IF NOT EXISTS intents (
                fingerprint TEXT PRIMARY KEY, scope TEXT NOT NULL, day TEXT NOT NULL,
                operation TEXT NOT NULL, symbol TEXT NOT NULL, quantity INTEGER NOT NULL,
                order_number INTEGER, state TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS swing_buys (
                fingerprint TEXT PRIMARY KEY, scope TEXT NOT NULL, day TEXT NOT NULL,
                symbol TEXT NOT NULL, notional INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS explicit_intents (
                intent_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS pre_send_aborts (
                fingerprint TEXT PRIMARY KEY, time TEXT NOT NULL)''')
            # Filled quantity of an intent when the broker history certified it terminal (2026-11-13; earlier intents have
            # no row and are estimated in held_exposure).
            db.execute('''CREATE TABLE IF NOT EXISTS intent_fills (
                fingerprint TEXT PRIMARY KEY, filled INTEGER NOT NULL)''')
            yield db

    def held_exposure(self, db):
        """KRW cost of what this scope holds now from its own buys: each buy's limit notional per share times the filled
        quantity, less the average cost of filled sells; an open buy counts in full. Filled quantity comes from
        intent_fills; an older terminal intent without it counts as fully filled unless a cancel targeted its order
        (then a buy counts nothing and a sell sells nothing, the cautious side for a cap)."""
        rows = db.execute('''SELECT i.fingerprint, i.operation, i.symbol, i.quantity, i.order_number, i.state, b.notional, f.filled
            FROM intents i LEFT JOIN swing_buys b ON b.fingerprint=i.fingerprint LEFT JOIN intent_fills f ON f.fingerprint=i.fingerprint
            WHERE i.scope=? AND i.operation IN ('krstockOrderCashBuy','krstockOrderCashSell')
            AND NOT EXISTS (SELECT 1 FROM pre_send_aborts a WHERE a.fingerprint=i.fingerprint) ORDER BY i.rowid''',
                          (self.scope,)).fetchall()
        cancelled = {r[0] for r in db.execute(
            "SELECT order_number FROM intents WHERE scope=? AND operation='krstockOrderCancel'", (self.scope,)).fetchall()}
        books = {}
        for row in rows:
            qty, cost = books.get(row['symbol'], (0, 0))
            if row['filled'] is not None:
                filled = row['filled']
            elif row['state'] != 'terminal':
                filled = row['quantity'] if row['operation'] == 'krstockOrderCashBuy' else 0
            else:
                filled = 0 if row['order_number'] in cancelled else row['quantity']
            if row['operation'] == 'krstockOrderCashBuy':
                per_share = (row['notional'] or 0) / row['quantity'] if row['quantity'] else 0
                qty, cost = qty + filled, cost + per_share * filled
            elif qty:
                sold = min(filled, qty)
                cost, qty = cost - cost / qty * sold, qty - sold
            books[row['symbol']] = (qty, cost if qty else 0)
        return sum(cost for _, cost in books.values())

    def nxt_test_order(self, op, fields, now):
        """True (and the one-use token consumed) only for the approved NXT test order; otherwise False."""
        if not nxt_test_shape(op, fields, now):
            return False
        token = get_runtime_root() / 'nhplug' / NXT_TEST['token']
        used = token.with_suffix('.used')
        try:
            os.rename(token, used)  # atomic: the token can be spent once
            data = json.loads(used.read_text())
        except (OSError, ValueError):
            return False
        return (isinstance(data, dict) and data.get('day') == NXT_TEST['day'] and data.get('symbol') == NXT_TEST['symbol']
                and type(data.get('max_price')) is int and 0 < fields['orr_pr'] <= data['max_price'])

    def release_rejected_nxt_test(self, fingerprint, broker_rows):
        """End a rejected NXT test intent: no broker order number and no 005930 order in today's broker history."""
        if not isinstance(broker_rows, list) or any(isinstance(r, dict) and r.get('iem_cd') == NXT_TEST['symbol'] for r in broker_rows):
            raise NHOrderBlocked('The test order may exist at the broker; reconcile it instead')
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM intents WHERE fingerprint=?', (fingerprint,)).fetchone()
            if (not row or row['scope'] != self.scope or row['day'] != NXT_TEST['day'] or row['symbol'] != NXT_TEST['symbol']
                    or row['operation'] != 'krstockOrderCashBuy' or row['state'] == 'terminal' or row['order_number'] is not None):
                raise NHOrderBlocked('Not a releasable NXT test intent')
            db.execute("UPDATE intents SET state='terminal' WHERE fingerprint=?", (fingerprint,))

    def before(self, remote, arguments, intent_id=None):
        if remote in READ_TOOLS:
            return None
        op = arguments.get('operationId', '') if remote == 'call_api' else ''
        if op.startswith(('krstockInquiry', 'krstockQuote')):
            return None
        if self.mode != 'paper' or op not in ORDERS:
            raise NHOrderBlocked('NH live orders and unsupported write tools are blocked')
        if intent_id is not None and (not isinstance(intent_id, str) or len(intent_id) != 32 or any(c not in '0123456789abcdef' for c in intent_id)):
            raise NHOrderBlocked('Invalid order intent ID')
        fields = arguments.get('input', {})
        if not isinstance(fields, dict) or fields.get('act_no') != self.account:
            raise NHOrderBlocked('Order account must match the configured paper account')
        symbol = fields.get('iem_cd')
        if not isinstance(symbol, str) or not re.fullmatch(r'[0-9][0-9A-Z]{5}', symbol):  # KRX codes may carry a letter since 2024
            raise NHOrderBlocked('A six-digit domestic symbol is required')
        cancel = op == 'krstockOrderCancel'
        if not cancel:
            self.check_owner()
        quantity = fields.get('orr_qty', 0)
        number = fields.get('org_mkt_orr_no') if cancel else None
        if cancel:
            if type(number) is not int or number <= 0 or fields.get('all_pat_dit_cd') != '1':
                raise NHOrderBlocked('Only whole-order paper cancellation is supported')
        now = datetime.now(ZoneInfo('Asia/Seoul'))
        nxt_test = not cancel and fields.get('rmt_mkt_cd') == 'NXT' and self.nxt_test_order(op, fields, now)
        if not cancel and not nxt_test and (type(quantity) is not int or quantity <= 0 or fields.get('rmt_mkt_cd') != 'KRX'
                                            or fields.get('sor_mkt_sli_yn') != 'N'):
            raise NHOrderBlocked('Only positive integer quantities on KRX are supported')
        day = now.strftime('%Y%m%d')
        policy = self.paper_policy() if not cancel else None
        notional = 0
        if policy and not cancel:
            if op == 'krstockOrderCashBuy':
                if (get_runtime_root() / 'nhplug' / 'PAPER_HALT').exists():
                    raise NHOrderBlocked('Paper buying is halted')
                if fields.get('nmn_pr_tp_cd') != '01':
                    raise NHOrderBlocked('Swing policy permits limit buys only')
                if not nxt_test and (now.weekday() >= 5 or not '09:00' <= now.strftime('%H:%M') < '15:00'):
                    raise NHOrderBlocked('Outside the permitted paper trading window')
                try:
                    price = Decimal(str(fields.get('orr_pr')))
                    if not price.is_finite() or price <= 0 or price != price.to_integral_value():
                        raise ValueError('invalid price')
                    notional = int(price) * quantity
                except (InvalidOperation, ValueError, TypeError, OverflowError) as exc:
                    raise NHOrderBlocked('Limit price must be a positive whole KRW amount') from exc
                limits = paper_limits()
                if not policy.get('_account_independent') and notional > limits['order_max_krw']:
                    raise NHOrderBlocked('Swing single order limit exceeded')
        parts = [self.scope, day, op, fields]
        if intent_id is not None:
            parts.append(intent_id)
        fingerprint = hashlib.sha256(json.dumps(parts, sort_keys=True, allow_nan=False).encode()).hexdigest()
        intent_key = hashlib.sha256((self.scope + ':' + intent_id).encode()).hexdigest() if intent_id else None
        with self.database() as db:
            # Commit intent before opening the transport; process death cannot erase it.
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM intents WHERE fingerprint=?', (fingerprint,)).fetchone():
                raise NHOrderBlocked('Duplicate NH order intent; do not resubmit')
            if intent_key and db.execute('SELECT 1 FROM explicit_intents WHERE intent_key=?', (intent_key,)).fetchone():
                raise NHOrderBlocked('Order intent ID already used; do not resubmit')
            if not cancel and db.execute("SELECT 1 FROM intents WHERE scope=? AND state!='terminal'", (self.scope,)).fetchone():
                raise NHOrderBlocked('Previous NH order needs reconciliation; query daily executions before any new order')
            if notional and not policy.get('_account_independent'):
                if db.execute("SELECT 1 FROM intents WHERE scope=? AND operation='krstockOrderCashBuy' AND fingerprint NOT IN (SELECT fingerprint FROM swing_buys)", (self.scope,)).fetchone():
                    raise NHOrderBlocked('Earlier buys need reconciliation before enabling swing limits')
                # User decision 2026-11-11: the 5-symbol cap counts symbols bought today, not since the account began.
                totals = db.execute('''SELECT COALESCE(SUM(notional),0),
                    COALESCE(SUM(CASE WHEN day=? THEN notional ELSE 0 END),0), COUNT(DISTINCT CASE WHEN day=? THEN symbol END)
                    FROM swing_buys b WHERE scope=? AND NOT EXISTS
                    (SELECT 1 FROM pre_send_aborts a WHERE a.fingerprint=b.fingerprint)''', (day, day, self.scope)).fetchone()
                known = db.execute('''SELECT 1 FROM swing_buys b WHERE scope=? AND symbol=? AND day=? AND NOT EXISTS
                    (SELECT 1 FROM pre_send_aborts a WHERE a.fingerprint=b.fingerprint)''', (self.scope, symbol, day)).fetchone()
                # User decision 2026-11-13: the 21,000,000 KRW cap is on what is held now (cost of open positions), not on
                # every buy since the account began; the 6,000,000 KRW daily buy cap is unchanged.
                if (self.held_exposure(db) + notional > limits['held_max_krw'] or totals[1] + notional > limits['daily_buy_krw']
                        or (totals[2] >= limits['daily_new_symbols'] and not known)):
                    raise NHOrderBlocked('Swing cumulative buy limit exceeded')
                db.execute('INSERT INTO swing_buys VALUES (?,?,?,?,?)', (fingerprint, self.scope, day, symbol, notional))
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?,?,?)',
                       (fingerprint, self.scope, day, op, symbol, quantity, number, 'uncertain'))
            if intent_key:
                db.execute('INSERT INTO explicit_intents VALUES (?,?)', (intent_key, fingerprint))
        return fingerprint

    def abort_before_send(self, fingerprint):
        """Record a proven pre-transport abort without releasing ambiguous orders."""
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT scope,state,order_number FROM intents WHERE fingerprint=?', (fingerprint,)).fetchone()
            if not row or row['scope'] != self.scope or row['state'] != 'uncertain' or row['order_number'] is not None:
                raise NHOrderBlocked('Pre-send abort cannot be proven')
            db.execute('INSERT INTO pre_send_aborts VALUES (?,?)',
                       (fingerprint, datetime.now(ZoneInfo('Asia/Seoul')).isoformat()))
            db.execute("UPDATE intents SET state='terminal' WHERE fingerprint=? AND scope=?", (fingerprint, self.scope))

    def after(self, remote, arguments, payload, fingerprint):
        data = payload.get('structured_content', payload.get('data'))
        if not isinstance(data, dict) or payload.get('status') != 'ok':
            data = {}
        if fingerprint:
            output = data.get('Output_0')
            number = output.get('mkt_orr_no') if isinstance(output, dict) else None
            state = 'acknowledged' if type(number) is int and number > 0 else 'uncertain'
            with self.database() as db:
                if state == 'acknowledged':
                    db.execute('''UPDATE intents SET state=?, order_number=CASE WHEN operation='krstockOrderCancel'
                               THEN order_number ELSE ? END WHERE fingerprint=?''', (state, number, fingerprint))
            payload['order_safety'] = {'state': state, 'resubmit_allowed': False}
            if state == 'uncertain':
                payload['status'] = 'error'
                payload['error'] = 'NH submission outcome is uncertain; do not retry. Reconcile broker order history.'
            return
        if self.mode != 'paper' or remote != 'call_api' or arguments.get('operationId') != 'krstockInquiryDailyOrderExecution':
            return
        message = data.get('rsp_msg', '')
        # Unknown/error messages cannot certify a terminal broker state.
        if not isinstance(message, str) or message.strip().rstrip('.') not in {
            '조회가 완료되었습니다', '모의투자 조회가 완료되었습니다', '모의투자 마지막 자료입니다',
            '모의투자 조회가 계속 됩니다. 다음 버튼을 누르세요',  # XA109: a full page with more to follow (11/13 13:22)
        }:
            return
        fields = arguments.get('input', {})
        rows = data.get('Output_0')
        if fields.get('act_no') != self.account or not isinstance(rows, list):
            return
        with self.database() as db:
            pending = db.execute("SELECT * FROM intents WHERE scope=? AND day=? AND state!='terminal' AND order_number IS NOT NULL",
                                 (self.scope, fields.get('orr_dt'))).fetchall()
            for intent in pending:
                matches = [r for r in rows if r.get('itg_orr_no') == intent['order_number']
                           and r.get('iem_cd') == intent['symbol']]
                if len(matches) != 1:
                    continue
                row = matches[0]
                counts = [row.get(k) for k in ('tot_cns_qty', 'can_qty', 'ny_cns_qty', 'orr_qty')]
                if any(type(v) is not int or v < 0 for v in counts):
                    continue
                filled, cancelled, remaining, ordered = counts
                expected = ordered if intent['operation'] == 'krstockOrderCancel' else intent['quantity']
                if remaining == 0 and expected > 0 and ordered == expected and filled + cancelled == expected:
                    db.execute("UPDATE intents SET state='terminal' WHERE fingerprint=?", (intent['fingerprint'],))
                    if intent['operation'] != 'krstockOrderCancel':
                        db.execute('INSERT OR REPLACE INTO intent_fills VALUES (?,?)', (intent['fingerprint'], filled))
