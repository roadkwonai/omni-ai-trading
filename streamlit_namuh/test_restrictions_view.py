import unittest
from unittest.mock import patch

import restrictions
import restrictions_view as rv
from strategy_settings import DEFAULTS, LABELS, LIMITS, problems


class RestrictionTests(unittest.TestCase):
    def test_every_switch_is_a_valid_on_by_default_setting(self):
        for key in restrictions.SWITCHES:
            self.assertEqual((DEFAULTS[key], LIMITS[key]), (1, (0, 1)))
            self.assertIn(key, LABELS)
        for key in restrictions.VALUES:
            self.assertIn(key, DEFAULTS)
        self.assertEqual(rv.change_text('flatten_hhmm', 1500), '장 마감 정리 시각 15:00')
        self.assertEqual(rv.change_text('rising_check', 0), '최근 분봉 상승 확인 끔')
        self.assertEqual(problems({**DEFAULTS, 'reentry_guard': 0}), [])
        self.assertTrue(problems({**DEFAULTS, 'reentry_guard': 0.5}))
        switches = [r[4] for r in restrictions.RULES if r[3] == 'switch']
        self.assertEqual(sorted(switches), sorted(restrictions.SWITCHES))
        self.assertTrue(all(r[1] in restrictions.GROUPS for r in restrictions.RULES))

    def test_counts_read_entry_and_strength_reasons_of_the_day(self):
        events = [{'time': '2026-11-13T12:30:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'jev_wait',
                   'strength_reason': 'reentry_below_last_exit'},
                  {'time': '2026-11-13T12:31:00', 'event': 'v2_execution_gate', 'permitted': False, 'reason': 'order_guard_refused'},
                  {'time': '2026-11-13T12:32:00', 'event': 'v2_entry_gate', 'permitted': True, 'reason': 'entry_conditions_met'},
                  {'time': '2026-11-12T12:30:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'volume_not_rising'}]
        found = rv.counts(events, '2026-11-13')
        self.assertEqual((found['reentry'], found['order_guard'], found['volume']), (1, 1, 0))

    def test_value_text_and_live_rows(self):
        self.assertEqual(rv.value_text('volume', {'entry_volume_ratio': 0}), '꺼짐')
        self.assertEqual(rv.value_text('overnight', {'overnight_max_manwon': 300}), '300만 원')
        record = {'kind': 'live', 'budget': {'capital_krw': 1_000_000, 'daily_loss_krw': 20_000, 'order_price_max_krw': 10_000}}
        rows = restrictions.live_rules(record)
        self.assertEqual(rows[0]['지금 값'], '꺼짐(계획만 기록)')
        self.assertIn('운용금', [r['항목'] for r in rows])

    def test_quantity_limits_show_enabled_and_disabled(self):
        with patch('tunables.setting', side_effect=lambda key: {'paper_max_order_qty': 0, 'paper_max_total_qty': 1000}[key]):
            self.assertEqual(rv.value_text('qty', {}), '1회 제한 없음 · 전체 1,000주')


if __name__ == '__main__':
    unittest.main()
