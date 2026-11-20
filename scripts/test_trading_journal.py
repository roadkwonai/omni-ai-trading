import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import trading_journal as tj

KST = ZoneInfo('Asia/Seoul')


def fill(time, side, qty, price, pnl=None, ret=None, symbol='005930'):
    return {'time': time, 'symbol': symbol, 'side': side, 'qty': qty, 'price': price, 'amount': qty * price, 'pnl': pnl,
            'return_pct': ret, 'path': 'Jev 후보'}


DAY = [fill('2026-11-16T09:30:00', 'BUY', 10, 1000), fill('2026-11-16T10:00:00', 'SELL', 5, 1100, 500, 10.0),
       fill('2026-11-16T11:00:00', 'SELL', 5, 900, -500, -10.0), fill('2026-11-16T13:00:00', 'BUY', 2, 500, symbol='000660'),
       fill('2026-11-16T14:00:00', 'SELL', 2, 600, 200, 20.0, symbol='000660')]


class JournalTests(unittest.TestCase):
    def test_performance_of_a_day(self):
        p = tj.performance(DAY)
        self.assertEqual((p['buys'], p['sells'], p['bought_krw'], p['realized_krw']), (2, 3, 11000, 200))
        self.assertEqual((p['wins'], p['losses'], p['win_rate'], p['profit_factor']), (2, 1, 67, 1.4))
        self.assertEqual((p['avg_win_pct'], p['avg_loss_pct'], p['return_on_buys_pct']), (15.0, -10.0, 1.82))
        self.assertEqual(p['peak'], {'time': '2026-11-16T10:00:00', 'realized_krw': 500})  # the day's best minute
        self.assertEqual(p['by_symbol'], {'000660': 200, '005930': 0})
        self.assertEqual((p['best_trade']['pnl'], p['worst_trade']['pnl']), (500, -500))
        self.assertEqual(tj.performance([])['realized_krw'], 0)

    def test_store_days_period_and_brief(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            for day, pnl in (('2026-11-13', 300), ('2026-11-16', -100), ('2026-12-01', 50)):
                rows = [fill(f'{day}T09:30:00', 'BUY', 1, 1000), fill(f'{day}T10:00:00', 'SELL', 1, 1000 + pnl, pnl, pnl / 10)]
                tj.store({'day': day, 'books': {'paper': {'label': '모의 (Jev)', 'fills': rows, 'performance': tj.performance(rows)}},
                          'system': {'by_entry_route': {'Jev 후보': {'n': 1, 'wins': int(pnl > 0), 'realized_krw': pnl}}},
                          'perspective': {'summary': f'{day} 요약', 'engine_notes': ['전쟁 뉴스에 지수 ETF가 크게 흔들림']}}, db)
            self.assertEqual([r['day'] for r in tj.days('2026-11', '2026-11', db)], ['2026-11-13', '2026-11-16'])  # a month
            self.assertEqual(len(tj.days('2026', '2026', db)), 3)  # a year
            book = tj.period(tj.days('2026-11', '2026-11', db))['paper']
            self.assertEqual((book['performance']['realized_krw'], book['up_days'], book['max_drawdown_krw']), (200, 1, -100))
            self.assertEqual((book['best_day'][0], book['worst_day'][0]), ('2026-11-13', '2026-11-16'))
            b = tj.brief(datetime(2026, 11, 17, 9, 0, tzinfo=KST), db=db)
            self.assertEqual([s['day'] for s in b['sessions']], ['2026-11-16', '2026-11-13'])  # before today, newest first
            self.assertEqual(b['sessions'][0]['engine_notes'], ['전쟁 뉴스에 지수 ETF가 크게 흔들림'])
            self.assertEqual(tj.brief(db=Path(d) / 'missing.sqlite3'), {})

    def test_merge_attaches_the_written_perspective(self):
        with tempfile.TemporaryDirectory() as d, patch.object(tj, 'REVIEWS', Path(d)):
            db = Path(d) / 'j.sqlite3'
            tj.store({'day': '2026-11-16', 'books': {}, 'perspective': None}, db)
            (Path(d) / 'journal-2026-11-16.json').write_text(json.dumps({'summary': '하루 요약', 'retrospective': ['복기']}, ensure_ascii=False))
            tj.merge('2026-11-16', db)
            self.assertEqual(tj.load('2026-11-16', db)['perspective']['retrospective'], ['복기'])

    def test_merge_picks_up_the_improvement_report_written_after_collection(self):
        with tempfile.TemporaryDirectory() as d, patch.object(tj, 'REVIEWS', Path(d)):
            db = Path(d) / 'j.sqlite3'
            tj.store({'day': '2026-11-16', 'books': {}, 'system': {'health': 'ok', 'improvement_report': None}}, db)
            tj.merge('2026-11-16', db)  # no report yet: stays empty
            self.assertIsNone(tj.load('2026-11-16', db)['system']['improvement_report'])
            (Path(d) / 'improvement-2026-11-16.md').write_text('# 개선안', encoding='utf-8')
            tj.merge('2026-11-16', db)
            system = tj.load('2026-11-16', db)['system']
            self.assertEqual((system['improvement_report'], system['health']), ('# 개선안', 'ok'))


    def test_issue_ranking_stars_and_report_sections(self):
        record = {'day': '2026-11-16', 'books': {'paper': {'label': '모의 (Jev)', 'fills': [fill('2026-11-16T09:30:00', 'BUY', 1, 1000)],
                                                         'performance': tj.performance([]), 'round_trips': []}},
                  'market': {'나스닥': {'close': 1.0, 'change_pct': -4.5, 'on': '2026-11-14'}},
                  'issues': {'전쟁·분쟁': [{'title': '중동 전쟁 확전 우려에 공습', 'publisher': 'A', 'link': 'https://a/1'},
                                          {'title': '중동 전쟁 확전 공습 이어져', 'publisher': 'B', 'link': 'https://a/2'}],
                             '미국 증시': [{'title': '뉴욕증시 폭락 나스닥 4% 하락', 'publisher': 'C', 'link': 'https://a/3'}],
                             '코인': [{'title': '비트코인 보합', 'publisher': 'D', 'link': 'https://a/4'}]},
                  'flows': {'market': {'KOSPI': {'개인': 5000.0, '외국인': -4000.0, '기관': -900.0, 'program': {'차익': -10.0, '비차익': -20.0, '전체': -30.0},
                                                 'breadth': {'상승': 100.0, '하락': 800.0}}},
                            'stocks': {'005930': {'외국인': {'shares': 100.0, 'value_100m': 2.0}, '기관': {'shares': -50.0, 'value_100m': -1.0},
                                                  '개인': {'shares': -50.0, 'value_100m': -1.0}, 'provisional': True}}}}
        heads, issues = tj.rank_issues(record)
        self.assertEqual(issues[0]['category'], '미국 증시')  # weight 3 + 폭락 + Nasdaq -4.5% (+2)
        self.assertEqual((issues[0]['stars'], issues[-1]['category'], issues[-1]['stars']), (4, '코인', 1))  # 6 points
        self.assertIn('나스닥 -4.5%', issues[0]['top'][0]['why'])
        self.assertEqual(tj.star_text(3), '★★★☆☆')
        md = tj.report_md(record)
        self.assertIn('## 오늘의 핵심 이슈', md)
        self.assertIn('1. ★★★★☆ **미국 증시 — [뉴욕증시 폭락 나스닥 4% 하락](https://a/3)**', md)
        self.assertIn('| 코스피 | **+5,000** | **-4,000** | -900 | -10 | -20 | -30 | 100 / 800 |', md)
        self.assertIn('| [삼성전자](https://m.stock.naver.com/domestic/stock/005930/total) | +100주 (+2.0억) | -50주 (-1.0억) | -50주 (-1.0억) | 잠정 |', md)


    def test_each_account_reads_its_own_trades_against_the_chart(self):
        """Review 4 (2026-11-19): section 3 pooled the paper and live fills of a code (P&L, counts, averages)."""
        paper = [fill('2026-11-16T09:30:00', 'BUY', 10, 1000), fill('2026-11-16T10:00:00', 'SELL', 10, 1050, 500, 5.0)]
        live = [fill('2026-11-16T09:31:00', 'BUY', 1, 1010), fill('2026-11-16T10:01:00', 'SELL', 1, 1060, 50, 4.95)]
        bars = [['09:00', 1000, 1100, 900, 1000, 10], ['10:30', 1000, 1100, 950, 1050, 10]]
        md = tj.report_md({'day': '2026-11-16', 'charts': {'005930': {'bars': bars, 'prev_close': 990}},
                           'books': {'paper': {'label': '모의 (Jev)', 'fills': paper, 'performance': tj.performance(paper)},
                                     'live-1': {'label': '실계좌 1', 'fills': live, 'performance': tj.performance(live)}}})
        link = tj.stock_link('005930')
        self.assertIn(f'### [005930]({link}) · 모의 (Jev) · 매수 1 · 매도 1 · 실현 +500원', md)
        self.assertIn(f'### [005930]({link}) · 실계좌 1 · 매수 1 · 매도 1 · 실현 +50원', md)
        self.assertIn('- 매수 평균 1,000원', md)
        self.assertIn('- 매수 평균 1,010원', md)
        self.assertNotIn('+550원', md)

    def test_day_list_names_the_stored_days_without_reading_them(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            self.assertEqual(tj.day_list(db), [])
            for day in ('2026-11-16', '2026-11-13'):
                tj.store({'day': day, 'books': {}}, db)
            self.assertEqual(tj.day_list(db), ['2026-11-13', '2026-11-16'])


class ReviewFixTests(unittest.TestCase):
    """Fixes from the 2026-11-13 review."""

    def test_a_losing_day_has_no_peak_and_no_best_trade(self):
        p = tj.performance([fill('2026-11-11T09:00:00', 'BUY', 1, 100), fill('2026-11-11T10:00:00', 'SELL', 1, 75, -25, -25.0)])
        self.assertIsNone(p['peak'])
        self.assertIsNone(p['best_trade'])
        self.assertEqual(p['worst_trade']['pnl'], -25)

    def test_written_perspective_of_any_shape_is_made_readable(self):
        view = tj.clean_perspective({'summary': 5, 'views': 'abc', 'retrospective': '하나', 'watch_next': ['a', None, 3],
                                     'top_issues': ['x', {'title': 'y', 'stars': float('inf'), 'links': 'z'}], 'sources': {'a': 1}})
        self.assertEqual((view['summary'], view['views'], view['retrospective'], view['watch_next']), ('5', {}, ['하나'], ['a', '3']))
        self.assertEqual([i['title'] for i in view['top_issues']], ['y'])
        self.assertEqual((view['top_issues'][0]['links'], view['sources']), ([], []))
        self.assertEqual(tj.star_count(float('inf')), 3)
        md = tj.report_md({'day': '2026-11-16', 'books': {}, 'perspective': view,
                           'issues': {'시황': [{'title': '코스피 $100 돌파 $ 200'}]}, 'engine_news': [{'title': 't'}]})
        self.assertIn('\\$100', md)
        self.assertNotIn('javascript:', tj.report_md({'day': '2026-11-16', 'books': {}, 'perspective': tj.clean_perspective(
            {'top_issues': [{'title': 'x', 'links': [{'title': 'bad', 'url': 'javascript:alert(1)'}]}],
             'sources': [{'title': 's', 'url': 'javascript:x'}]})}))

    def test_stored_rows_are_cleaned_on_read_and_a_failed_read_is_not_cached(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            tj.store({'day': '2026-11-16', 'books': {}, 'perspective': {'views': 'abc', 'top_issues': 'x'}}, db)
            self.assertEqual(tj.load('2026-11-16', db)['perspective']['views'], {})
            with patch.object(tj, '_days', side_effect=tj.sqlite3.OperationalError('database is locked')):
                self.assertEqual(tj.days(db=db), [])
            self.assertEqual([r['day'] for r in tj.days(db=db)], ['2026-11-16'])  # the next run reads it

    def test_rebuild_keeps_what_only_that_day_captured(self):
        old = {'day': '2026-11-10', 'books': {}, 'market_context': {'time': 't'},
               'flows': {'market': {'KOSPI': {'개인': 1.0, 'program': {'전체': 5.0}, 'breadth': {'상승': 300}}}},
               'charts': {'X': {'bars': [['09:00', 1, 1, 1, 1, 1]], 'interval': '1m'}, 'Y': {'bars': [['09:00', 1, 1, 1, 1, 1]], 'interval': '60m'}}}
        new = {'day': '2026-11-10', 'books': {}, 'flows': {'market': {'KOSPI': {'개인': 2.0}}},
               'charts': {'X': {'bars': [['09:00', 2, 2, 2, 2, 2]], 'interval': '5m'}, 'Y': {'bars': [['09:00', 3, 3, 3, 3, 3]], 'interval': '5m'}}}
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            tj.store(old, db)
            with patch.object(tj, 'build', return_value=new):
                tj.rebuild('2026-11-10', db)
            got = tj.load('2026-11-10', db)
        kospi = got['flows']['market']['KOSPI']
        self.assertEqual((kospi['개인'], kospi['program'], kospi['breadth']), (2.0, {'전체': 5.0}, {'상승': 300}))
        self.assertEqual(got['market_context'], {'time': 't'})
        self.assertEqual((got['charts']['X']['interval'], got['charts']['Y']['interval']), ('1m', '5m'))

    def test_rebuild_keeps_fuller_charts_and_what_sources_no_longer_return(self):
        full = [[f'09:{m:02d}', 1, 1, 1, 1, 1] for m in range(30)]
        old = {'day': '2026-11-16', 'books': {}, 'charts': {'X': {'bars': full, 'interval': '1m', 'source': 'NH'}},
               'market': {'코스피': {'close': 5094.64, 'change_pct': -3.7, 'on': '2026-11-16'}, '나스닥': {'close': 1.0, 'on': '2026-11-13'}},
               'issues': {'시황': [{'title': 'a'}], '코인': [{'title': 'b'}]}, 'engine_news': [{'title': 'n'}], 'analysts': {'X': {}}}
        new = {'day': '2026-11-16', 'books': {}, 'charts': {'X': {'bars': full[:1], 'interval': '1m', 'source': 'Yahoo 1m'}},
               'market': {'나스닥': {'close': 2.0, 'on': '2026-11-13'}}, 'issues': {'시황': [], '코인': [{'title': 'c'}]},
               'engine_news': [], 'analysts': {}}
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            tj.store(old, db)
            with patch.object(tj, 'build', return_value=new):
                tj.rebuild('2026-11-16', db)
            got = tj.load('2026-11-16', db)
        self.assertEqual(len(got['charts']['X']['bars']), 30)  # same interval, more bars: the old chart stays
        self.assertEqual(got['market']['코스피']['change_pct'], -3.7)  # missing now: kept
        self.assertEqual(got['market']['나스닥']['close'], 2.0)  # built later: the fresh value
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            tj.store({**old, 'built': '2026-11-16T15:29:21+09:00'}, db)
            with patch.object(tj, 'build', return_value=json.loads(json.dumps(new))):
                tj.rebuild('2026-11-16', db)
            kept = tj.load('2026-11-16', db)
            self.assertEqual(kept['market']['나스닥']['close'], 1.0)  # built on the day: the close snapshot
            self.assertEqual(kept['issues'], old['issues'])  # and the headlines the written journal cites
        self.assertEqual(got['issues'], {'시황': [{'title': 'a'}], '코인': [{'title': 'c'}]})
        self.assertEqual((got['engine_news'], got['analysts']), ([{'title': 'n'}], {'X': {}}))

    def test_market_is_as_of_the_korean_close_of_that_day(self):
        from zoneinfo import ZoneInfo
        ny, utc = ZoneInfo('America/New_York'), ZoneInfo('UTC')
        series = {  # a Korean index whose 11/16 bar the source has dropped, and a US session that opens after the close
            '^KS11': [(datetime(2026, 11, 12, 9, tzinfo=KST), 0, 0, 0, 100.0), (datetime(2026, 11, 13, 9, tzinfo=KST), 0, 0, 0, 99.0)],
            '^KQ11': [(datetime(2026, 11, 13, 9, tzinfo=KST), 0, 0, 0, 50.0), (datetime(2026, 11, 16, 9, tzinfo=KST), 0, 0, 0, 48.0)],
            '^IXIC': [(datetime(2026, 11, 12, 9, 30, tzinfo=ny), 0, 0, 0, 200.0), (datetime(2026, 11, 13, 9, 30, tzinfo=ny), 0, 0, 0, 198.0),
                      (datetime(2026, 11, 16, 9, 30, tzinfo=ny), 0, 0, 0, 190.0)],
            'BTC-USD': [(datetime(2026, 11, 15, tzinfo=utc), 0, 0, 0, 10.0), (datetime(2026, 11, 16, tzinfo=utc), 0, 0, 0, 11.0)],
            # a still-open FX bar is stamped with its last trade time (08:19 KST the next morning), not its start
            'KRW=X': [(datetime(2026, 11, 13, tzinfo=utc), 0, 0, 0, 1000.0), (datetime(2026, 11, 16, 23, 19, tzinfo=utc), 0, 0, 0, 1010.0)],
            'CL=F': [(datetime(2026, 11, 13, tzinfo=ny), 0, 0, 0, 80.0), (datetime(2026, 11, 16, tzinfo=ny), 0, 0, 0, 84.0)]}
        import oversold_research
        with patch.object(oversold_research, 'fetch', side_effect=lambda symbol, *a, **k: series.get(symbol, [])):
            got = tj.market('2026-11-16')
        self.assertNotIn('코스피', got)
        self.assertEqual((got['코스닥']['on'], got['코스닥']['change_pct']), ('2026-11-16', -4.0))
        self.assertEqual((got['나스닥']['on'], got['나스닥']['close']), ('2026-11-13', 198.0))
        self.assertEqual(got['비트코인']['on'], '2026-11-16')  # started 09:00 KST, before the close
        self.assertEqual((got['원/달러']['on'], got['원/달러']['change_pct']), ('2026-11-16', 1.0))
        self.assertEqual(got['WTI 유가']['on'], '2026-11-16')  # futures session began the evening before

    def test_chart_prefers_the_runners_partial_bars_over_a_thinner_source(self):
        own = [[f'{9 + m // 60:02d}:{m % 60:02d}', 1, 1, 1, 1, 1] for m in range(200)]  # 09:00~12:19, not the whole day
        def fetch(symbol, interval, rng, volume=False):
            if interval == '1d':
                return [(datetime(2026, 11, 13, 9, tzinfo=KST), 0, 0, 0, 100.0)]
            return [(datetime(2026, 11, 16, 15, 19, tzinfo=KST), 1, 1, 1, 1, 1)] if interval == '1m' else []
        import oversold_cases
        import oversold_research
        with patch.object(tj, '_ledger_bars', return_value=own), patch.object(oversold_research, 'fetch', side_effect=fetch), \
             patch.object(oversold_cases, 'symbol_of', return_value='100790.KQ'):
            got = tj.chart_data('2026-11-16', '100790', today=datetime(2026, 11, 17).date())
        self.assertEqual((len(got['bars']), got['source'], got['prev_close']), (200, 'NH (실행기 기록, 일부 시간)', 100.0))

    def test_chart_uses_kiwoom_minutes_before_yahoo_when_the_runner_has_none(self):
        import kiwoom_data
        import oversold_cases
        import oversold_research
        bars = [(datetime(2026, 8, 3, 9, m, tzinfo=KST), 10, 11, 9, 10, 5) for m in range(3)]
        with patch.object(tj, '_ledger_bars', return_value=[]), patch.object(kiwoom_data, 'configured', return_value=True), \
             patch.object(kiwoom_data, 'day_bars', return_value={'bars': bars, 'prev_close': 9.5}), \
             patch.object(oversold_research, 'fetch', return_value=[]) as fetch, \
             patch.object(oversold_cases, 'symbol_of', return_value='005930.KS'):
            got = tj.chart_data('2026-08-03', '005930', today=datetime(2026, 11, 17).date())
        self.assertEqual((got['source'], got['interval'], got['prev_close']), ('Kiwoom 1m', '1m', 9.5))
        self.assertEqual(got['bars'][0], ['09:00', 10, 11, 9, 10, 5])
        self.assertTrue(all(c.args[1] == '1d' for c in fetch.call_args_list))  # no Yahoo minute request

    def test_kiwoom_bars_keep_kiwoom_previous_close_without_a_yahoo_request(self):
        import kiwoom_data
        import oversold_cases
        import oversold_research
        bars = [(datetime(2026, 8, 3, 9, m, tzinfo=KST), 10, 11, 9, 10, 5) for m in range(3)]
        daily = [(datetime(2026, 7, 31, tzinfo=KST), 8.7, 8.7, 8.7, 8.7)]
        with patch.object(tj, '_ledger_bars', return_value=[]), patch.object(kiwoom_data, 'configured', return_value=True), \
             patch.object(kiwoom_data, 'day_bars', return_value={'bars': bars, 'prev_close': 9.5}), \
             patch.object(oversold_research, 'fetch', return_value=daily) as fetch, \
             patch.object(oversold_cases, 'symbol_of', return_value='005930.KS'):
            got = tj.chart_data('2026-08-03', '005930', today=datetime(2026, 11, 17).date())
        self.assertEqual(got['prev_close'], 9.5)  # the same raw-price basis as the bars
        fetch.assert_not_called()

    def test_rebuild_keeps_each_pressure_part_toss_failed_to_return(self):
        good = {'short': {'date': '2026-11-16', 'amount_100m': 7.0}, 'credit': {'date': '2026-11-14', 'margin_balance': 5}, 'lending': None}
        old = {'day': '2026-11-16', 'books': {}, 'flows': {'pressure': {'005930': good}}}
        new = {'day': '2026-11-16', 'books': {}, 'flows': {'pressure': {'005930': {
            'short': {'error': 'RuntimeError'}, 'credit': {'date': '2026-11-16', 'margin_balance': 6}, 'lending': {'error': 'RuntimeError'}}}}}
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'j.sqlite3'
            tj.store(old, db)
            with patch.object(tj, 'build', return_value=new):
                tj.rebuild('2026-11-16', db)
            got = tj.load('2026-11-16', db)['flows']['pressure']['005930']
        self.assertEqual(got['short'], good['short'])  # failed now: the earlier fetch stays
        self.assertEqual(got['credit']['margin_balance'], 6)  # fetched now: the fresh value
        self.assertEqual(got['lending'], {'error': 'RuntimeError'})  # nothing better kept

    def test_investor_leaders_keep_only_rankings_of_that_day(self):
        import toss_data
        ranked = {'외국인': {'ranked_at': '2026-11-17T15:20:00+09:00', 'net_buy': [1]},
                  '개인': {'ranked_at': '2026-11-16T20:20:00+09:00', 'net_buy': [2]}}
        with patch.object(toss_data, 'configured', return_value=True), patch.object(toss_data, 'investor_rankings', return_value=ranked):
            self.assertEqual(list(tj.investor_leaders('2026-11-17')), ['외국인'])

    def test_daily_range_reaches_old_days(self):
        today = datetime.now(KST).date()
        from datetime import timedelta
        self.assertEqual([tj._daily_range(today - timedelta(days=n)) for n in (10, 200, 900)], ['3mo', '2y', '10y'])


if __name__ == '__main__':
    unittest.main()


def test_live_rows_carry_the_holding_after_each_fill_like_the_engine_journal():
    # 11-20: the first long-term live fills broke 종합 현황 (engine_compare.positions read r['after'], KeyError)
    import engine_compare as ec
    fills = [{'time': '2026-11-20T09:40:01+09:00', 'symbol': '379810', 'side': 'BUY', 'qty': 5, 'price': 20000},
             {'time': '2026-11-20T09:41:01+09:00', 'symbol': '379810', 'side': 'SELL', 'qty': 2, 'price': 21000}]
    with patch('live_fills.rows', return_value=fills):
        rows = tj._live_rows('live-2', '장기투자')
    assert [(r['after'], r['avg']) for r in rows] == [(5, 20000.0), (3, 20000.0)]
    assert rows[1]['cost'] == 40000.0 and rows[1]['pnl'] == 2000.0
    assert ec.positions(rows)['379810']['qty'] == 3 and ec.summary(rows)['sells'] == 1
    import engine_compare
    shape = set(engine_compare.journal.__doc__.split('[{')[1].split('}]')[0].replace(' ', '').split(','))
    assert shape <= set(rows[0])  # every key the engine journal promises (11-20: 'after', then 'basis')
