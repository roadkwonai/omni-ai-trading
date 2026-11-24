import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import nh_paper_isolated as p
from virtual_paper_broker import VirtualBroker

KST = ZoneInfo('Asia/Seoul')
ACCOUNT = '00000000000'


class Read:
    def __init__(self):
        self.prices = {'005930': (50_000, 50_100)}

    def call_tool(self, tool, params):
        bid, ask = self.prices[params['stock_code']]
        return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
            'iem_cd': params['stock_code'], 'market_status': '1', 'hoga_bsop_hour': datetime.now(KST).strftime('%H:%M:%S'),
            'bidp': bid, 'askp': ask}}}


class VirtualBrokerTests(unittest.TestCase):
    def setUp(self):
        self._session = patch.object(p, 'order_session', return_value=True)  # fills at any wall-clock time
        self._session.start()
        self.addCleanup(self._session.stop)
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.read = Read()
        self.broker = VirtualBroker(root / 'virtual.sqlite3', self.read, cash=1_000_000)
        self.db = p.connect_journal(root / 'orders.sqlite3', p.account_hash(ACCOUNT))
        self.day = datetime.now(KST).strftime('%Y%m%d')

    def tearDown(self):
        self.db.close()
        self.broker.db.close()
        self.temp.cleanup()

    def order(self, side, qty, price, check=lambda: True):
        return p.submit(self.db, self.broker, ACCOUNT, self.day, '005930', side, qty, price, etf7=True,
                        signal_check=check, deadline_ms=2**52, allowed={'005930'})

    def settle(self):
        row = self.db.execute("SELECT id,day,symbol,side,qty,price,status,order_number,created FROM intents WHERE status!='terminal'").fetchone()
        return p.reconcile(self.db, self.broker, ACCOUNT, row, stopping=True)

    def test_marketable_orders_fill_into_the_runner_journal(self):
        self.order('BUY', 10, 50_100)
        self.settle()
        self.assertEqual(p.owned_positions(self.db), {'005930': 10})
        self.assertEqual(self.broker.cash(), 1_000_000 - 501_000)
        balance = p.paper_pages(self.broker, 'krstockInquiryBalance', {'act_no': ACCOUNT}, 'Output_1')
        p.verify_positions(balance['Output_1'], {'005930': 10}, etf7=True)
        self.assertEqual(balance['Output_1'][0]['eal_pls_amt'], -1000)  # valued at the bid
        self.order('SELL', 4, 50_000)
        self.settle()
        self.assertEqual(p.owned_positions(self.db), {'005930': 6})
        fills = self.db.execute('SELECT side,quantity,price FROM fills ORDER BY rowid').fetchall()
        self.assertEqual(fills, [('BUY', 10, 50_100.0), ('SELL', 4, 50_000.0)])

    def test_resting_order_is_cancelled_by_reconcile(self):
        self.order('BUY', 1, 49_000)  # below the ask: rests
        self.assertFalse(self.settle())  # stopping=True: cancels
        self.settle()
        self.assertEqual(p.owned_positions(self.db), {})
        self.assertEqual(self.broker.cash(), 1_000_000)

    def test_expired_signal_and_impossible_orders_never_reach_the_book(self):
        with self.assertRaises(p.PaperOrderNotSent):
            self.order('BUY', 1, 50_100, check=lambda: False)
        with self.assertRaises(p.PaperOrderGuardRefused):
            self.order('SELL', 1, 50_000)  # nothing held
        with self.assertRaises(p.PaperOrderGuardRefused):
            self.order('BUY', 100, 50_100)  # beyond virtual cash
        self.assertEqual(self.broker.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)
        self.assertIsNone(self.db.execute("SELECT 1 FROM intents WHERE status!='terminal'").fetchone())


    def test_shared_guard_limits_refuse_before_the_book(self):
        broker = VirtualBroker(Path(self.temp.name) / 'limited.sqlite3', self.read, cash=50_000_000,
                               limits={'order_krw': 3_000_000, 'daily_buy_krw': 6_000_000, 'daily_new_symbols': 1})
        order = lambda symbol, qty: broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {
            'iem_cd': symbol, 'orr_qty': qty, 'orr_pr': 50_100}})
        self.assertIn('single order', order('005930', 60)['error'])
        self.assertEqual(order('005930', 59)['status'], 'ok')
        self.read.prices['069500'] = (30_000, 30_050)
        self.assertIn('new symbol', order('069500', 1)['error'])
        self.assertEqual(order('005930', 59)['status'], 'ok')
        self.assertIn('daily buy', order('005930', 2)['error'])  # 5,911,800 + 100,200 > 6,000,000
        broker.db.close()

    def test_limits_are_read_per_order_and_cap_the_held_amount(self):
        current = {'order_krw': 10_000_000, 'daily_buy_krw': 50_000_000, 'daily_new_symbols': 5, 'held_krw': 6_000_000}
        broker = VirtualBroker(Path(self.temp.name) / 'live-limits.sqlite3', self.read, cash=50_000_000, limits=lambda: current)
        order = lambda qty: broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {
            'iem_cd': '005930', 'orr_qty': qty, 'orr_pr': 50_100}})
        self.assertEqual(order(100)['status'], 'ok')  # 5,010,000 held
        self.assertIn('held amount', order(20)['error'])  # 5,010,000 + 1,002,000 > 6,000,000
        current['held_krw'] = 10_000_000  # the dashboard raised it: the next order sees it
        self.assertEqual(order(20)['status'], 'ok')
        broker.db.close()

    def test_vibe_virtual_runner_uses_current_shared_order_limit(self):
        import vibe_runner as vibe

        record = {'limits': {'order_max_krw': 1_000_000}}

        def check(_db, _read, broker, _account, _root, _once, **_kwargs):
            fields = {'iem_cd': '005930', 'orr_qty': 30, 'orr_pr': 50_100}
            with self.assertRaisesRegex(ValueError, 'single order'):
                broker.order('BUY', fields)
            record['limits']['order_max_krw'] = 2_000_000
            self.assertEqual(broker.order('BUY', fields)['status'], 'ok')
            broker.db.close()
            _db.close()

        with patch('accounts.load', return_value={'paper-shared': record}), \
             patch('src.tools.mcp.MCPServerAdapter', return_value=self.read), patch.object(vibe, 'loop', side_effect=check):
            vibe.run_virtual({'nhplug_readonly': object()}, Path(self.temp.name))

    def test_held_limit_includes_unfilled_buy_reservations(self):
        broker = VirtualBroker(Path(self.temp.name) / 'pending.sqlite3', self.read, cash=10_000_000,
                               limits={'held_krw': 1_000_000})
        fields = {'iem_cd': '005930', 'orr_qty': 10, 'orr_pr': 50_000}
        self.assertEqual(broker.order('BUY', fields)['status'], 'ok')  # below ask: remains open
        with self.assertRaisesRegex(ValueError, 'held amount'):
            broker.order('BUY', {**fields, 'orr_qty': 11})
        broker.db.close()


if __name__ == '__main__':
    unittest.main()


def test_a_buy_that_cannot_pay_its_fee_is_refused_up_front():
    import tempfile
    from pathlib import Path
    from virtual_paper_broker import VirtualBroker
    with tempfile.TemporaryDirectory() as d:
        broker = VirtualBroker(Path(d) / 'v.sqlite3', None, cash=1_000, fee=0.01)
        response = broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '379810', 'orr_qty': 10, 'orr_pr': 100}})
        assert response['status'] == 'error' and 'cash' in response['error']


class Limited(Read):
    """NH answers the first `limited` quote calls with the MCP rate-limit error (429 → category rate_limit)."""
    def __init__(self, limited):
        super().__init__()
        self.limited, self.calls = limited, 0

    def call_tool(self, tool, params):
        self.calls += 1
        if self.calls <= self.limited:
            return {'status': 'error', 'error': '오류: 호출 유량을 초과했습니다.\n{"category": "rate_limit", "code": "IGW42902"}'}
        return super().call_tool(tool, params)


def test_a_rate_limited_quote_is_asked_again_and_other_errors_are_not(tmp_path, monkeypatch):
    import virtual_paper_broker as V
    monkeypatch.setattr(V, 'RATE_LIMIT_WAIT', (0, 0))
    read = Limited(2)
    broker = VirtualBroker(tmp_path / 'v.sqlite3', read)
    assert broker.quote('005930') == (50_000, 50_100) and read.calls == 3
    read = Limited(5)
    import pytest
    with pytest.raises(ValueError, match='quote unavailable'):
        VirtualBroker(tmp_path / 'w.sqlite3', read).quote('005930')
    assert read.calls == 3  # two retries, then the step waits for its next check

    class Down(Read):
        def call_tool(self, tool, params):
            self.calls = getattr(self, 'calls', 0) + 1
            return {'status': 'error', 'error': 'unknown'}
    down = Down()
    with pytest.raises(ValueError):
        VirtualBroker(tmp_path / 'x.sqlite3', down).quote('005930')
    assert down.calls == 1


def test_an_order_that_does_not_fill_keeps_why_in_the_history(tmp_path):
    # 2026-11-18·19 long-term 적립식 buys rested and were cancelled with no reason on record (weekly audit W47, A1)
    read = Read()
    broker = VirtualBroker(tmp_path / 'v.sqlite3', read, cash=1_000_000)
    with patch.object(p, 'order_session', return_value=True):
        broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '005930', 'orr_qty': 1, 'orr_pr': 50_000}})
        day = broker.clock().strftime('%Y%m%d')
        row = broker.call_tool('call_api', {'operationId': 'krstockInquiryDailyOrderExecution', 'input': {'orr_dt': day}})['data']['Output_0'][0]
    assert row['tot_cns_qty'] == 0 and '50,100' in row['note'] and '50,000' in row['note']


def test_a_slow_answer_is_judged_by_the_time_it_arrived(tmp_path):
    # 2026-11-24 10:30: NH took 5.8 s, so a quote stamped on arrival looked more than 5 s ahead of the clock read before the call
    from datetime import timedelta
    start, waited = datetime.now(KST).replace(hour=10, minute=30, second=0, microsecond=0), [timedelta(0)]

    class Slow(Read):
        stamp = None

        def call_tool(self, tool, params):
            waited[0] += timedelta(seconds=5.8)
            response = super().call_tool(tool, params)
            response['data']['Output_0']['hoga_bsop_hour'] = self.stamp or (start + waited[0]).strftime('%H:%M:%S')
            return response
    read = Slow()
    broker = VirtualBroker(tmp_path / 'v.sqlite3', read, clock=lambda: start + waited[0])
    read.stamp = (start + timedelta(seconds=6)).strftime('%H:%M:%S')  # the stamp is NH's clock, about the arrival time
    assert broker.quote('005930') == (50_000, 50_100)
    read.stamp = (start - timedelta(seconds=150)).strftime('%H:%M:%S')  # an old quote is still refused
    try:
        broker.quote('005930')
    except ValueError as exc:
        assert 'stale' in str(exc)
    else:
        raise AssertionError('an old quote was accepted')
