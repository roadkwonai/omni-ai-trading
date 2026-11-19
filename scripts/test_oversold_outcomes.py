import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import oversold_outcomes as oo
from strategy_settings import DEFAULTS

KST = ZoneInfo('Asia/Seoul')


def day(d, o, h, lo, c):
    return (datetime.fromisoformat(f'{d}T09:00:00+09:00'), o, h, lo, c)


def m5(hhmm, o, h, lo, c):
    return (datetime.fromisoformat(f'2026-11-06T{hhmm}:00+09:00'), o, h, lo, c)


# prev close 100, low 90 (-10%), the fall stops for 4 bars (20 min / 5), then +1.5% is hit
BARS = [m5('09:00', 100, 100, 95, 95), m5('09:05', 95, 95, 90, 90.5)] + \
       [m5(t, 90.5, 91, 90.4, 90.8) for t in ('09:10', '09:15', '09:20', '09:25')] + [m5('09:30', 90.8, 92.5, 90.6, 92.3)]
DAILY = [day('2026-11-05', 100, 101, 99, 100), day('2026-11-06', 95, 95, 90, 92), day('2026-11-07', 92, 96, 91, 95)]
ATR = {datetime(2026, 11, 6).date(): 2.0}


def evaluate(code='005930', daily=DAILY, settings=DEFAULTS):
    return oo.evaluate_day('2026-11-06', code, daily, BARS, ATR, lambda d: -3.0, settings)


class OutcomeTests(unittest.TestCase):
    def test_market_data_finds_a_code_on_either_market(self):
        """Review 4 C10: one code->Yahoo rule (oversold_cases.yahoo_symbols); an unknown code is tried on .KS, then .KQ."""
        bars = {'900100.KQ': DAILY, '^IXIC': DAILY}
        with patch('oversold_research.fetch', lambda symbol, interval, rng: bars.get(symbol, [])):
            data, _ = oo.market_data(['900100'], '2026-11-06')
        self.assertEqual(data['900100'], (DAILY, DAILY))

    def test_rule_params_follow_the_settings(self):
        p = oo.rule_params(DEFAULTS, 'stock')
        self.assertEqual((p['x'], p['k'], p['n'], p['r'], p['hold']), (6.0, 1.5, 4, 1.0, True))
        p = oo.rule_params({**DEFAULTS, 'oversold_calm_min': 7, 'oversold_hold_days': 0}, 'etf')
        self.assertEqual((p['n'], p['hold']), (2, False))  # 7 minutes -> 2 five-minute bars

    def test_triggered_day_replays_the_rule(self):
        row = evaluate()
        self.assertEqual((row['status'], row['drop_pct'], row['need_pct'], row['atr_multiple']), ('triggered', -10.0, 6.0, 5.0))
        self.assertEqual(row['rule']['exit'], 'target')
        self.assertAlmostEqual(row['rule']['return_pct'], 1.35)  # +1.5% target minus 0.15% cost
        self.assertEqual((row['nasdaq_prev_pct'], row['next_close_pct'], row['gap_recovered_pct']), (-3.0, 3.26, 20.0))
        self.assertFalse(row['rule']['pending_next_day'])

    def test_near_miss_and_quiet_days(self):
        near = [DAILY[0], day('2026-11-06', 99, 99, 96, 97), DAILY[2]]  # -4% >= 60% of the 6% needed
        self.assertEqual(evaluate(daily=near)['status'], 'near')
        quiet = [DAILY[0], day('2026-11-06', 99, 99, 98, 98.5), DAILY[2]]
        self.assertIsNone(evaluate(daily=quiet))
        self.assertIsNone(oo.evaluate_day('2026-11-09', '005930', DAILY, BARS, ATR, lambda d: None, DEFAULTS))  # no bar that day

    def test_leveraged_etf_off_is_only_near(self):
        self.assertEqual(evaluate('122630')['status'], 'near')
        self.assertEqual(evaluate('122630', settings={**DEFAULTS, 'oversold_lev_on': 1})['status'], 'triggered')

    def test_held_trade_waits_for_the_next_close(self):
        stuck = [b if b[0].strftime('%H:%M') != '09:30' else m5('09:30', 90.8, 91.5, 90.6, 91.0) for b in BARS]
        row = oo.evaluate_day('2026-11-06', '005930', DAILY[:2], stuck, ATR, lambda d: None, DEFAULTS)
        self.assertTrue(row['rule']['pending_next_day'])
        self.assertIsNone(row['next_close_pct'])

    def test_summary_keeps_the_newest_record_per_day_and_skips_pending(self):
        win = {'kind': 'stock', 'status': 'triggered', 'rule': {'return_pct': 1.35, 'pending_next_day': False}, 'jev': {'bought': True}}
        loss = {**win, 'rule': {'return_pct': -0.8, 'pending_next_day': False}, 'jev': {}}
        pending = {**win, 'rule': {'return_pct': 0.2, 'pending_next_day': True}}
        records = [{'day': '2026-11-07', 'rows': [loss]}, {'day': '2026-11-06', 'rows': [win, pending]},
                   {'day': '2026-11-06', 'rows': [loss]}]  # older record of 11-06: ignored
        with patch('research_store.history', return_value=records):
            out = oo.summary(20)
        g = out['groups']['stock:triggered']
        self.assertEqual((out['sessions'], out['events']), (['2026-11-06', '2026-11-07'], 3))
        self.assertEqual((g['n'], g['simulated'], g['wins'], g['bought_jev'], g['worst_pct']), (3, 2, 1, 2, -0.8))
        self.assertAlmostEqual(g['mean_return_pct'], 0.28)


if __name__ == '__main__':
    unittest.main()
