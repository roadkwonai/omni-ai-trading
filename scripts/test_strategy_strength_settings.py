import tempfile
import unittest
from pathlib import Path

import nh_paper_isolated as p
import strategy_settings as s


class StrengthSettingsTests(unittest.TestCase):
    def test_every_preset_is_a_valid_setting_and_the_runner_applies_it(self):
        for name, preset in s.STRENGTH_PRESETS.items():
            with self.subTest(preset=name):
                self.assertEqual(s.problems({**s.DEFAULTS, **preset}), [])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'nhplug' / 'shared-etf-paper'
            root.mkdir(parents=True)
            s.write_settings(root, {**s.DEFAULTS, **s.STRENGTH_PRESETS['적극적']})
            p.apply_settings(root)
            self.assertEqual((p.STRENGTH_TOP_N, p.STRENGTH_JEV_MIN, p.STRENGTH_CHANGE), (5, 0.10, (1.0, 29.0)))
            s.write_settings(root, dict(s.DEFAULTS))
            p.apply_settings(root)
            self.assertEqual((p.STRENGTH_TOP_N, p.STRENGTH_JEV_MIN), (3, 0.15))

    def test_inverted_or_fractional_values_are_refused(self):
        self.assertTrue(s.problems({**s.DEFAULTS, 'strength_change_min': 10.0, 'strength_change_max': 8.0}))
        self.assertTrue(s.problems({**s.DEFAULTS, 'strength_top_n': 2.5}))


if __name__ == '__main__':
    unittest.main()
