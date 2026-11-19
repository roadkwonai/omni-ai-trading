import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import nh_live_research_gate as gate

KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=KST)
STAMP = '2026-11-11T11:00:00+09:00'


class ResearchGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def db(self, name):
        return sqlite3.connect(self.root / name)

    def make_observations(self):
        with self.db('paper-observations.sqlite3') as db:
            db.execute('CREATE TABLE price_observations (status TEXT, decision_time TEXT)')
            db.executemany('INSERT INTO price_observations VALUES (?,?)',
                           [('observed', STAMP), ('insufficient', STAMP), ('not_recorded', STAMP)])

    def make_research(self):
        with self.db('paper-model.sqlite3') as db:
            db.execute('CREATE TABLE jev_predictions_latest (source TEXT, offset INTEGER, symbol TEXT, jev_time TEXT)')
            db.executemany('INSERT INTO jev_predictions_latest VALUES (?,?,?,?)',
                           [('source', 1, 'INV', STAMP), ('source', 1, 'INV', STAMP),
                            ('source', 2, 'K200', STAMP)])

    def make_orders(self, *, sale=True):
        with self.db('orders.sqlite3') as db:
            db.execute('CREATE TABLE meta (name TEXT, value TEXT)')
            db.execute('INSERT INTO meta VALUES (?,?)', ('account_sha256', 'a' * 64))
            db.execute('CREATE TABLE intents (id TEXT, symbol TEXT, side TEXT, qty INTEGER, status TEXT, order_number INTEGER)')
            db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?)', ('buy', '114800', 'BUY', 1, 'terminal', 1))
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('buy', '114800', 'BUY', 1, 1277, STAMP))
            if sale:
                db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?)', ('sell', '114800', 'SELL', 1, 'terminal', 2))
                db.execute('INSERT INTO fills VALUES (?,?,?,?,?,?)', ('sell', '114800', 'SELL', 1, 1300, STAMP))

    def test_research_reads_the_old_per_run_table_of_a_model_file_written_before_the_latest_table(self):
        with self.db('paper-model.sqlite3') as db:
            db.execute('CREATE TABLE jev_predictions (source TEXT, offset INTEGER, symbol TEXT, jev_time TEXT)')
            db.executemany('INSERT INTO jev_predictions VALUES (?,?,?,?)',
                           [('source', 1, 'INV', STAMP), ('source', 1, 'INV', STAMP), ('source', 2, 'K200', STAMP)])
        research = gate.evaluate(self.root, NOW)['jev_research']
        self.assertTrue(research['available'])
        self.assertEqual(research['unique_predictions'], 2)

    def test_missing_inputs_are_not_zero_or_ready_and_no_files_created(self):
        report = gate.evaluate(self.root, NOW)
        self.assertEqual(report['status'], 'NOT_READY')
        for name in ('price_observations', 'jev_research', 'paper_fills'):
            self.assertFalse(report[name]['available'])
        self.assertIsNone(report['costs']['net_return_pct'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_research_and_confirmed_fills_are_separate_and_duplicate_runs_deduplicated(self):
        self.make_observations()
        self.make_research()
        self.make_orders()
        report = gate.evaluate(self.root, NOW)
        self.assertEqual(report['price_observations']['observed'], 1)
        self.assertEqual(report['price_observations']['record_dates'], 1)
        self.assertIsNone(report['price_observations']['independent_samples'])
        self.assertEqual(report['jev_research']['unique_predictions'], 2)
        self.assertEqual(report['paper_fills']['local_reconciled_fills'], 2)
        self.assertEqual(report['paper_fills']['closed_units'], 1)
        self.assertEqual(report['paper_fills']['open_units'], 0)
        self.assertFalse(report['paper_fills']['broker_independently_checked'])
        self.assertIsNone(report['costs']['net_return_pct'])
        self.assertEqual(report['status'], 'NOT_READY')

    def test_open_position_and_unresolved_order_are_not_completed_roundtrips(self):
        self.make_orders(sale=False)
        with self.db('orders.sqlite3') as db:
            db.execute('INSERT INTO intents VALUES (?,?,?,?,?,?)', ('unknown', '114800', 'SELL', 1, 'uncertain', None))
        fills = gate.evaluate(self.root, NOW)['paper_fills']
        self.assertEqual((fills['closed_units'], fills['open_units'], fills['unresolved_orders']), (0, 1, 1))

    def test_unverified_or_unmatched_sell_does_not_become_a_roundtrip(self):
        self.make_orders()
        with self.db('orders.sqlite3') as db:
            db.execute("UPDATE intents SET status='uncertain' WHERE id='sell'")
        report = gate.evaluate(self.root, NOW)
        self.assertFalse(report['paper_fills']['available'])
        self.assertEqual(report['status'], 'NOT_READY')

    def test_bad_time_invalidates_only_affected_evidence(self):
        self.make_observations()
        self.make_research()
        with self.db('paper-observations.sqlite3') as db:
            db.execute("UPDATE price_observations SET decision_time='2026-11-12T12:00:00+09:00'")
        report = gate.evaluate(self.root, NOW)
        self.assertFalse(report['price_observations']['available'])
        self.assertTrue(report['jev_research']['available'])
        self.assertEqual(report['status'], 'NOT_READY')


if __name__ == '__main__':
    unittest.main()
