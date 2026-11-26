"""Compare supplied decisions against the same recorded quotes in isolated virtual books."""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

from virtual_paper_broker import VirtualBroker

KST = ZoneInfo('Asia/Seoul')
ARMS = ('baseline', 'experience')
LIMIT_KEYS = ('order_krw', 'daily_buy_krw', 'daily_new_symbols', 'held_krw')


def _time(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.utcoffset() is None:
            raise ValueError('timezone required')
        return stamp.astimezone(KST)
    except (TypeError, ValueError):
        raise ValueError('invalid timestamp') from None


class RecordedQuote:
    def __init__(self):
        self.row = None

    def call_tool(self, tool, params):
        if tool != 'get_stock_price' or params.get('stock_code') != self.row['symbol']:
            raise ValueError('recorded quote unavailable')
        return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
            'iem_cd': self.row['symbol'], 'market_status': '1',
            'hoga_bsop_hour': self.row['source_at'][11:19], 'bidp': self.row['bid'], 'askp': self.row['ask']}}}


def equity_at(broker, quotes, at, fee):
    holdings = broker.holdings()
    if any(code not in quotes or not timedelta(0) <= at - quotes[code][0] <= timedelta(seconds=30)
           for code in holdings):
        return None
    return broker.cash() + sum(qty * quotes[code][1] * (1 - fee) for code, (qty, _) in holdings.items())


def replay(steps, *, cash, limits, fee):
    """Replay a finite sequence using caller-provided decisions and simulated marketable limit fills.

    This is an execution-scenario comparison. It does not run Jev or the full paper runner.
    """
    if (not isinstance(steps, list) or len(steps) > 10000 or type(cash) is not int or cash <= 0
            or not isinstance(limits, dict) or any(type(limits.get(k)) is not int or limits[k] <= 0 for k in LIMIT_KEYS)
            or type(fee) not in (int, float) or not math.isfinite(fee) or not 0 <= fee < 1):
        raise ValueError('invalid replay parameters')
    reader = RecordedQuote()
    clock = [None]
    books = {arm: VirtualBroker(':memory:', reader, cash=cash, clock=lambda: clock[0], limits=dict(limits), fee=fee)
             for arm in ARMS}
    events = {arm: [] for arm in ARMS}
    equity_curves = {arm: [] for arm in ARMS}
    last, seen, quotes = None, set(), {}
    try:
        for row in steps:
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or row['id'] in seen:
                raise ValueError('invalid or duplicate observation id')
            seen.add(row['id'])
            at, source, received = (_time(row.get(k)) for k in ('at', 'source_at', 'received_at'))
            symbol, bid, ask, decisions = (row.get(k) for k in ('symbol', 'bid', 'ask', 'decisions'))
            if (last is not None and at <= last or source > received or received > at
                    or at - source > timedelta(seconds=30) or source.date() != at.date()
                    or not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9]{6}', symbol)
                    or type(bid) is not int or type(ask) is not int or not 0 < bid <= ask
                    or not isinstance(decisions, dict) or set(decisions) != set(ARMS)):
                raise ValueError('invalid or unavailable observation')
            last, clock[0], reader.row = at, at, {**row, 'source_at': source.isoformat()}
            quotes[symbol] = (source, bid)
            for arm, broker in books.items():
                before = equity_at(broker, quotes, at, fee)
                decision = decisions[arm]
                if (not isinstance(decision, dict) or decision.get('action') not in ('WAIT', 'BUY', 'SELL')
                        or _time(decision.get('decided_at')) > at):
                    raise ValueError('invalid decision')
                action = decision['action']
                event = {'id': row['id'], 'at': at.isoformat(), 'action': action, 'status': 'wait'}
                if action != 'WAIT':
                    if (type(decision.get('qty')) is not int or decision['qty'] <= 0
                            or type(decision.get('limit')) is not int or decision['limit'] <= 0):
                        raise ValueError('invalid order')
                    pending = broker.db.execute('SELECT COUNT(*) FROM orders WHERE filled+cancelled<qty').fetchone()[0]
                    if pending:
                        event['status'] = 'blocked_pending'
                    else:
                        response = broker.call_tool('call_api', {
                            'operationId': 'krstockOrderCashBuy' if action == 'BUY' else 'krstockOrderCashSell',
                            'input': {'iem_cd': symbol, 'orr_qty': decision['qty'], 'orr_pr': decision['limit']}})
                        if response.get('status') != 'ok':
                            event['status'] = 'refused'
                            event['reason'] = response.get('error')
                        else:
                            number = response['data']['Output_0']['mkt_orr_no']
                            filled, price = broker.db.execute('SELECT filled,fill_price FROM orders WHERE number=?', (number,)).fetchone()
                            event.update(order_id=number, status='filled' if filled else 'accepted_unfilled',
                                         filled=filled, fill_price=price)
                events[arm].append(event)
                equity_curves[arm].append({'at': at.isoformat(), 'before_krw': before,
                                           'after_krw': equity_at(broker, quotes, at, fee)})
        result = {}
        for arm, broker in books.items():
            holdings = broker.holdings()
            equity = equity_at(broker, quotes, last, fee) if last is not None else broker.cash()
            peak = float(cash)
            worst_krw = worst_pct = 0.0
            missing = 0
            for point in equity_curves[arm]:
                for value in (point['before_krw'], point['after_krw']):
                    if value is None:
                        missing += 1
                        continue
                    peak = max(peak, value)
                    worst_krw = max(worst_krw, peak - value)
                    worst_pct = max(worst_pct, 100 * (peak - value) / peak)
            orders, fills, pending = broker.db.execute('SELECT COUNT(*),COALESCE(SUM(filled>0),0),'
                                                       'COALESCE(SUM(filled+cancelled<qty),0) FROM orders').fetchone()
            result[arm] = {'cash': broker.cash(), 'holdings': holdings, 'orders': orders, 'fills': fills,
                           'pending': pending, 'events': events[arm],
                           'realized_krw': broker.cash() - cash if not holdings else None,
                           'liquidation_value_krw': equity, 'equity_curve': equity_curves[arm],
                           'sampled_max_drawdown_krw': worst_krw, 'sampled_max_drawdown_pct': worst_pct,
                           'missing_equity_marks': missing}
        return result
    finally:
        for broker in books.values():
            broker.db.close()
