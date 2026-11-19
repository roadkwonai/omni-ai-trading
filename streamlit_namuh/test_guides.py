import json
import re
import unittest

import guides


class GuideTests(unittest.TestCase):
    def test_every_diagram_builds_with_the_rules_in_force(self):
        v = guides.values()
        for key, (title, build) in guides.DIAGRAMS.items():
            dot = build(v)
            self.assertTrue(dot.startswith('digraph') and dot.count('{') == dot.count('}'), key)
            self.assertNotIn('{v[', dot)  # every value was filled in
        self.assertIn(f"{v['entry_confidence']:.2f}", guides.entry_dot(v))

    def test_every_screen_guide_is_complete_and_points_to_real_diagrams(self):
        for key, guide in guides.GUIDES.items():
            self.assertTrue(guide['purpose'] and guide['steps'], key)
            self.assertTrue(set(guide['diagrams']) <= set(guides.DIAGRAMS), key)
        self.assertEqual(set(guides.TOPICS), set(guides.TOPIC_TEXT))

    def test_premarket_and_held_position_guides_reflect_current_flow(self):
        v = guides.values()
        data = guides.data_dot(v)
        self.assertIn('premarket -> jev', data)
        self.assertIn('premarket -> vibe', data)
        self.assertIn('보유 중이면 추가 매수', guides.strength_dot(v))
        self.assertNotIn('이미 보유한 종목은 건너', guides.topic('strength', v))
        self.assertIn('09:00 개장부터', guides.engines_dot(v))
        self.assertIn('국면 판정', guides.engines_dot(v))

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
