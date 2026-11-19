import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import position_paths as pp

KST = ZoneInfo('Asia/Seoul')
T = lambda h, m: datetime(2026, 11, 12, h, m, tzinfo=KST)


def bars(code, start, closes, highs=None, lows=None):
    return [(start + timedelta(minutes=i), c, (highs or closes)[i], (lows or closes)[i], c) for i, c in enumerate(closes)]


class PositionPathTests(unittest.TestCase):
    def test_vibe_score_uses_published_time_and_legacy_is_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / 'ledger.sqlite3'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (time TEXT, event TEXT, data TEXT)')
            start = T(10, 0).isoformat()
            advice = [{'time': start, 'available_at': T(10, 5).isoformat(), 'scores': {'229200': {'score': 0.5}}},
                      {'time': T(10, 10).isoformat(), 'scores': {'229200': {'score': 0.5}}}]
            views = pp.decisions(ledger, '2026-11-12', advice)
            self.assertEqual(views[0][1], T(10, 5).isoformat())
            self.assertTrue(views[0][0].startswith('vibe_available:'))
            self.assertTrue(views[1][0].startswith('vibe_unverified:'))
            series = {'229200': [(T(10, i), 100 + i, 100 + i, 100 + i, 100 + i, T(10, i + 1)) for i in range(40)],
                      '069500': [(T(10, i), 200, 200, 200, 200, T(10, i + 1)) for i in range(40)]}
            rows = pp.outcome_rows(views, series)
            self.assertEqual((rows[0]['time'], rows[0]['price'], rows[0]['status']), (T(10, 5).isoformat(), 104, 'settled'))
            self.assertEqual((rows[1]['status'], rows[1]['price'], rows[1]['r30']), ('time_unverified', None, None))

    def test_vibe_bars_must_have_been_observed_by_each_measurement(self):
        now = T(10, 5)
        early = (T(10, 2), 101, 101, 101, 101, T(10, 3))
        late = (T(10, 4), 120, 120, 120, 120, T(10, 8))
        self.assertEqual(pp.close_at([early, late], now, observed_only=True), 101)
        self.assertIsNone(pp.close_at([early, late], T(10, 6), observed_only=True, max_age=timedelta(minutes=2)))
        self.assertEqual(pp.close_at([early, late], now), 120)  # legacy Jev path unchanged
        self.assertIsNone(pp.close_after([late], now, observed_only=True))

    def test_vibe_invalid_or_timezone_free_available_time_is_not_settled(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / 'ledger.sqlite3'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (time TEXT, event TEXT, data TEXT)')
            items = [{'time': T(10, 0).isoformat(), 'available_at': value, 'scores': {'005930': {'score': 0.3}}}
                     for value in ('2026-11-12T10:05:00', T(9, 59).isoformat(), 'bad')]
            views = pp.decisions(ledger, '2026-11-12', items)
            self.assertEqual(len(views), 3)
            self.assertTrue(all(view[0].startswith('vibe_unverified:') for view in views))

    def test_recorded_bar_receipt_time_and_late_revision_are_not_lookahead(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / 'ledger.sqlite3'
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (time TEXT, data TEXT)')
                def event(at, price):
                    row = {'bsop_time': '100300', 'stck_oprc': price, 'stck_hgpr': price,
                           'stck_lwpr': price, 'stck_prpr': price}
                    db.execute('INSERT INTO events_v2 VALUES (?,?)', (at.isoformat(), json.dumps({'market_bars': {'KQ150': [row]}})))
                event(T(10, 4), 101)
                event(T(10, 6), 101)  # identical repeats preserve the first receipt
                event(T(10, 9), 105)  # a revised bar is not known at 10:05
            bars = pp.day_bars(ledger, '2026-11-12')['229200']
            self.assertIsNone(pp.close_at(bars, T(10, 5), observed_only=True))
            self.assertEqual(pp.close_at(bars, T(10, 5)), 105)  # Jev path retains existing behavior
            self.assertEqual(pp.close_at(bars, T(10, 9), observed_only=True), 105)

    def test_update_preserves_legacy_vibe_rows_without_overwriting_them(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with sqlite3.connect(root / 'trading-ledger.sqlite3') as db:
                db.execute('CREATE TABLE events_v2 (time TEXT, event TEXT, data TEXT)')
            with sqlite3.connect(root / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            legacy_key = f'vibe:{T(10, 0).isoformat()}:005930'
            template = pp.outcome_rows([(legacy_key, T(10, 0).isoformat(), 'vibe', 'score', '005930', None, 0.5, 1, None)], {})[0]
            template.update(status='settled', r30=99.0)
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                cols = list(template)
                db.execute(f"CREATE TABLE decision_outcomes ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'key' else '') for c in cols)})")
                db.execute(f"INSERT INTO decision_outcomes VALUES ({','.join('?' for _ in cols)})", tuple(template[c] for c in cols))
                db.execute('CREATE TABLE snapshots (id INTEGER PRIMARY KEY, kind TEXT, time TEXT, payload TEXT)')
                advice = {'time': T(10, 0).isoformat(), 'scores': {'005930': {'score': 0.5}}}
                db.execute('INSERT INTO snapshots (kind,time,payload) VALUES (?,?,?)', ('vibe_advice', advice['time'], json.dumps(advice)))
            pp.update(root, '2026-11-12', today='2026-11-12')
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                self.assertEqual(db.execute('SELECT status,r30 FROM decision_outcomes WHERE key=?', (legacy_key,)).fetchone(), ('settled', 99.0))
                self.assertEqual(db.execute("SELECT status,r30 FROM decision_outcomes WHERE key LIKE 'vibe_unverified:%'").fetchone(), ('time_unverified', None))

    def test_episode_mfe_mae_and_what_happened_after_the_exit(self):
        fills = [('114800', 'BUY', 2, 1000.0, T(10, 0).isoformat()), ('114800', 'BUY', 2, 990.0, T(10, 5).isoformat()),
                 ('114800', 'SELL', 4, 1010.0, T(10, 10).isoformat()), ('069500', 'BUY', 1, 100.0, T(10, 0).isoformat())]
        series = {'114800': bars('114800', T(9, 59), [1000, 1000, 1020, 980, 995] + [1000] * 6 + [1015] * 40,
                                  highs=[1000, 1000, 1020, 990, 995] + [1000] * 6 + [1030] * 40,
                                  lows=[1000, 1000, 1000, 970, 995] + [1000] * 6 + [1015] * 40)}
        rows = {r['symbol']: r for r in pp.path_rows(pp.positions(fills), series)}
        inv = rows['114800']
        self.assertEqual((inv['status'], inv['avg_entry'], inv['realized_krw'], inv['return_pct']), ('closed', 995.0, 60, 1.508))
        self.assertEqual((inv['mfe_pct'], inv['mae_pct']), (2.513, -2.513))
        self.assertEqual(inv['after_30m_pct'], 0.495)   # 1015 vs the 1010 exit
        self.assertEqual(inv['after_high_pct'], 1.98)   # 1030 vs 1010
        self.assertEqual(rows['069500']['status'], 'open')
        self.assertIsNone(rows['069500']['mfe_pct'])     # no bars recorded for it

    def test_decisions_settle_against_the_benchmark_and_wait_for_data(self):
        series = {'229200': bars('229200', T(10, 0), [100 + i for i in range(40)]),
                  '069500': bars('069500', T(10, 0), [200] * 40)}
        views = [('a', T(10, 1).isoformat(), 'jev', 'entry', '229200', 'KQ150', 0.7, 1, 'd' * 64),
                 ('b', T(10, 30).isoformat(), 'jev', 'entry', '229200', 'KQ150', 0.7, 1, None),
                 ('c', T(10, 1).isoformat(), 'vibe', 'score', '000001', None, 0.6, 1, None)]
        rows = {r['key']: r for r in pp.outcome_rows(views, series)}
        self.assertEqual((rows['a']['price'], rows['a']['r5'], rows['a']['b5'], rows['a']['status']), (100, 5.0, 0.0, 'settled'))
        self.assertEqual(rows['b']['status'], 'pending')   # no bar 30 minutes after 10:30 yet
        self.assertEqual(rows['c']['status'], 'no_price')

    def test_update_reads_v2_events_and_rewrites_rows_without_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with sqlite3.connect(root / 'trading-ledger.sqlite3') as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, digest TEXT, time TEXT, event TEXT, data TEXT, schema_version INTEGER)')
                bars_event = {'market_bars': {'KQ150': [{'bsop_time': f'10{m:02d}00', 'stck_oprc': 100, 'stck_hgpr': 100,
                                                         'stck_lwpr': 100, 'stck_prpr': 100 + m} for m in range(40)]}}
                db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?,2)', ('s', 1, 'x', T(10, 40).isoformat(), 'market_context', json.dumps(bars_event)))
                db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?,2)', ('s', 2, 'y', T(10, 2).isoformat(), 'jev_shadow',
                                                                          json.dumps({'jev_choice': 'KQ150', 'jev_confidence': 0.7})))
            with sqlite3.connect(root / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            for _ in range(2):
                result = pp.update(root, '2026-11-12')
            self.assertEqual(result, {'day': '2026-11-12', 'positions': 0, 'decisions': 1, 'settled': 1})
            with sqlite3.connect(root / 'trading-ledger.sqlite3') as db:  # a decision near the end of a past day
                db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?,2)', ('s', 3, 'z', T(10, 35).isoformat(), 'jev_shadow',
                                                                          json.dumps({'jev_choice': 'KQ150', 'jev_confidence': 0.7})))
            pp.update(root, '2026-11-12', today='2026-11-12')
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                self.assertEqual(db.execute("SELECT status FROM decision_outcomes WHERE time LIKE '%10:35%'").fetchone()[0], 'pending')
            pp.update(root, '2026-11-12', today='2026-11-13')
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                self.assertEqual(db.execute("SELECT status FROM decision_outcomes WHERE time LIKE '%10:35%'").fetchone()[0], 'insufficient')
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                self.assertEqual(db.execute('SELECT count(*), max(r30) FROM decision_outcomes').fetchone(), (2, 29.703))  # the 10:02 settled decision plus the unsettled 10:35 one


class MultiDayTests(unittest.TestCase):
    """Review 4 C4/C9: the watch updates only its own day."""

    def root(self, d):
        root = Path(d)
        with sqlite3.connect(root / 'trading-ledger.sqlite3') as db:
            db.execute('CREATE TABLE events_v2 (time TEXT, event TEXT, data TEXT)')
            for day, high in (('2026-11-12', 120.0), ('2026-11-13', 105.0)):
                for minute in range(60):  # 10:00-10:59, each bar logged a minute later
                    bar = {'bsop_time': f'10{minute:02d}00', 'stck_oprc': 100, 'stck_hgpr': high if minute == 30 else 101,
                           'stck_lwpr': 99, 'stck_prpr': 100}
                    at = datetime.fromisoformat(f'{day}T10:{minute:02d}:00+09:00') + timedelta(minutes=1)
                    db.execute('INSERT INTO events_v2 VALUES (?,?,?)', (at.isoformat(), 'market_context',
                                                                        json.dumps({'market_bars': {'005930': [bar]}})))
            # a Jev pick at 10:50 on day 1: its 30 minutes run past the last bar that day records
            db.execute('INSERT INTO events_v2 VALUES (?,?,?)', ('2026-11-12T10:50:00+09:00', 'jev_shadow',
                                                                json.dumps({'jev_choice': '005930', 'jev_confidence': 0.7})))
        with sqlite3.connect(root / 'orders.sqlite3') as db:
            db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            db.execute("INSERT INTO fills VALUES ('b', '005930', 'BUY', 10, 100, '2026-11-12T10:00:30+09:00')")
            db.execute("INSERT INTO fills VALUES ('s', '005930', 'SELL', 10, 100, '2026-11-13T10:45:30+09:00')")
        return root

    def test_an_overnight_position_keeps_the_best_and_worst_of_every_held_day(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.root(d)
            pp.update(root, day='2026-11-12', today='2026-11-12')
            pp.update(root, day='2026-11-13', today='2026-11-13')  # the exit day
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                row = db.execute('SELECT mfe_pct, mae_pct, bars_held, status FROM position_paths').fetchone()
        self.assertEqual(row, (20.0, -1.0, 60 + 46, 'closed'))  # the 120 high was on day 1

    def test_a_finished_day_has_no_pending_outcomes(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.root(d)
            pp.update(root, day='2026-11-12', today='2026-11-12')
            pp.update(root, day='2026-11-13', today='2026-11-13')
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                status = db.execute("SELECT status FROM decision_outcomes WHERE time LIKE '2026-11-12%'").fetchone()[0]
        self.assertEqual(status, 'insufficient')


class GapTests(unittest.TestCase):
    def test_a_settlement_needs_a_bar_near_its_target(self):
        series = bars('x', T(10, 0), [100, 101]) + [(T(10, 20), 120, 120, 120, 120)]
        self.assertEqual(pp.close_after(series, T(10, 2)), 101)
        self.assertIsNone(pp.close_after(series, T(10, 5)))   # next bar is 15 minutes late


if __name__ == '__main__':
    unittest.main()
