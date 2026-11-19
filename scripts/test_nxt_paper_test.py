import unittest
from unittest.mock import patch

import nxt_paper_test as t


class NxtTestPlanTests(unittest.TestCase):
    def test_limit_is_far_below_the_quote_on_the_tick_grid_and_inside_the_band(self):
        limit = t.limit_price(208000, 206000)
        self.assertEqual(limit, 156000)           # 208,000 x 0.75 on a 100-won tick
        self.assertGreater(limit, 206000 * 0.7)   # still above the -30% lower limit
        self.assertIsNone(t.limit_price(208000, 300000))  # quote far under the previous close: no safe price

    def test_outside_the_window_nothing_is_sent(self):
        from datetime import datetime
        with patch.object(t, 'datetime') as clock, patch('src.config.loader.load_agent_config') as configs:
            clock.now.return_value = datetime(2026, 11, 12, 9, 0, tzinfo=t.KST)
            with patch.object(t.Path, 'write_text'), patch.object(t.os, 'chmod'):
                result = t.main()
            configs.assert_not_called()
        self.assertEqual(result['outcome'], 'skipped')


if __name__ == '__main__':
    unittest.main()
