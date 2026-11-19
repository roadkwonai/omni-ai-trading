import sqlite3
import tempfile
import unittest
from pathlib import Path

import daily_review as d


class DailyReviewTests(unittest.TestCase):
    def test_lessons_name_the_numbers_they_rest_on(self):
        facts = {'closed': 2, 'wins': 1, 'realized_krw': -500, 'avg_return_pct': -0.2, 'avg_mfe_pct': 0.4, 'avg_mae_pct': -1.8,
                 'avg_after_exit_30m_pct': 0.6, 'entry_waits': {'volume_not_rising': 40, 'direction_not_confirmed': 10},
                 'settled_calls': {'jev entry': {'n': 30, 'hit_rate_30m': 0.4, 'avg_return_30m_pct': 0.1, 'avg_vs_kodex200_30m_pct': -0.2}},
                 'exit_reasons': {}}
        out = d.lessons(facts, '코스피 하락장·혼조')
        self.assertEqual(len(out), 4)
        self.assertIn('-0.20%p', out[0])
        self.assertIn('+0.60%', out[1])
        self.assertIn('volume_not_rising', out[3])
        self.assertEqual(d.lessons({'closed': 0, 'settled_calls': {}, 'entry_waits': {}}), ['체결과 정산 데이터가 부족해 오늘은 교훈을 만들지 않았다.'])

    def test_build_reads_the_days_records_and_stores_a_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                db.execute('CREATE TABLE position_paths (key TEXT PRIMARY KEY, closed TEXT, realized_krw, return_pct, mfe_pct, mae_pct, after_30m_pct)')
                db.execute("INSERT INTO position_paths VALUES ('a', '2026-11-12T11:00:00+09:00', 1200, 0.9, 1.1, -0.3, 0.1)")
                db.execute('CREATE TABLE decision_outcomes (key TEXT PRIMARY KEY, time TEXT, engine, kind, direction, r30, b30, status)')
                db.executemany('INSERT INTO decision_outcomes VALUES (?,?,?,?,?,?,?,?)', [
                    (f'k{i}', '2026-11-12T10:00:00+09:00', 'jev', 'entry', 1, 0.5, 0.1, 'settled') for i in range(12)])
            review = d.store(d.build(root, '2026-11-12'), root)
            self.assertEqual((review['facts']['closed'], review['facts']['wins'], review['facts']['realized_krw']), (1, 1, 1200))
            self.assertIn('+0.40%p', review['lessons'][0])
            import research_store
            self.assertEqual(research_store.history('daily_review', 1, root / 'market-research.sqlite3')[0]['day'], '2026-11-12')

    def test_unverified_vibe_results_are_not_counted_as_settled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                db.execute('CREATE TABLE decision_outcomes (key TEXT, time TEXT, engine, kind, direction, r30, b30, status)')
                db.executemany('INSERT INTO decision_outcomes VALUES (?,?,?,?,?,?,?,?)', [
                    ('jev:t', '2026-11-12T10:00:00+09:00', 'jev', 'entry', 1, 1.0, 0.0, 'settled'),
                    ('vibe:old:005930', '2026-11-12T10:00:00+09:00', 'vibe', 'score', 1, 40.0, 0.0, 'settled'),
                    ('vibe_available:new:005930', '2026-11-12T10:05:00+09:00', 'vibe', 'score', 1, -1.0, 0.0, 'settled'),
                ])
            facts = d.facts(root, '2026-11-12')
            self.assertEqual(facts['settled_calls']['vibe score']['n'], 1)
            self.assertEqual(facts['settled_calls']['vibe score']['avg_return_30m_pct'], -1.0)
            self.assertEqual(facts['settled_calls']['jev entry']['n'], 1)
            self.assertEqual(facts['vibe_legacy_settled_excluded'], 1)


if __name__ == '__main__':
    unittest.main()
