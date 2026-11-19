"""Research-only checks for a single paper position; never submit orders.

Callers must obtain account scope, complete broker snapshots and guard state from
authenticated read-only sources. These checks cannot authenticate input data.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from etf_universe import SYMBOLS

KST = ZoneInfo('Asia/Seoul')


def _stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware time required')
    return value.astimezone(KST)


def _identity(value, length):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{' + str(length) + '}', value) is not None


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError('invalid price')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid price') from exc
    if not result.is_finite() or result <= 0:
        raise ValueError('invalid price')
    return result


def position_from_entry(scope, journal_scope, intent, fill, link):
    """Build a position only from matching, terminal, locally confirmed records.

    The fill time is the local confirmation time, not a broker execution time.
    """
    if not _identity(scope, 64) or journal_scope != scope:
        raise ValueError('paper account scope mismatch')
    if not all(isinstance(row, dict) for row in (intent, fill, link)):
        raise ValueError('missing entry evidence')
    order_id, cycle_id = intent.get('order_id'), link.get('cycle_id')
    if (not _identity(order_id, 32) or not _identity(cycle_id, 32)
            or link.get('order_id') != order_id or fill.get('order_id') != order_id
            or intent.get('status') != 'terminal' or intent.get('side') != 'BUY'
            or type(intent.get('order_number')) is not int or intent['order_number'] <= 0
            or fill.get('side') != 'BUY' or intent.get('symbol') not in SYMBOLS.values()
            or fill.get('symbol') != intent['symbol']
            or type(intent.get('quantity')) is not int or intent['quantity'] != 1
            or type(fill.get('quantity')) is not int or fill['quantity'] != 1):
        raise ValueError('entry records do not identify a single verified fill')
    price, confirmed = _price(fill.get('price')), _stamp(fill.get('time'))
    return {'scope': scope, 'symbol': intent['symbol'], 'quantity': 1,
            'entry_price': str(price), 'confirmed_at': confirmed.isoformat(),
            'order_id': order_id, 'order_number': intent['order_number'], 'cycle_id': cycle_id}


def _position(position):
    if (not isinstance(position, dict) or not _identity(position.get('scope'), 64)
            or not _identity(position.get('order_id'), 32) or not _identity(position.get('cycle_id'), 32)
            or type(position.get('order_number')) is not int or position['order_number'] <= 0
            or position.get('symbol') not in SYMBOLS.values()
            or type(position.get('quantity')) is not int or position['quantity'] != 1):
        raise ValueError('invalid position')
    return _price(position.get('entry_price')), _stamp(position.get('confirmed_at'))


def _fresh(value, now, limit):
    delta = now - _stamp(value)
    return timedelta(0) <= delta <= limit


def review_preclose(now, position, quote, pending, news, decision, jev):
    """Return research evidence for preclose review, never an order instruction."""
    result = {'state': 'blocked', 'reason': 'invalid_evidence', 'purpose': 'research_only'}
    try:
        now = _stamp(now)
        entry, confirmed = _position(position)
        if confirmed > now or now.weekday() >= 5 or not '14:50' <= now.strftime('%H:%M') < '15:00':
            return result
        result.update(time=now.isoformat(), scope=position['scope'], cycle_id=position['cycle_id'])
        if type(pending) is not int or pending < 0:
            return result
        if pending:
            return {**result, 'state': 'reconciliation_required', 'reason': 'order_reconciliation_required'}
        if (not isinstance(quote, dict) or quote.get('symbol') != position['symbol']
                or quote.get('market_status') != '1' or not _fresh(quote.get('time'), now, timedelta(seconds=120))
                or _stamp(quote['time']) < confirmed):
            return result
        bid = _price(quote.get('bid'))
        if bid <= entry * Decimal('0.98') or bid >= entry * Decimal('1.02'):
            return {**result, 'state': 'protect_sell', 'reason': 'price_exit_threshold'}
        if (not isinstance(news, dict) or news.get('complete') is not True
                or not _fresh(news.get('time'), now, timedelta(hours=24))):
            return {**result, 'state': 'unreviewed_hold', 'reason': 'news_unavailable'}
        for model, minimum in ((decision, 0.8), (jev, 0.7)):
            if (not isinstance(model, dict) or model.get('symbol') != position['symbol']
                    or model.get('choice') not in ('HOLD', 'SELL')
                    or type(model.get('confidence')) not in (int, float)
                    or not math.isfinite(model['confidence']) or not minimum <= model['confidence'] <= 1
                    or not _fresh(model.get('time'), now, timedelta(seconds=60))
                    or _stamp(model['time']) < confirmed):
                return {**result, 'state': 'unreviewed_hold', 'reason': 'model_unavailable'}
            if _stamp(news['time']) > _stamp(model['time']):
                return {**result, 'state': 'unreviewed_hold', 'reason': 'news_after_model'}
        if decision['choice'] != jev['choice']:
            return {**result, 'state': 'unreviewed_hold', 'reason': 'model_disagreement'}
        return {**result, 'state': 'reviewed_sell' if decision['choice'] == 'SELL' else 'reviewed_hold',
                'reason': 'models_agree'}
    except (KeyError, TypeError, ValueError, OverflowError):
        return result


def verify_overnight(now, position, close_review, broker, guard):
    """Assess next-session consistency; a True result is not order authorization."""
    result = {'state': 'blocked', 'reason': 'unverified', 'purpose': 'research_only'}
    try:
        now = _stamp(now)
        _, confirmed = _position(position)
        if (now.weekday() >= 5 or not '09:10' <= now.strftime('%H:%M') < '15:00'
                or confirmed.date() >= now.date() or not isinstance(close_review, dict)
                or close_review.get('state') not in ('reviewed_hold', 'unreviewed_hold')
                or close_review.get('scope') != position['scope']
                or close_review.get('cycle_id') != position['cycle_id']):
            return result
        review_time = _stamp(close_review.get('time'))
        if (review_time.date() < confirmed.date() or review_time.date() >= now.date()
                or not '14:50' <= review_time.strftime('%H:%M') < '15:00'):
            return result
        if (not isinstance(broker, dict) or broker.get('scope') != position['scope']
                or broker.get('account_type') != '03' or broker.get('session_confirmed') is not True
                or broker.get('holdings_complete') is not True
                or broker.get('previous_orders_complete') is not True or broker.get('current_orders_complete') is not True
                or broker.get('history_since') != confirmed.date().isoformat()
                or broker.get('history_through') != (now.date() - timedelta(days=1)).isoformat()
                or not _fresh(broker.get('time'), now, timedelta(seconds=120))):
            return result
        holdings = broker.get('holdings')
        previous_orders, current_orders = broker.get('previous_orders'), broker.get('current_orders')
        if not isinstance(holdings, list) or len(holdings) != 1 or not isinstance(holdings[0], dict):
            return result
        holding = holdings[0]
        if (holding.get('symbol') != position['symbol'] or type(holding.get('quantity')) is not int
                or holding['quantity'] != position['quantity']
                or not isinstance(previous_orders, list) or not isinstance(current_orders, list)):
            return result
        if any(not isinstance(order, dict) or type(order.get('remaining')) is not int or order['remaining'] != 0
               for order in previous_orders + current_orders):
            return result
        entries = [order for order in previous_orders if order.get('order_number') == position['order_number']]
        if (len(entries) != 1 or entries[0].get('symbol') != position['symbol']
                or entries[0].get('side') != 'BUY' or type(entries[0].get('quantity')) is not int
                or entries[0]['quantity'] != position['quantity']
                or type(entries[0].get('filled')) is not int or entries[0]['filled'] != position['quantity']):
            return result
        if (not isinstance(guard, dict) or guard.get('scope') != position['scope']
                or guard.get('owner') != 'nhplug_paper_isolated' or guard.get('halt') is not False
                or type(guard.get('unresolved')) is not int or guard['unresolved'] != 0
                or type(guard.get('local_unresolved')) is not int or guard['local_unresolved'] != 0
                or not _fresh(guard.get('time'), now, timedelta(seconds=120))):
            return result
        known = guard.get('known_order_numbers')
        if (not isinstance(known, list) or not known
                or any(type(number) is not int or number <= 0 for number in known)
                or len(known) != len(set(known))
                or any(type(order.get('order_number')) is not int or order['order_number'] not in known
                       for order in previous_orders + current_orders)):
            return result
        return {**result, 'state': 'reconciled', 'reason': 'broker_and_guard_match'}
    except (KeyError, TypeError, ValueError, OverflowError):
        return result
