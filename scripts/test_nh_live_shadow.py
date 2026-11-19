import unittest
import unittest.mock

import nh_live_shadow as s


class AccountShadowTests(unittest.TestCase):
    def setUp(self):  # the card reads the mirror's state under the runtime folder: never the operator's real one
        import os, tempfile
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        env = unittest.mock.patch.dict(os.environ, {'VIBE_TRADING_HOME': runtime.name})
        env.start()
        self.addCleanup(env.stop)

    def test_the_account_mirrors_its_ledger_fills_one_share_per_tranche(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [('a', '251340', 'BUY', 526, 2850, '2026-11-11T14:02:25+09:00'),
                 ('b', '251340', 'BUY', 474, 2875, '2026-11-12T10:18:35+09:00'),
                 ('c', '007660', 'BUY', 10, 87000, '2026-11-12T10:20:00+09:00'),
                 ('d', '251340', 'SELL', 500, 2900, '2026-11-12T11:00:00+09:00'),
                 ('e', '251340', 'SELL', 500, 2880, '2026-11-12T14:41:00+09:00')]
        events = [{'time': '2026-11-12T14:30:00+09:00', 'event': 'market_context', 'market_bars': {'KQ150INV': [{'stck_prpr': '2890'}]}}]
        record = {'engine': 'jev', 'budget': {'capital_krw': 1_000_000, 'order_price_max_krw': 50_000, 'daily_loss_krw': 20_000}}
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(Path(d) / 'orders.sqlite3')
            db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills); db.commit(); db.close()
            with unittest.mock.patch('nh_live_follow.paper_source', return_value=Path(d) / 'orders.sqlite3'):
                result = s.account_shadow(record, events, '2026-11-12')
            with unittest.mock.patch('nh_live_follow.paper_source', return_value=Path(d) / 'missing.sqlite3'):
                empty = s.account_shadow(record, events, '2026-11-12')
        # yesterday's tranche is carried (1 share @2850); today's second tranche adds one; the half sale leaves one
        self.assertEqual([(t['side'], t['symbol'], t['qty'], t['price']) for t in result['trades']],
                         [('BUY', '251340', 1, 2875), ('SELL', '251340', 1, 2900), ('SELL', '251340', 1, 2880)])
        self.assertEqual(result['realized'], 55)  # (2900 + 2880 - 2 * 2862.5)
        self.assertEqual(result['skipped_entries'], {'주문가격 50,000원 초과': 1})
        self.assertEqual(result['open'], {})
        self.assertEqual((empty['trades'], empty['open']), ([], {}))

    def test_a_swing_carried_by_the_ledger_stays_open_until_the_ledger_sells(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [('a', '069500', 'BUY', 100, 1000, '2026-11-10T10:00:01+09:00'),
                 ('b', '069500', 'SELL', 100, 1012, '2026-11-13T14:40:30+09:00')]
        record = {'engine': 'jev', 'budget': {'capital_krw': 1_000_000, 'order_price_max_krw': 10_000, 'daily_loss_krw': 20_000}}
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(Path(d) / 'orders.sqlite3')
            db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills); db.commit(); db.close()
            with unittest.mock.patch('nh_live_follow.paper_source', return_value=Path(d) / 'orders.sqlite3'):
                held = s.account_shadow(record, [], '2026-11-12')
                sold = s.account_shadow(record, [], '2026-11-13')
        self.assertEqual(held['open']['069500']['qty'], 1)  # third night: still mirrored
        self.assertEqual((sold['trades'][-1]['side'], sold['trades'][-1]['pnl']), ('SELL', 12))


class EffectiveRulesTests(unittest.TestCase):
    def test_follow_paper_takes_paper_settings_but_keeps_the_live_stop_cap(self):
        paper = {'sell_confidence': 0.6, 'stop_loss_pct': 3.0, 'take_profit_all_pct': 2.5}
        with unittest.mock.patch('strategy_settings.read_settings', return_value=(paper, 'custom')):
            rules = s.effective_rules({'rules': {'follow_paper': True, 'stop_loss_pct': 1.0}})
            own = s.effective_rules({'rules': {'follow_paper': False, 'stop_loss_pct': 1.0}})
        self.assertEqual((rules['sell_confidence'], rules['stop_loss_pct'], rules['take_profit_pct']), (0.6, 1.0, 2.5))  # account 1% is tighter
        with unittest.mock.patch('strategy_settings.read_settings', return_value=(paper, 'custom')):
            wide = s.effective_rules({'rules': {'follow_paper': True, 'stop_loss_pct': 2.0}})
        self.assertEqual(wide['stop_loss_pct'], 2.0)  # paper 3% never widens past the approved 2%
        self.assertEqual(own['stop_loss_pct'], 1.0)


if __name__ == '__main__':
    unittest.main()


class LedgerStopTests(unittest.TestCase):
    def test_overnight_is_same_simulated_position_generation_not_today_approval(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [
            ('a', '069500', 'BUY', 100, 1000, '2026-11-11T14:00:00+09:00'),
            ('b', '069500', 'SELL', 50, 1020, '2026-11-12T09:30:00+09:00'),
            ('c', '069500', 'BUY', 100, 1010, '2026-11-12T10:00:00+09:00'),
            ('d', '114800', 'BUY', 100, 1000, '2026-11-11T14:00:00+09:00'),
            ('e', '114800', 'SELL', 100, 1000, '2026-11-12T09:30:00+09:00'),
            ('f', '114800', 'BUY', 100, 1010, '2026-11-12T10:00:00+09:00'),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'orders.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills)
            result = s.ledger_shadow(path, '2026-11-12', [], 1_000_000, 10_000, shares_for=lambda price, qty=1, fill=None, nth=1: 100)
        self.assertTrue(result['open']['069500']['overnight'])  # partial sale and add keep the generation
        self.assertFalse(result['open']['114800']['overnight'])  # flat before today's re-entry
        self.assertEqual(result['mode'], 'observation_only_no_orders')

    def test_overnight_requires_a_known_korean_opening_day(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [
            ('a', '069500', 'BUY', 100, 1000, '2026-11-11T15:30:00+00:00'),
            ('b', '114800', 'BUY', 100, 1000, '2026-11-11T14:00:00'),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'orders.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills)
            result = s.ledger_shadow(path, '2026-11-12', [], 1_000_000, 10_000)
        self.assertFalse(result['open']['069500']['overnight'])  # UTC is already Nov 12 in Seoul
        self.assertIsNone(result['open']['114800']['overnight'])  # no timezone means the date is unknown

    def test_overnight_uses_simulated_holding_not_a_skipped_paper_buy(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [
            ('a', '069500', 'BUY', 100, 11000, '2026-11-11T14:00:00+09:00'),
            ('b', '069500', 'BUY', 100, 1000, '2026-11-12T10:00:00+09:00'),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'orders.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills)
            result = s.ledger_shadow(path, '2026-11-12', [], 1_000_000, 10_000)
        self.assertFalse(result['open']['069500']['overnight'])

    def test_rebuy_after_partial_sale_matches_mirror_tranches(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [('a', '069500', 'BUY', 100, 1000, '2026-11-12T10:00:00+09:00'),
                 ('b', '069500', 'SELL', 50, 1020, '2026-11-12T10:10:00+09:00'),
                 ('c', '069500', 'BUY', 1, 1000, '2026-11-12T10:20:00+09:00')]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'orders.sqlite3'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills); db.commit(); db.close()
            result = s.ledger_shadow(path, '2026-11-12', [], 1_000_000, 10_000,
                                     shares_for=lambda price, qty=1, fill=None, nth=1: 100_000 // price)
        self.assertEqual([(t['side'], t['qty']) for t in result['trades']],
                         [('BUY', 100), ('SELL', 50), ('BUY', 100)])
        self.assertEqual(result['open']['069500']['qty'], 150)

    def test_live_stop_on_recorded_closes_latches_for_the_day_and_amount_sizing(self):
        import sqlite3, tempfile
        from pathlib import Path
        fills = [('a', '069500', 'BUY', 100, 1000, '2026-11-12T10:00:00+09:00'),
                 ('b', '069500', 'BUY', 100, 970, '2026-11-12T10:40:00+09:00')]
        closes = lambda t, p: {'time': f'2026-11-12T{t}+09:00', 'event': 'market_context', 'market_bars': {'K200': [{'stck_prpr': str(p)}]}}
        events = [closes('10:01:00', 1000), closes('10:30:00', 975), closes('10:41:00', 972)]
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(Path(d) / 'orders.sqlite3')
            db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills); db.commit(); db.close()
            one = s.ledger_shadow(Path(d) / 'orders.sqlite3', '2026-11-12', events, 1_000_000, 10_000, stop_pct=2.0)
            amount = s.ledger_shadow(Path(d) / 'orders.sqlite3', '2026-11-12', events, 1_000_000, 10_000,
                                     shares_for=lambda price, qty=1, fill=None, nth=1: 100_000 // price, stop_pct=2.0)
        self.assertEqual([(t['side'], t['qty'], t['price'], t['reason']) for t in one['trades']],
                         [('BUY', 1, 1000, '모의 원장 매수'), ('SELL', 1, 975, '실계좌 손절 −2%')])  # no rebuy at 10:40 (latched)
        self.assertEqual(one['open'], {})
        self.assertEqual([(t['side'], t['qty']) for t in amount['trades']], [('BUY', 100), ('SELL', 100)])
        self.assertEqual(amount['realized'], -2500)


class LossLatchTests(unittest.TestCase):
    def test_the_daily_loss_latch_sells_everything_and_stops_buying(self):
        """Review 2 M5: the card never latched the daily loss; the mirror's guard sells all and buys nothing more that day."""
        import sqlite3, tempfile
        from pathlib import Path
        fills = [('a', '005930', 'BUY', 10, 100_000, '2026-11-06T10:00:00+09:00'),
                 ('b', '000660', 'BUY', 10, 100_000, '2026-11-06T10:00:00+09:00'),
                 ('c', '069500', 'BUY', 10, 1_000, '2026-11-06T11:00:00+09:00')]
        down = {'time': '2026-11-06T10:30:00+09:00', 'market_bars': {'005930': [{'stck_prpr': 98_500}], '000660': [{'stck_prpr': 98_500}]}}
        with tempfile.TemporaryDirectory() as d:
            with sqlite3.connect(Path(d) / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills)
            result = s.ledger_shadow(Path(d) / 'orders.sqlite3', '2026-11-06', [down], 10_000_000, 300_000,
                                     shares_for=lambda price, qty=1, fill=None, nth=1: qty, stop_pct=2.0)
        self.assertEqual(result['loss_halt'], '2026-11-06T10:30:00+09:00')  # 30,000 down across two symbols, each above its stop
        self.assertEqual([(t['side'], t['symbol'], t['qty']) for t in result['trades'] if t['side'] == 'SELL'],
                         [('SELL', '000660', 10), ('SELL', '005930', 10)])
        self.assertEqual((result['skipped_entries'], result['open'], result['realized']), ({'일일 손실 중단': 1}, {}, -30_000))
