import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nh_paper_isolated as p

WEAK = {'코스피': {'regime': '하락장', 'today_tone': '혼조'}}


class WeakMarketTests(unittest.TestCase):
    def test_weak_market_is_a_falling_regime_without_a_strong_day_mirrored_for_inverse(self):
        self.assertTrue(p.weak_market(WEAK, '069500', False))
        self.assertFalse(p.weak_market({'코스피': {'regime': '하락장', 'today_tone': '강세'}}, '069500', False))
        self.assertFalse(p.weak_market(WEAK, '114800', True))
        self.assertTrue(p.weak_market({'코스피': {'regime': '상승장', 'today_tone': '혼조'}}, '114800', True))
        self.assertFalse(p.weak_market(None, '069500', False))
        self.assertTrue(p.averaging_down_blocked(WEAK, '069500', False))  # a mixed day in a 하락장 now stops dip-buying too

    def test_weak_targets_take_half_early_and_never_loosen(self):
        with patch.multiple(p, WEAK_HALF_PCT=1.0, WEAK_ALL_PCT=2.0):
            data = p.weak_targets({'profile': 'defensive', 'stop_pct': 2.0, 'half_pct': 1.33, 'all_pct': 1.33})
            wide = p.weak_targets({'profile': 'defensive', 'stop_pct': 2.0, 'half_pct': 3.0, 'all_pct': 3.0})
            tight = p.weak_targets({'profile': 'defensive', 'stop_pct': 2.0, 'half_pct': 0.8, 'all_pct': 0.8})
        self.assertEqual((data['half_pct'], data['all_pct'], data['weak_caps']), (1.0, 1.33, True))   # today's 삼성전자 plan
        self.assertEqual((wide['half_pct'], wide['all_pct']), (1.0, 2.0))
        self.assertEqual((tight['half_pct'], tight['all_pct']), (0.8, 0.8))  # a lower data target stays

    def test_direction_switch_closes_only_losing_holdings_the_market_works_against(self):
        rows = [{'iem_cd': '005930', 'now_pr': 209500, 'phs_pr': 211000}, {'iem_cd': '069500', 'now_pr': 86000, 'phs_pr': 85000},
                {'iem_cd': '114800', 'now_pr': 1200, 'phs_pr': 1300}]
        owned = {'005930': 8, '069500': 10, '114800': 5}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(p.direction_switch_targets(rows, owned, True, {'114800'}, WEAK, d), ['005930'])  # winner 069500 stays
            self.assertEqual(p.direction_switch_targets(rows, owned, True, {'114800'}, {'코스피': {'regime': '하락장', 'today_tone': '강세'}}, d), [])
            (Path(d) / 'exit-requests.json').write_text(json.dumps(['233740']))
            p.request_exits(d, ['005930'])
            self.assertEqual(json.loads((Path(d) / 'exit-requests.json').read_text()), ['005930', '233740'])

    def test_volume_check_follows_the_setting_and_zero_turns_it_off(self):
        rows = [{'vol': '110'}] * 5 + [{'vol': '100'}] * 5  # +10%
        with patch.object(p, 'ENTRY_VOLUME_RATIO', 1.2):
            self.assertEqual(p.volume_check(rows), 'volume_not_rising')
        with patch.object(p, 'ENTRY_VOLUME_RATIO', 1.0):
            self.assertIsNone(p.volume_check(rows))
        with patch.object(p, 'ENTRY_VOLUME_RATIO', 0.0):
            self.assertIsNone(p.volume_check([]))


if __name__ == '__main__':
    unittest.main()
