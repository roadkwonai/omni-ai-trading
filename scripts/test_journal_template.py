import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import journal_template as T
import trading_journal as tj

KST = ZoneInfo('Asia/Seoul')


def day_record(day, pnl, perspective=None):
    fills = [{'time': f'{day}T09:00:00+09:00', 'symbol': '005930', 'side': 'BUY', 'qty': 1, 'price': 1000, 'amount': 1000, 'pnl': None},
             {'time': f'{day}T10:00:00+09:00', 'symbol': '005930', 'side': 'SELL', 'qty': 1, 'price': 1000 + pnl, 'amount': 1000 + pnl,
              'pnl': pnl, 'return_pct': pnl / 10}]
    return {'day': day, 'books': {'paper': {'label': '모의 (Jev)', 'fills': fills, 'performance': tj.performance(fills), 'round_trips': []}},
            'market': {'코스피': {'close': 100 + pnl / 100, 'change_pct': -3.0 if pnl < 0 else 0.5, 'on': day}},
            'system': {'by_entry_route': {'Jev 후보': {'n': 1, 'wins': int(pnl > 0), 'realized_krw': pnl}}, 'errors': {'x': 1}},
            'perspective': perspective}


class PeriodKeyTests(unittest.TestCase):
    def test_keys_bounds_and_labels(self):
        self.assertEqual([T.key_of(k, '2026-11-13') for k in T.KINDS], ['2026-11', '2026-Q4', '2026-H2', '2026'])
        self.assertEqual(T.bounds('month', '2026-02'), ('2026-02-01', '2026-02-28'))
        self.assertEqual(T.bounds('quarter', '2026-Q4'), ('2026-10-01', '2026-12-31'))
        self.assertEqual(T.bounds('half', '2026-H1'), ('2026-01-01', '2026-06-30'))
        self.assertEqual(T.bounds('year', '2026'), ('2026-01-01', '2026-12-31'))
        self.assertEqual([T.label(k, T.key_of(k, '2026-05-02')) for k in T.KINDS], ['2026년 5월', '2026년 2분기', '2026년 상반기', '2026년'])
        self.assertEqual([T.previous(k, T.key_of(k, '2027-01-01')) for k in T.KINDS], ['2026-12', '2026-Q4', '2026-H2', '2026'])
        self.assertEqual([T.kind_of(k) for k in ('2026-11-13', '2026-11', '2026-Q4', '2026-H2', '2026')],
                         ['day', 'month', 'quarter', 'half', 'year'])
        with self.assertRaises(ValueError):
            T.kind_of('2026-Q5')

    def test_due_periods_on_the_first_of_a_month(self):
        self.assertEqual(T.due('2027-01-01'), [('month', '2026-12'), ('quarter', '2026-Q4'), ('half', '2026-H2'), ('year', '2026')])
        self.assertEqual(T.due('2026-07-01'), [('month', '2026-06'), ('quarter', '2026-Q2'), ('half', '2026-H1')])
        self.assertEqual(T.due('2026-11-01'), [('month', '2026-10')])
        self.assertEqual(T.due('2026-11-13'), [])


class TemplateTests(unittest.TestCase):
    def test_the_automatic_day_report_follows_the_template_even_on_an_empty_day(self):
        for record in ({'day': '2026-11-16', 'books': {}}, day_record('2026-11-16', 50)):
            md = tj.report_md(record)
            self.assertEqual(T.check(md, 'day'), [], md)
            self.assertEqual([line[3:] for line in md.splitlines() if line.startswith('## ')], T.headings('day'))

    def test_the_checker_names_what_is_missing_or_out_of_order(self):
        md = tj.report_md({'day': '2026-11-16', 'books': {}})
        broken = md.replace('## 6. 애널리스트', '## 애널리스트')
        self.assertIn("'## 6. 애널리스트' 절이 없습니다", T.check(broken, 'day'))
        a, b = '## 1. 숫자로 보는 하루', '## 2. 매매 관점 — 무엇을 왜 사고 팔았나'
        swapped = md.replace(a, '@@').replace(b, a).replace('@@', b)
        self.assertTrue(any('순서' in p for p in T.check(swapped, 'day')))
        self.assertIn("제목 아래 '> 한 줄 요약'이 없습니다", T.check(md.replace('> ', ''), 'day'))
        written = md.replace(T.EMPTY_WRITTEN, '- 메모')  # a written 출처 without links
        self.assertIn("'## 출처'에 링크가 없습니다", T.check(written, 'day'))
        # a written journal must not keep the skeleton's placeholders (the automatic report may)
        self.assertEqual(T.check(md, 'day'), [])
        self.assertTrue(any("'작성 전' 자리표시가 7곳" in p for p in T.check(md, 'day', written=True)), T.check(md, 'day', written=True))


class SkillSyncTests(unittest.TestCase):
    def test_the_skill_references_list_every_template_section(self):
        skill = Path(__file__).resolve().parents[1] / '.claude/skills/trading-journal-writer/references'
        for name, kind in (('day.md', 'day'), ('period.md', 'period')):
            text = (skill / name).read_text(encoding='utf-8')
            found = [line[3:] for line in text.splitlines() if line.startswith('## ')]
            listed = [h for h in found if h in T.headings(kind)]
            self.assertEqual(listed, T.headings(kind), name)  # every section, in the template's order


class SkillCommandTests(unittest.TestCase):
    """The commands trading-journal-writer tells an agent to run, end to end in a throwaway home folder."""

    def run_cli(self, home, *args):
        import os
        import subprocess
        import sys
        repo = Path(__file__).resolve().parents[1]
        env = {**os.environ, 'HOME': str(home), 'PYTHONPATH': os.pathsep.join(str(repo / p) for p in ('agent', 'scripts', 'streamlit_namuh'))}
        return subprocess.run([sys.executable, *args], cwd=repo, env=env, capture_output=True, text=True, timeout=60)

    def test_the_documented_day_and_period_commands(self):
        with tempfile.TemporaryDirectory() as home:
            root = Path(home) / '.vibe-trading/nhplug/shared-etf-paper'
            (root / 'reviews').mkdir(parents=True)
            for day, pnl in (('2026-11-12', -300), ('2026-11-13', 500)):
                tj.store(day_record(day, pnl), root / 'trading-journal.sqlite3')
            auto = self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-13', '--report')
            self.assertEqual(auto.returncode, 0, auto.stderr)
            self.assertEqual(T.check(auto.stdout, 'day'), [])
            built = self.run_cli(home, 'scripts/trading_journal.py', '--period', 'month', '--key', '2026-11')
            self.assertEqual(json.loads(built.stdout), {'period': '2026-11', 'days': 2, 'checked': []})
            report = self.run_cli(home, 'scripts/trading_journal.py', '--period', 'month', '--key', '2026-11', '--report')
            self.assertEqual(T.check(report.stdout, 'period'), [])
            # the written journal: the skeleton with the analysis filled in, then checked and merged
            written = auto.stdout.replace(T.EMPTY_WRITTEN, '- [근거 기사](https://news.example/1)').replace(T.SUMMARY_PENDING, '반등한 날이다.')
            (root / 'reviews/journal-2026-11-13.md').write_text(written)
            (root / 'reviews/journal-2026-11-13.json').write_text(json.dumps({'day': '2026-11-13', 'summary': '반등', 'engine_notes': ['급락 뒤 반등']}))
            status = json.loads(self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-13', '--status').stdout)
            self.assertEqual((status['trading_day'], status['collected'], status['fills'], status['written'], status['merged']),
                             (True, True, 2, {'json': True, 'md': True}, False))  # written, not merged yet
            weekend = json.loads(self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-14', '--status').stdout)
            self.assertEqual((weekend['trading_day'], weekend['collected']), (False, False))
            week = json.loads(self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-13', '--week').stdout)
            self.assertEqual((week['paper']['realized_krw'], week['paper']['trading_days']), (200, 2))
            due = self.run_cli(home, 'scripts/journal_template.py', '--due', '2027-01-01')
            self.assertEqual(due.stdout.split('\n')[:2], ['month 2026-12', 'quarter 2026-Q4'])
            ok = self.run_cli(home, 'scripts/journal_template.py', '--check', str(root / 'reviews/journal-2026-11-13.md'))
            self.assertEqual((ok.returncode, ok.stdout.strip()), (0, '템플릿과 일치합니다.'))
            (root / 'reviews/journal-2026-11-12.md').write_text(written.replace('## 7. 시스템', '## 시스템'))
            bad = self.run_cli(home, 'scripts/journal_template.py', '--check', str(root / 'reviews/journal-2026-11-12.md'))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("'## 7. 시스템' 절이 없습니다", bad.stdout)
            merged = self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-13', '--merge')
            self.assertEqual(json.loads(merged.stdout), {'merged': '2026-11-13'})
            refilled = self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-13', '--report').stdout
            blank = self.run_cli(home, 'scripts/trading_journal.py', '--day', '2026-11-13', '--report', '--blank').stdout
            self.assertIn('> 반등', refilled)  # the merged analysis comes back in the report …
            self.assertEqual(blank.count(T.PLACEHOLDER), 7)  # … and --blank gives the bare skeleton to write anew
            self.assertEqual(tj.load('2026-11-13', root / 'trading-journal.sqlite3')['perspective']['summary'], '반등')
            self.assertEqual({oct((root / f'reviews/journal-2026-11-13.{e}').stat().st_mode & 0o777) for e in ('json', 'md')}, {'0o600'})
            month = tj.load_period('2026-11', root / 'trading-journal.sqlite3')  # refreshed by the day's merge
            self.assertEqual(month['lessons']['engine_notes'], [['2026-11-13', '급락 뒤 반등']])
            (root / 'reviews/journal-2026-11.json').write_text(json.dumps({'period': '2026-11', 'summary': '11월 정리'}))
            period = self.run_cli(home, 'scripts/trading_journal.py', '--period', 'month', '--key', '2026-11', '--merge')
            self.assertEqual(json.loads(period.stdout), {'merged': '2026-11'})
            self.assertEqual(tj.load_period('2026-11', root / 'trading-journal.sqlite3')['perspective']['summary'], '11월 정리')


class PeriodJournalTests(unittest.TestCase):
    def test_build_store_report_and_engine_brief(self):
        with tempfile.TemporaryDirectory() as d, patch.object(tj, 'REVIEWS', Path(d)):
            db = Path(d) / 'j.sqlite3'
            tj.store(day_record('2026-11-12', -300, {'summary': '손실', 'strategy_improvements': ['추격 매수 줄이기']}), db)
            tj.store(day_record('2026-11-13', 500), db)
            tj.store(day_record('2026-12-01', 100), db)
            (Path(d) / 'journal-2026-Q4.json').write_text(json.dumps({'summary': '4분기 정리', 'engine_notes': ['폭락일 뒤 반등']}))
            self.assertEqual(tj.update_periods('2026-11-13', db), ['2026-11', '2026-Q4', '2026-H2', '2026'])
            month, quarter = tj.load_period('2026-11', db), tj.load_period('2026-Q4', db)
            self.assertEqual((month['days'], quarter['days']), (2, 3))
            self.assertEqual(quarter['books']['paper']['performance']['realized_krw'], 300)
            self.assertEqual(quarter['monthly'], {'2026-11': {'모의 (Jev)': 200}, '2026-12': {'모의 (Jev)': 100}})
            self.assertEqual(quarter['routes']['Jev 후보'], {'n': 3, 'wins': 2, 'realized_krw': 300})
            self.assertEqual(quarter['system']['errors'], {'x': 3})
            self.assertEqual(quarter['market']['코스피']['down'], [['2026-11-12', -3.0]])  # stored as JSON
            for record in (month, quarter, tj.build_period('year', '2025', db)):  # a period without days still has every section
                md = tj.period_report_md(record)
                self.assertEqual(T.check(md, 'period'), [], md)
                self.assertEqual([line[3:] for line in md.splitlines() if line.startswith('## ')], T.headings('period'))
            md = tj.period_report_md(month)
            self.assertIn('| 2026-11-12 | -300원 |', md)  # a month lists its days
            self.assertIn('- 2026-11-12 · 추격 매수 줄이기', md)  # the daily lessons before the period analysis is written
            self.assertIn('> 4분기 정리', tj.period_report_md(quarter))
            self.assertIn('- 폭락일 뒤 반등', tj.period_report_md(quarter))
            brief = tj.brief(datetime(2026, 11, 14, 9, tzinfo=KST), db=db)
            self.assertEqual([p['period'] for p in brief['periods']], ['2026년 11월', '2026년 4분기'])
            self.assertEqual(brief['periods'][1]['engine_notes'], ['폭락일 뒤 반등'])

    def test_merging_a_day_never_collected_collects_it_in_full_first(self):
        with tempfile.TemporaryDirectory() as d, patch.object(tj, 'REVIEWS', Path(d)), \
                patch.object(tj, 'build', return_value=day_record('2026-11-16', 10)) as build:
            tj.merge('2026-11-16', Path(d) / 'j.sqlite3')
        build.assert_called_once_with('2026-11-16')  # with the network: market, news, charts

    def test_merging_a_written_day_refreshes_its_periods(self):
        with tempfile.TemporaryDirectory() as d, patch.object(tj, 'REVIEWS', Path(d)):
            db = Path(d) / 'j.sqlite3'
            tj.store(day_record('2026-11-13', 500), db)
            (Path(d) / 'journal-2026-11-13.json').write_text(json.dumps({'summary': '반등', 'system_improvements': ['조회 실패 재시도']}))
            tj.merge('2026-11-13', db)
            self.assertIn('- 2026-11-13 · 조회 실패 재시도', tj.period_report_md(tj.load_period('2026', db)))


if __name__ == '__main__':
    unittest.main()
