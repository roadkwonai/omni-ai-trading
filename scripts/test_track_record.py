import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import track_record as t


class TrackRecordTests(unittest.TestCase):
    def test_trades_and_direction_summary_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with sqlite3.connect(root / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [
                    ('a', '114800', 'BUY', 10, 1000.0, '2026-11-12T10:00:00+09:00'),
                    ('b', '114800', 'SELL', 5, 1010.0, '2026-11-12T10:10:00+09:00'),
                    ('c', '114800', 'SELL', 5, 980.0, '2026-11-12T10:20:00+09:00')])
            with sqlite3.connect(root / 'paper-model.sqlite3') as db:
                db.execute('CREATE TABLE jev_predictions_latest (run_digest TEXT, symbol TEXT, alpha INTEGER, beta INTEGER)')
                db.executemany('INSERT INTO jev_predictions_latest VALUES (?,?,?,?)',
                               [('r', 'K200', 2, 1), ('r', 'K200', 4, 2), ('r', 'INV', 1, 1)])
            now = datetime(2026, 11, 12, 11)
            result = t.summary(root, now)
            self.assertEqual((result['paper_trades']['closed_sells'], result['paper_trades']['wins'],
                              result['paper_trades']['avg_return_pct']), (2, 1, -0.5))
            self.assertEqual(result['pick_direction_research'], {'K200': {'picks_rose_after': 3, 'picks_fell_after': 1}})
            (root / 'orders.sqlite3').unlink()
            self.assertIs(t.summary(root, now + timedelta(seconds=10)), result)  # cached
            self.assertNotIn('paper_trades', t.summary(root, now + timedelta(seconds=400)))

    def test_jev_view_follows_the_jev_ledger_and_carries_its_strategy(self):
        import json, sqlite3
        from zoneinfo import ZoneInfo
        now = datetime(2026, 11, 12, 11, tzinfo=ZoneInfo('Asia/Seoul'))
        with tempfile.TemporaryDirectory() as temp:
            nhplug = Path(temp) / 'nhplug'
            for folder in ('shared-etf-paper', 'jev-virtual'):
                (nhplug / folder).mkdir(parents=True)
            (nhplug / 'shared-etf-paper' / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in [
                {'time': (now - timedelta(minutes=50)).isoformat(), 'event': 'jev_holding_decision', 'symbol': '114800', 'choice': 'SELL', 'confidence': 0.9},
                {'time': (now - timedelta(minutes=3)).isoformat(), 'event': 'jev_holding_decision', 'symbol': '005930', 'choice': 'SELL', 'confidence': 0.72},
                {'time': (now - timedelta(minutes=2)).isoformat(), 'event': 'jev_shadow', 'jev': {'candidate': {
                    'choice': 'K200', 'confidence': 0.7, 'probabilities': {'WAIT': 0.2, 'K200': 0.7, '036540': 0.1}}}}]))
            with sqlite3.connect(nhplug / 'shared-etf-paper' / 'orders.sqlite3') as db:
                db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
                db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [('1', '069500', 'BUY', 1, 100.0, now.isoformat()),
                                                                         ('2', '069500', 'SELL', 1, 99.0, now.isoformat())])
            view = t.jev_view(temp, now)  # no accounts.json: Jev is the default main engine -> shared ledger
            self.assertEqual((view['its_role'], view['latest_view']['choice'], view['its_paper_trades']['losses']), ('main', 'K200', 1))
            self.assertIn('stop_loss_pct', view['strategy']['settings'])
            self.assertEqual(view['latest_view']['probabilities_by_code'], {'069500': 0.7, '036540': 0.1})
            self.assertEqual(view['latest_view']['wait_probability'], 0.2)
            self.assertEqual(view['holding_views'], {'005930': {'time': '10:57:00', 'choice': 'SELL', 'confidence': 0.72}})  # 50 min old: dropped
            (nhplug / 'accounts.json').write_text(json.dumps({'version': 1, 'accounts': {'paper-shared': {
                'kind': 'paper', 'engine': 'vibe', 'auto': True, 'budget': {'first_buy_krw': 1_000_000, 'per_symbol_krw': 3_000_000}}}}))
            view = t.jev_view(temp, now)  # vibe can no longer be main (user 2026-11-17): Jev stays on the shared ledger
            self.assertEqual(view['its_role'], 'main')

    def test_direction_reads_the_last_run_of_a_model_file_written_before_the_latest_table(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Path(temp) / 'paper-model.sqlite3'
            with sqlite3.connect(model) as db:
                db.execute('CREATE TABLE jev_predictions (run_digest TEXT, symbol TEXT, alpha INTEGER, beta INTEGER)')
                db.executemany('INSERT INTO jev_predictions VALUES (?,?,?,?)',
                               [('old', 'K200', 9, 9), ('new', 'K200', 2, 1), ('new', 'K200', 4, 2)])
            self.assertEqual(t.direction_results(model), {'K200': {'picks_rose_after': 3, 'picks_fell_after': 1}})
            model.unlink()
            sqlite3.connect(model).close()
            self.assertEqual(t.direction_results(model), {})

    def test_missing_sources_give_empty_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(t.summary(Path(temp), datetime(2026, 11, 12)), {})



class SettledCallTests(unittest.TestCase):
    def test_only_verified_vibe_settlements_enter_summary_without_mutating_history(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'market-research.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE decision_outcomes (key TEXT, engine TEXT, kind TEXT, direction, r30, b30, status)')
                db.executemany('INSERT INTO decision_outcomes VALUES (?,?,?,?,?,?,?)', [
                    ('jev:t', 'jev', 'entry', 1, 1.0, 0.0, 'settled'),
                    ('vibe:old:005930', 'vibe', 'score', 1, 20.0, 0.0, 'settled'),
                    ('vibe_unverified:old:005930', 'vibe', 'score', 1, 30.0, 0.0, 'settled'),
                    ('vibe_available:new:005930', 'vibe', 'score', 1, -1.0, 0.0, 'settled'),
                ])
            calls = t.settled_decisions(path)
            self.assertEqual((calls['jev entry']['n'], calls['vibe score']['n']), (1, 1))
            self.assertEqual(calls['vibe score']['avg_return_30m_pct'], -1.0)
            summary = t.summary(Path(d), datetime(2026, 11, 12), ttl=0)
            self.assertEqual(summary['vibe_legacy_settled_excluded'], 2)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM decision_outcomes').fetchone()[0], 4)

    def test_settled_calls_are_summarised_per_engine_in_the_called_direction(self):
        import sqlite3, tempfile
        from pathlib import Path
        import track_record as t
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'market-research.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE decision_outcomes (engine, kind, direction, r30, b30, status)')
                db.executemany('INSERT INTO decision_outcomes VALUES (?,?,?,?,?,?)', [
                    ('jev', 'entry', 1, 1.0, 0.5, 'settled'), ('jev', 'entry', 1, -1.0, 0.0, 'settled'),
                    ('jev', 'holding', -1, -2.0, -1.0, 'settled'), ('jev', 'entry', 1, None, None, 'pending'),
                    ('vibe', 'score', 1, 10.0, 0.0, 'settled')])
            result = t.settled_decisions(path)
            self.assertEqual(result['jev entry'], {'n': 2, 'hit_rate_30m': 0.5, 'avg_return_30m_pct': 0.0, 'avg_vs_kodex200_30m_pct': -0.25})
            self.assertEqual(result['jev holding']['hit_rate_30m'], 1.0)  # a SELL call that fell counts as a hit
            self.assertNotIn('vibe score', result)  # no key means no publication-time proof
            self.assertEqual(t.settled_decisions(Path(d) / 'missing.sqlite3'), {})


if __name__ == '__main__':
    unittest.main()
