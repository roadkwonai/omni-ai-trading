import unittest

import status_cards as sc


class StatusCardTests(unittest.TestCase):
    def test_card_escapes_text_and_keeps_state_in_words(self):
        html = sc.html('코스피 <국면>', '하락장', 'down', 'trending_down', sub='오늘 혼조', help='a "b"')
        self.assertIn('코스피 &lt;국면&gt;', html)
        self.assertIn('aria-label="코스피 &lt;국면&gt;: 하락장 · 오늘 혼조"', html)  # the state is readable without colour
        self.assertIn('title="a &quot;b&quot;"', html)
        self.assertIn(sc.TONES['down'][0], html)

    def test_alert_always_pulses_and_others_only_when_asked(self):
        self.assertIn('sc-pulse', sc.html('x', 'y', 'alert'))
        self.assertIn('sc-alert-ring', sc.html('x', 'y', 'alert'))
        self.assertNotIn('sc-pulse', sc.html('x', 'y', 'ok'))
        self.assertIn('sc-pulse', sc.html('x', 'y', 'ok', pulse=True))

    def test_meanings_map_to_korean_market_colours(self):
        self.assertEqual(sc.regime('상승장')[0], 'up')
        self.assertEqual(sc.regime('하락장')[0], 'down')
        self.assertEqual(sc.service('실행 중'), ('ok', 'play_circle', True))
        self.assertEqual(sc.service('확인 불가')[0], 'alert')
        self.assertEqual([sc.signed(v) for v in (3, -1, 0)], ['up', 'down', 'off'])
        self.assertIn('prefers-reduced-motion', sc.CSS)


if __name__ == '__main__':
    unittest.main()


def test_signed_percent_preserves_small_moves_and_neutral_zero():
    from status_cards import cell_tone, signed_percent

    assert signed_percent(-0.0018) == '-0.0018%'
    assert signed_percent(0.00000002) == '+0.00000002%'
    assert cell_tone(signed_percent(0)) == ''
    assert cell_tone(signed_percent(None)) == ''
