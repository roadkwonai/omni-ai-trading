import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import strategy_settings as s
import nh_paper_isolated as p


class StrategySettingsTests(unittest.TestCase):
    def test_scale_switches_default_for_old_file_and_save_individually(self):
        keys = ('scale_signal_add_on', 'scale_step_add_on', 'scale_profit_rebuy_on')
        with tempfile.TemporaryDirectory() as temp:
            old = {k: v for k, v in s.DEFAULTS.items() if k not in keys}
            (Path(temp) / s.FILE).write_text(json.dumps(old))
            settings, status = s.read_settings(temp)
            self.assertEqual(status, 'custom')
            self.assertEqual([settings[k] for k in keys], [1, 1, 1])
            settings.update({'scale_signal_add_on': 0, 'scale_step_add_on': 1, 'scale_profit_rebuy_on': 0})
            s.write_settings(temp, settings)
            saved, status = s.read_settings(temp)
            self.assertEqual(status, 'custom')
            self.assertEqual([saved[k] for k in keys], [0, 1, 0])
            for key in keys:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    s.write_settings(temp, {**saved, key: 0.5})

    def test_scale_interval_defaults_for_old_file_and_validates_minutes(self):
        with tempfile.TemporaryDirectory() as temp:
            old = {k: v for k, v in s.DEFAULTS.items() if k != 'scale_interval_min'}
            (Path(temp) / s.FILE).write_text(json.dumps(old))
            settings, status = s.read_settings(temp)
            self.assertEqual((settings['scale_interval_min'], status), (5, 'custom'))
            for minutes in (1, 60):
                with self.subTest(minutes=minutes):
                    s.write_settings(temp, {**settings, 'scale_interval_min': minutes})
                    self.assertEqual(s.read_settings(temp)[0]['scale_interval_min'], minutes)
            for minutes in (0, 61, 1.5, True):
                with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                    s.write_settings(temp, {**settings, 'scale_interval_min': minutes})

    def test_defaults_custom_and_invalid_fall_back(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(s.read_settings(temp), (s.DEFAULTS, 'default'))
            custom = {**s.DEFAULTS, 'stop_loss_pct': 3.0, 'take_profit_all_pct': 3.0}
            s.write_settings(temp, custom)
            self.assertEqual(s.read_settings(temp), (custom, 'custom'))
            (Path(temp) / s.FILE).write_text(json.dumps({**custom, 'stop_loss_pct': 50}))
            self.assertEqual(s.read_settings(temp), (s.DEFAULTS, 'invalid'))
            (Path(temp) / s.FILE).write_text('broken')
            self.assertEqual(s.read_settings(temp)[1], 'invalid')

    def test_rejects_inconsistent_values(self):
        for bad in ({'take_profit_half_pct': 2.0}, {'add_step_pct': 5.1}, {'entry_confidence': 1.2}, {'sell_confidence': None}):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):
                    s.write_settings(temp, {**s.DEFAULTS, **bad})

    def test_slot_counts_minutes_and_add_size_are_whole_numbers(self):
        for key in ('ipo_daily_max', 'strength_slot_max', 'strength_slot_daily', 'ipo_time_stop_min', 'add_size_pct'):
            with self.subTest(key=key):
                self.assertTrue(s.problems({**s.DEFAULTS, key: s.DEFAULTS[key] + 0.5}))
                self.assertEqual(s.problems({**s.DEFAULTS, key: s.DEFAULTS[key] + 1}), [])

    def test_sizing_switches_and_swing_days_reject_fractions(self):
        """position_sizing tests the switches for truth (0.3 would mean on) and swing_max_days counts sessions."""
        for key, value in (('size_holding_on', 0.3), ('size_market_on', 0.3), ('regime_trim_on', 0.3), ('swing_max_days', 2.5)):
            with self.subTest(key=key):
                self.assertTrue(s.problems({**s.DEFAULTS, key: value}))
        self.assertEqual(s.problems({**s.DEFAULTS, 'size_market_on': 0, 'swing_max_days': 3}), [])

    def test_scale_step_and_stop_allow_independent_values_within_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            for step in (0.2, 1.0, 5.0):
                with self.subTest(step=step):
                    s.write_settings(temp, {**s.DEFAULTS, 'add_step_pct': step, 'stop_loss_pct': 0.5})
                    saved, status = s.read_settings(temp)
                    self.assertEqual((saved['add_step_pct'], saved['stop_loss_pct'], status), (step, 0.5, 'custom'))
            for step in (0.19, 5.01):
                with self.subTest(step=step), self.assertRaises(ValueError):
                    s.write_settings(temp, {**s.DEFAULTS, 'add_step_pct': step})

    def test_runner_applies_settings_to_exits_split_and_confidence(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(p, 'journal_event') as events:
            s.write_settings(temp, {**s.DEFAULTS, 'stop_loss_pct': 3.0, 'take_profit_all_pct': 4.0,
                                    'take_profit_half_pct': 1.5, 'add_step_pct': 1.0,
                                    'entry_confidence': 0.5, 'sell_confidence': 0.8})
            p.apply_settings(temp)
            try:
                self.assertFalse(p.price_exit(1000, 980))   # -2% no longer exits
                self.assertTrue(p.price_exit(1000, 970))    # -3% does
                self.assertFalse(p.price_exit(1000, 1030))
                self.assertTrue(p.price_exit(1000, 1040))
                plan = {'buys': 1, 'sells': 0, 'first': 1000, 'bought': 10, 'sold': 0, 'profit_sold': 0}
                self.assertIsNone(p.split_step(plan, 10, 1000, 1014))
                self.assertEqual(p.split_step(plan, 10, 1000, 1015), 'take_profit')
                jev = {'model': 'jev-1.14.0', 'chart_sha256': 'a' * 64, 'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': 0.5}}
                self.assertEqual(p.select_entry(None, None, jev, etf7=True, modes={'decision': False, 'jev': True})[2],
                                 'entry_conditions_met')
                self.assertEqual(events.call_args.args[1], 'strategy_settings')
            finally:
                with tempfile.TemporaryDirectory() as empty:
                    p.apply_settings(empty)  # restore defaults for other tests
            self.assertTrue(p.price_exit(1000, 980))


if __name__ == '__main__':
    unittest.main()
