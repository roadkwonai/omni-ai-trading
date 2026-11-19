import unittest

import strategy_view as v
from strategy_settings import DEFAULTS


class StrategyViewTests(unittest.TestCase):
    def test_jev_routes_and_v2_veto(self):
        rules = {**DEFAULTS, 'oversold_on': 1, 'oversold_v2_on': 1}
        jev = v.route_cards(rules)
        self.assertIn('Jev WAIT', dict(jev[0][1])['조건'])
        self.assertTrue(any('신규주' in route[0] for route in jev))
        self.assertFalse(any('vibe' in str(route) for route in jev))

    def test_regime_card_shows_exit_style_and_add_buy_chips(self):
        row = {'국면': '하락장', '오늘 흐름': '혼조·판단 불가', '추가 매수(롱)': '중단', '추가 매수(인버스)': '허용',
               '청산': '약세장: 늦어도 +1% 절반 · <x>', '장 마감': '14:40 정리'}
        html = v.regime_card(row, last=True)
        self.assertIn('약세장 청산', html)
        self.assertIn('롱 추가 중단', html)
        self.assertIn('이전 시황 · 참고', html)
        self.assertNotIn('sv-on', html)
        self.assertIn(v.EXITS['약세장'][0], html)
        self.assertIn(v.ON, html)
        self.assertIn(v.OFF, html)
        self.assertIn('현재 시황 기준', v.regime_card(row, current=True))
        self.assertIn('&lt;x&gt;', html)
        self.assertEqual(v.matching([{'국면': '상승장', '오늘 흐름': '강세'}, row], '하락장', '혼조'), 1)
        self.assertIsNone(v.matching([row], '상승장', '혼조'))

    def test_checklist_escapes_and_numbers_by_css(self):
        html = v.checklist_html(['a<b', 'c'])
        self.assertEqual(html.count('<li>'), 2)
        self.assertIn('a&lt;b', html)

    def test_routes_say_when_the_strategy_slots_go_before_jev_and_the_new_listing_rules(self):
        # 11-18 review: with ipo_slot_on/strength_slot_on saved the cards still said "Jev가 대기할 때" / "후보가 없을 때"
        rules = {**DEFAULTS, 'ipo_slot_on': 1, 'strength_slot_on': 1, 'ipo_max_positions': 2, 'ipo_daily_max': 3, 'ipo_time_stop_min': 45}
        cards = {title.split(' · ')[1]: dict(items) for title, items, *_ in v.route_cards(rules)}
        self.assertIn('Jev 판단보다 먼저', cards['신규주']['언제'])
        self.assertIn('Jev 판단보다 먼저', cards['관심종목 강도']['언제'])
        self.assertIn('동시 2종목 · 하루 3종목', cards['신규주']['크기'])
        self.assertIn('45분', cards['신규주']['청산'])
        off = {title.split(' · ')[1]: dict(items) for title, items, *_ in v.route_cards(
            {**DEFAULTS, 'ipo_slot_on': 0, 'strength_slot_on': 0, 'ipo_time_stop_min': 0})}
        self.assertEqual(off['관심종목 강도']['언제'], 'Jev가 대기할 때')
        self.assertNotIn('분 지나면', off['신규주']['청산'])
        self.assertEqual(v.route_order(rules), '낙폭과대 선별 v2 → 반등 → 신규주 슬롯 → 관심종목 강도 슬롯 → Jev → 관심종목 강도 → 신규주')
        self.assertEqual(v.route_order(off), '낙폭과대 선별 v2 → 반등 → Jev → 관심종목 강도 → 신규주')

    def test_the_inverse_add_chip_is_amber_when_it_depends_on_the_tape(self):
        row = {'국면': '하락장', '오늘 흐름': '약세', '추가 매수(롱)': '중단', '추가 매수(인버스)': '조건부',
               '청산': '약세장: x', '인버스 청산': '손절 −2%', '장 마감': '14:40 정리'}
        html = v.regime_card(row)
        self.assertIn('인버스 추가 조건부', html)
        self.assertIn('손절 −2%', html)

if __name__ == '__main__':
    unittest.main()
