import unittest
from unittest.mock import patch

import journal_view as jv
import trading_journal as tj

DATA = {'bars': [['09:00', 100, 102, 99, 101, 10], ['09:01', 101, 105, 100, 104, 30], ['09:02', 104, 104, 96, 97, 20],
                 ['09:03', 97, 99, 95, 98, 40]], 'interval': '1m', 'source': 'test', 'prev_close': 100}
FILLS = [('모의 (Jev)', {'time': '2026-11-16T09:00:20+09:00', 'symbol': 'X', 'side': 'BUY', 'qty': 10, 'price': 100, 'pnl': None}),
         ('모의 (Jev)', {'time': '2026-11-16T09:01:40+09:00', 'symbol': 'X', 'side': 'SELL', 'qty': 10, 'price': 104, 'pnl': 40, 'return_pct': 4.0})]


class ChartTests(unittest.TestCase):
    def test_vwap_and_fill_minutes(self):
        frame = jv.bar_frame('2026-11-16', DATA)
        self.assertAlmostEqual(frame['vwap'].iloc[0], (102 + 99 + 101) / 3)
        self.assertAlmostEqual(frame['vwap'].iloc[1], ((302 / 3) * 10 + (309 / 3) * 30) / 40)
        trades = jv.fill_frame(FILLS)
        self.assertEqual([t.strftime('%H:%M') for t in trades['time']], ['09:00', '09:01'])  # KST, floored to the bar
        self.assertEqual(list(trades['label']), ['매수 10', '매도 10 +40'])

    def test_analysis_reads_the_trades_against_the_day(self):
        lines = tj.trade_lines(DATA['bars'], 100, [f for _, f in FILLS], DATA['bars'])
        self.assertIn('전일 대비 -2.00%', lines[0])
        self.assertTrue(lines[1].startswith('매수 평균 100원') and '하루 범위의 50% 자리' in lines[1])  # (100-95)/(105-95)
        self.assertIn('뒤 최고 +0.00%', lines[3])  # nothing higher than the sale after 09:01
        self.assertIn('시장 대비 +0.00%', lines[4])

    def test_report_links_every_source(self):
        record = {'day': '2026-11-16', 'books': {'paper': {'label': '모의 (Jev)', 'fills': [f for _, f in FILLS],
                                                         'performance': tj.performance([{**f, 'amount': f['qty'] * f['price']} for _, f in FILLS]),
                                                         'round_trips': [{'symbol': 'X', 'open': '09:00:20', 'close': '09:01:40', 'entry_route': 'Jev 후보',
                                                                          'exit': 'take_profit', 'held_min': 1, 'pnl': 40, 'return_pct': 4.0}]}},
                  'charts': {'X': DATA}, 'market': {'나스닥': {'close': 1.0, 'change_pct': -1.0, 'on': '2026-11-14'}},
                  'issues': {'전쟁·분쟁': [{'title': '휴전 협상 ~ 진전', 'publisher': 'A', 'link': 'https://news.example/1'}]},
                  'engine_news': [{'title': '반도체 강세', 'publisher': 'B', 'minutes': 30, 'link': 'https://news.example/2'}],
                  'analysts': {'X': {'recomm_mean': 4.1, 'target_mean': 120.0, 'report_tone': 'up', 'link': 'https://stock/X',
                                     'reports': [{'text': '20261116 증권: 상향', 'link': 'https://research/1'}]}},
                  'perspective': {'summary': '시장 폭락 뒤 반등', 'retrospective': ['늦은 매수가 좋았다'],
                                  'sources': [{'publisher': 'C', 'date': '2026-11-16', 'title': '기사', 'url': 'https://news.example/3'}]}}
        md = tj.report_md(record)
        for needle in ('# 매매일지 · 2026-11-16 (월)', '> 시장 폭락 뒤 반등', '| 모의 (Jev) | +40원', '익절',
                       '(https://news.example/1)', '휴전 협상 \\~ 진전', '(https://news.example/2) · B · 30분', '(https://research/1)',
                       '## 8. 복기·회고', '- 늦은 매수가 좋았다', '[C · 2026-11-16 · 기사](https://news.example/3)', '### [X]'):
            self.assertIn(needle, md)

    def test_chart_source_prefers_a_full_session_of_runner_bars(self):
        full = [[f'{9 + i // 60:02d}:{i % 60:02d}', 1, 1, 1, 1, 1] for i in range(360)]  # 09:00 ~ 14:59
        with patch.object(tj, '_ledger_bars', return_value={'069500': full}), patch('oversold_research.fetch', return_value=[]):
            self.assertEqual(tj.chart_data('2026-11-16', '069500')['source'], 'NH (실행기 기록)')
        with patch.object(tj, '_ledger_bars', return_value={'036540': full[:100]}), patch('oversold_research.fetch', return_value=[]):
            self.assertEqual(tj.chart_data('2026-11-16', '036540')['source'], 'NH (실행기 기록, 일부 시간)')  # Yahoo empty: what there is


def _views_app():
    import journal_view as jv
    import trading_journal as tj
    fills = [{'time': '2026-11-16T09:00:20+09:00', 'symbol': 'X', 'side': 'BUY', 'qty': 10, 'price': 100, 'amount': 1000, 'pnl': None},
             {'time': '2026-11-16T09:01:40+09:00', 'symbol': 'X', 'side': 'SELL', 'qty': 10, 'price': 104, 'amount': 1040, 'pnl': 40,
              'return_pct': 4.0}]
    record = {'day': '2026-11-16', 'books': {'paper': {'label': '모의 (Jev)', 'fills': fills, 'performance': tj.performance(fills), 'round_trips': []}},
              'market': {'코스피': {'close': 2500.0, 'change_pct': -3.1, 'on': '2026-11-16'}},
              'flows': {'market': {'KOSPI': {'개인': 5000.0, '외국인': -4200.0, '기관': -800.0}}},
              'issues': {'전쟁·분쟁': [{'title': '휴전 협상 결렬', 'publisher': 'A', 'link': 'https://news.example/1'}]},
              'system': {'errors': {'quote_failed': 2}, 'entry_gates': {'rising_check': 5}},
              'charts': {'X': {'bars': [['09:00', 100, 102, 99, 101, 10], ['09:01', 101, 105, 100, 104, 30]], 'interval': '1m',
                               'source': 'test', 'prev_close': 100}, '069500': {'bars': []}}}
    jv.day_view(record)
    jv.period_view([record, {**record, 'day': '2026-11-17'}], '테스트 기간')


def _render_app():
    from unittest.mock import patch

    import journal_view as jv
    import trading_journal as tj
    def rec(day, pnl):
        fills = [{'time': f'{day}T09:00:00+09:00', 'symbol': 'X', 'side': 'BUY', 'qty': 1, 'price': 100, 'amount': 100, 'pnl': None},
                 {'time': f'{day}T10:00:00+09:00', 'symbol': 'X', 'side': 'SELL', 'qty': 1, 'price': 100 + pnl, 'amount': 100 + pnl,
                  'pnl': pnl, 'return_pct': float(pnl)}]
        return {'day': day, 'built': f'{day}T16:00:00+09:00', 'charts': {'X': {'bars': []}, '069500': {'bars': []}},
                'books': {'paper': {'label': '모의 (Jev)', 'fills': fills, 'performance': tj.performance(fills), 'round_trips': []}},
                'perspective': {'top_issues': [{'title': '전쟁', 'stars': '★★★★', 'why': '유가'}]}}
    records = [rec('2026-11-12', -5), rec('2026-11-13', 7)]
    with patch.object(tj, 'days', lambda since='0000', until='9999', db=None: [r for r in records if since <= r['day'][:len(since)] and r['day'][:len(until)] <= until]), \
            patch.object(tj, 'day_list', lambda db=None: [r['day'] for r in records]), \
            patch.object(tj, 'load', lambda day, db=None: next((r for r in records if r['day'] == day), None)):
        jv.render()


class NavigationTests(unittest.TestCase):
    def test_previous_day_and_jump_back_from_the_month(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_render_app, default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(at.caption[0].value.startswith('2026-11-13 (금)'))
        at.button(key='journal_prev').click().run()
        self.assertTrue(at.caption[0].value.startswith('2026-11-12 (목)'))
        self.assertTrue(at.button(key='journal_prev').disabled)
        at.session_state['journal_unit'] = '월'
        at.run()
        self.assertFalse(at.exception, at.exception)
        next(b for b in at.button if b.label == '2026-11-13 일지 열기').click().run()  # the month's best day
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.session_state['journal_unit'], '일')
        self.assertTrue(at.caption[0].value.startswith('2026-11-13 (금)'))
        self.assertIn('★★★★', ' '.join(m.value for m in at.markdown))  # stars written as text still render

    def test_each_mode_keeps_its_choice(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_render_app, default_timeout=30).run()
        at.button(key='journal_prev').click().run()
        at.session_state['journal_unit'] = '월'
        at.run()
        at.session_state['journal_unit'] = '일'
        at.run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(at.caption[0].value.startswith('2026-11-12 (목)'))  # not reset to the latest day

    def test_stars_from_any_form(self):
        self.assertEqual([tj.star_count(v) for v in (4, '5', '★★', None, 9, 'x')], [4, 5, 2, 3, 5, 3])


class ViewTests(unittest.TestCase):
    def test_day_and_period_views_render_cards_and_buttons(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_views_app, default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        labels = [b.label for b in at.button]
        for needle in ('자세히 · 모의 (Jev) 매매', '자세히 · 투자자·프로그램 수급', '자세히 · 이슈·뉴스·애널리스트', '자세히 · 시스템',
                       '자세히 · 누적 손익과 낙폭', '2026-11-16 일지 열기'):
            self.assertIn(needle, labels)
        html = ' '.join(m.value for m in at.markdown)
        self.assertIn('오류 2회', html)
        self.assertIn('-4,200억', html)

    def test_a_card_opens_its_pop_up(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_views_app, default_timeout=30).run()
        next(b for b in at.button if b.label == '자세히 · 시스템').click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn('rising_check', ' '.join(str(d.value) for d in at.dataframe))


if __name__ == '__main__':
    unittest.main()
