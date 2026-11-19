import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from etf_swing_state import position_from_entry, review_preclose, verify_overnight

KST = ZoneInfo('Asia/Seoul')
SCOPE, ORDER, CYCLE = 'a' * 64, 'b' * 32, 'c' * 32
ENTRY = datetime(2026, 11, 11, 10, 0, tzinfo=KST)
CLOSE = datetime(2026, 11, 11, 14, 55, tzinfo=KST)
NEXT = datetime(2026, 11, 12, 9, 20, tzinfo=KST)


def evidence():
    intent = {'order_id': ORDER, 'status': 'terminal', 'side': 'BUY', 'symbol': '114800', 'quantity': 1, 'order_number': 101}
    fill = {'order_id': ORDER, 'side': 'BUY', 'symbol': '114800', 'quantity': 1,
            'price': 100, 'time': ENTRY.isoformat()}
    link = {'order_id': ORDER, 'cycle_id': CYCLE}
    return intent, fill, link


def position():
    return position_from_entry(SCOPE, SCOPE, *evidence())


def close_inputs(price=100, decision='HOLD', jev='HOLD'):
    quote = {'symbol': '114800', 'bid': price, 'time': CLOSE.isoformat(), 'market_status': '1'}
    news = {'complete': True, 'time': CLOSE.isoformat()}
    a = {'symbol': '114800', 'choice': decision, 'confidence': 0.8, 'time': CLOSE.isoformat()}
    b = {'symbol': '114800', 'choice': jev, 'confidence': 0.7, 'time': CLOSE.isoformat()}
    return quote, news, a, b


def restart_inputs():
    broker = {'scope': SCOPE, 'account_type': '03', 'session_confirmed': True,
              'holdings_complete': True, 'previous_orders_complete': True, 'current_orders_complete': True,
              'history_since': ENTRY.date().isoformat(), 'history_through': CLOSE.date().isoformat(),
              'time': NEXT.isoformat(), 'holdings': [{'symbol': '114800', 'quantity': 1}],
              'previous_orders': [{'order_number': 101, 'symbol': '114800', 'side': 'BUY',
                                   'quantity': 1, 'filled': 1, 'remaining': 0}], 'current_orders': []}
    guard = {'scope': SCOPE, 'owner': 'nhplug_paper_isolated', 'halt': False,
             'unresolved': 0, 'local_unresolved': 0, 'known_order_numbers': [101], 'time': NEXT.isoformat()}
    return broker, guard


class SwingStateTests(unittest.TestCase):
    def test_entry_requires_matching_terminal_single_share_and_cycle(self):
        self.assertEqual(position()['symbol'], '114800')
        for index, change in ((0, {'status': 'acknowledged'}), (1, {'quantity': 2}),
                              (1, {'order_id': 'd' * 32}), (2, {'cycle_id': None}),
                              (1, {'price': float('nan')}), (1, {'time': '2026-11-11T10:00:00'})):
            records = list(evidence())
            records[index] = {**records[index], **change}
            with self.subTest(index=index, change=change), self.assertRaises(ValueError):
                position_from_entry(SCOPE, SCOPE, *records)
        with self.assertRaises(ValueError):
            position_from_entry(SCOPE, 'f' * 64, *evidence())

    def test_threshold_exit_precedes_news_and_models(self):
        quote, _, _, _ = close_inputs(98)
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, None, None, None)['state'], 'protect_sell')
        quote['bid'] = 102
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, None, None, None)['state'], 'protect_sell')
        quote['bid'] = 100
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, None, None, None)['state'], 'unreviewed_hold')

    def test_preclose_requires_fresh_agreement(self):
        quote, news, a, b = close_inputs(decision='SELL', jev='SELL')
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, news, a, b)['state'], 'reviewed_sell')
        b['choice'] = 'HOLD'
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, news, a, b)['reason'], 'model_disagreement')
        b['time'] = (CLOSE - timedelta(seconds=61)).isoformat()
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, news, a, b)['state'], 'unreviewed_hold')
        b['time'] = (CLOSE + timedelta(seconds=1)).isoformat()
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, news, a, b)['state'], 'unreviewed_hold')
        b['time'] = (CLOSE - timedelta(seconds=1)).isoformat()
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, news, a, b)['reason'], 'news_after_model')

    def test_pending_or_invalid_quote_never_proves_exit(self):
        quote, news, a, b = close_inputs(98)
        self.assertEqual(review_preclose(CLOSE, position(), quote, 1, news, a, b)['state'], 'reconciliation_required')
        quote['time'] = (CLOSE - timedelta(seconds=121)).isoformat()
        self.assertEqual(review_preclose(CLOSE, position(), quote, 0, news, a, b)['state'], 'blocked')
        self.assertEqual(review_preclose(CLOSE.replace(hour=15), position(), quote, 0, news, a, b)['state'], 'blocked')

    def test_preclose_evidence_cannot_precede_confirmed_entry(self):
        intent, fill, link = evidence()
        fill['time'] = (CLOSE-timedelta(seconds=5)).isoformat()
        late_position = position_from_entry(SCOPE, SCOPE, intent, fill, link)
        quote, news, a, b = close_inputs()
        quote['time'] = (CLOSE-timedelta(seconds=10)).isoformat()
        self.assertEqual(review_preclose(CLOSE, late_position, quote, 0, news, a, b)['state'], 'blocked')
        quote['time'] = CLOSE.isoformat()
        a['time'] = (CLOSE-timedelta(seconds=10)).isoformat()
        self.assertEqual(review_preclose(CLOSE, late_position, quote, 0, news, a, b)['state'], 'unreviewed_hold')

    def test_next_day_requires_complete_matching_evidence(self):
        quote, news, a, b = close_inputs()
        review = review_preclose(CLOSE, position(), quote, 0, news, a, b)
        broker, guard = restart_inputs()
        self.assertEqual(verify_overnight(NEXT, position(), review, broker, guard)['state'], 'reconciled')
        for key, bad in (('scope', 'f' * 64), ('account_type', '01'), ('session_confirmed', False),
                         ('previous_orders_complete', False), ('current_orders_complete', False),
                         ('history_since', '2026-11-10'), ('history_through', '2026-11-10'),
                         ('previous_orders', []),
                         ('holdings_complete', False), ('holdings', []),
                         ('holdings', [{'symbol': '069500', 'quantity': 1}]),
                         ('current_orders', [{'remaining': 1}]),
                         ('current_orders', [{'order_number': 102, 'remaining': 0}]),
                         ('time', (NEXT-timedelta(seconds=121)).isoformat())):
            with self.subTest(key=key):
                self.assertEqual(verify_overnight(NEXT, position(), review, {**broker, key: bad}, guard)['state'], 'blocked')
        for key, bad in (('scope', 'f' * 64), ('owner', 'nhplug_paper'), ('halt', True),
                         ('unresolved', 1), ('local_unresolved', 1), ('unresolved', False),
                         ('known_order_numbers', [101, 101]), ('known_order_numbers', [])):
            with self.subTest(key=key):
                self.assertEqual(verify_overnight(NEXT, position(), review, broker, {**guard, key: bad})['state'], 'blocked')

    def test_prior_review_identity_and_future_time_fail_closed(self):
        quote, news, a, b = close_inputs()
        review = review_preclose(CLOSE, position(), quote, 0, news, a, b)
        broker, guard = restart_inputs()
        for change in ({'cycle_id': 'd' * 32}, {'scope': 'f' * 64}, {'state': 'reviewed_sell'},
                       {'time': NEXT.isoformat()}, {'time': '2026-11-11T14:55:00'}):
            self.assertEqual(verify_overnight(NEXT, position(), {**review, **change}, broker, guard)['state'], 'blocked')


if __name__ == '__main__':
    unittest.main()
