import json
import re
import unittest
import unittest.mock

import guides


class GuideTests(unittest.TestCase):
    def test_every_diagram_builds_with_the_rules_in_force(self):
        v = guides.values()
        for key, (title, build) in guides.DIAGRAMS.items():
            dot = build(v)
            self.assertTrue(dot.startswith('digraph') and dot.count('{') == dot.count('}'), key)
            self.assertNotIn('{v[', dot)  # every value was filled in
        self.assertIn(f"{v['entry_confidence']:.2f}", guides.entry_dot(v))

    def test_the_accounts_guide_explains_the_live_sizing_bases(self):
        rows = dict(dict(guides.GUIDES['accounts']['sections'])['실계좌 매수 크기 기준 · 1주 기준과 금액 기준'])
        for name in ('1주 기준', '금액 기준', '1주 미만 몫', '차이가 나는 곳', '바꾸면'):
            self.assertIn(name, rows)

    def test_every_screen_guide_is_complete_and_points_to_real_diagrams(self):
        for key, guide in guides.GUIDES.items():
            self.assertTrue(guide['purpose'] and guide['steps'], key)
            self.assertTrue(set(guide['diagrams']) <= set(guides.DIAGRAMS) | {'limits', 'engines'}, key)  # rule cards · comparison cards
        self.assertEqual(set(guides.TOPICS), set(guides.TOPIC_TEXT))

    def test_premarket_and_held_position_guides_reflect_current_flow(self):
        v = guides.values()
        data = guides.data_dot(v)
        self.assertIn('premarket -> jev', data)
        self.assertIn('premarket -> vibe', data)
        self.assertIn('보유 중이면 추가 매수', guides.strength_dot(v))
        self.assertNotIn('이미 보유한 종목은 건너', guides.topic('strength', v))
        rows = dict((k, (a, b)) for k, a, b in guides.engine_rows(v))
        self.assertIn('09:00 개장부터', rows['판단 주기'][0])
        self.assertIn('국면 판정', rows['판단'][1])

    def test_app_tabs_use_existing_guide_keys(self):
        source = open(guides.__file__.replace('guides.py', 'app.py'), encoding='utf-8').read()
        used = set(re.findall(r"help_row\('(\w+)'", source)) | {'etf', 'live_desk'}
        self.assertTrue(used <= set(guides.GUIDES), used - set(guides.GUIDES))

    def test_new_listing_guides_use_the_saved_holding_limits_and_time_stop(self):
        # 11-18 review: "신규주는 한 번에 한 종목" was hard-coded while ipo_max_positions is a 1~3 setting
        v = {**guides.values(), 'ipo_max_positions': 2, 'ipo_daily_max': 3, 'ipo_time_stop_min': 60}
        for text in (guides.ipo_dot(v), json.dumps(guides.ipo_flow(v), ensure_ascii=False), guides.topic('ipo', v)):
            self.assertNotIn('한 번에 한 종목', text)
            self.assertIn('동시 2종목', text)
            self.assertIn('60분', text)

if __name__ == '__main__':
    unittest.main()


def _topic_page(key):
    import guides
    guides.topic_view(key, guides.values())


def test_topics_render_as_summary_card_and_callouts_and_limits_as_rule_cards():
    """2026-11-19: topics were one wall of markdown; 제한 규칙 a long bullet list."""
    from streamlit.testing.v1 import AppTest
    from restrictions import RULES
    page = AppTest.from_function(_topic_page, args=('limits',), default_timeout=30).run()
    assert not page.exception
    html = ' '.join(m.value for m in page.markdown)
    assert html.count('class="gd-rule gd-') == len(RULES)
    assert 'gd-badge gd-on' in html and 'gd-badge gd-fix' in html and '\\~' not in html
    page = AppTest.from_function(_topic_page, args=('oversold',), default_timeout=30).run()
    html = ' '.join(m.value for m in page.markdown)
    assert 'class="gd-lead"' in html and '>근거</span>' in html and '>바꾸는 곳</span>' in html


def test_the_live_topic_describes_the_accounts_as_they_are():
    import guides
    with unittest.mock.patch('accounts.load', return_value={
            'live-1': {'kind': 'live', 'label': '실계좌 1', 'engine': 'jev', 'live_orders': True},
            'live-2': {'kind': 'live', 'label': '실계좌 2', 'engine': 'longterm'}}):
        text = guides.live_text()
    assert '실계좌 1 Jev 트레이딩(실주문 켜짐)' in text and '실계좌 2 장기투자(계획만)' in text
    assert '관찰 모드' not in text and '인버스' not in text and '1,000,000,000' not in text
