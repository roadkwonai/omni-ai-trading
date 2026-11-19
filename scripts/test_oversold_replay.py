import unittest
from datetime import datetime, timedelta

import oversold_replay as rp


def bars(rows):
    """[(o, h, l, c)] -> daily bars on consecutive days."""
    start = datetime(2026, 11, 2)
    return [(start + timedelta(days=n), *r) for n, r in enumerate(rows)]


# day 0 prev close 100; day 1 the fall (low 90, close 92); then a rebound
UP = bars([(100, 100, 100, 100), (95, 95, 90, 92), (92, 95, 91, 94), (94, 97, 93, 96), (96, 99, 95, 98), (98, 101, 97, 100), (100, 102, 99, 101)])
# the same fall, then it keeps falling
DOWN = bars([(100, 100, 100, 100), (95, 95, 90, 92), (91, 92, 88, 89), (89, 90, 85, 86), (86, 87, 84, 85), (85, 86, 83, 84), (84, 85, 82, 83)])


class ReplayTests(unittest.TestCase):
    def test_holding_strategies_on_a_rebound(self):
        r = rp.replay(UP, 1, 'etf', atr_pct=2.0)
        self.assertEqual((r['close_1d']['ret'], r['close_1d']['days']), (2.07, 1))  # 94/92 - 0.1% cost
        self.assertEqual(r['close_5d']['ret'], 9.68)  # 101/92
        self.assertEqual((r['close_5d_tp8_low5']['exit'], r['close_5d_tp8_low5']['days'], r['close_5d_tp8_low5']['ret']),
                         ('target', 4, 7.9))  # 92 * 1.08 = 99.36 reached on day 5 (high 101)
        self.assertEqual((r['close_gap_low5']['exit'], r['close_gap_low5']['ret']), ('target', 8.6))  # back to 100
        self.assertEqual(r['next_open_5d']['ret'], 9.68)  # open 92 next day, out at 101
        self.assertEqual(r['close_5d']['mae'], -1.09)  # low 91 while held

    def test_stops_on_a_further_fall(self):
        r = rp.replay(DOWN, 1, 'stock', atr_pct=3.0)
        low5 = r['close_5d_low5']  # stop 85.5: day 3 low 85
        self.assertEqual((low5['exit'], low5['days'], low5['ret']), ('stop', 2, -7.32))
        atr = r['close_5d_atr']  # stop 87.3: day 2 low 88, day 3 low 85, opened at 89
        self.assertEqual((atr['exit'], atr['days'], atr['ret']), ('stop', 2, -5.36))
        self.assertEqual(r['close_5d']['exit'], 'time')
        gap_open = rp.run(bars([(100, 100, 100, 100), (95, 95, 90, 92), (80, 81, 79, 80)] + [(80, 80, 80, 80)] * 4), 1, 'stock', name='close_5d_low5')
        self.assertEqual(gap_open['ret'], -13.29)  # opened under the stop: filled at the open 80

    def test_pending_until_enough_days(self):
        self.assertTrue(rp.run(UP[:4], 1, 'etf', name='close_5d')['pending'])
        self.assertFalse(rp.run(UP[:4], 1, 'etf', name='close_1d').get('pending'))

    def test_table_counts_a_crash_day_once(self):
        a = {'day': '2026-11-03', 'replay': {'close_5d': {'ret': 10.0}}}
        b = {'day': '2026-11-03', 'replay': {'close_5d': {'ret': 0.0}}}
        c = {'day': '2026-11-04', 'replay': {'close_5d': {'ret': -2.0}}, 'intraday': {'rule': {'return_pct': 1.35, 'adverse_pct': -1.0}}}
        rows = {r['strategy']: r for r in rp.table([a, b, c])}
        self.assertEqual((rows['close_5d']['n'], rows['close_5d']['days'], rows['close_5d']['day_mean'], rows['close_5d']['mean']),
                         (3, 2, 1.5, 2.67))
        self.assertEqual((rows['now_rule']['n'], rows['now_rule']['mae']), (1, -1.0))

    def test_gate_v2_needs_a_market_crash_without_a_rising_nasdaq(self):
        kospi = {'kind': 'kr_index', 'code': '^KS11', 'day': 'd1', 'drop_pct': -8.8, 'prior_5d': 0.0, 'nasdaq_prev': -4.2}
        kosdaq = {'kind': 'kr_index', 'code': '^KQ11', 'day': 'd1', 'drop_pct': -3.0, 'nasdaq_prev': -4.2}
        markets = rp.market_days([kospi, kosdaq, {**kospi, 'day': 'd2', 'drop_pct': -5.5, 'prior_5d': -6.0, 'nasdaq_prev': 0.9}])
        self.assertEqual(markets['d1'], {'kospi': kospi, 'kosdaq': kosdaq})
        stock = {'kind': 'stock', 'code': '005930', 'day': 'd1', 'drop_pct': -10.0}  # KOSPI stock
        self.assertTrue(rp.gate_v2(stock, markets))
        self.assertTrue(rp.gate_v2(kospi, markets))
        self.assertFalse(rp.gate_v2({**stock, 'drop_pct': -13.0}, markets))  # 4.2%p worse than its market
        self.assertFalse(rp.gate_v2({**stock, 'code': '196170'}, markets))  # a KOSDAQ stock: KOSDAQ only fell 3%
        self.assertFalse(rp.gate_v2({**stock, 'day': 'd2'}, markets))  # Nasdaq rose the night before
        self.assertFalse(rp.gate_v2(stock, {'d1': {'kospi': {**kospi, 'nasdaq_prev': None}}}))  # unknown night: like live
        self.assertTrue(rp.gate_v2({**stock, 'drop_pct': -7.0}, {'d1': {'kospi': {**kospi, 'drop_pct': -5.2, 'prior_5d': -7.0}}}))  # capitulation
        self.assertTrue(rp.gate_v2({'kind': 'lev', 'code': '122630', 'day': 'd1', 'drop_pct': -17.0}, markets))  # vs 2x market
        self.assertIsNone(rp.gate_v2({'kind': 'crypto', 'code': 'BTC-USD', 'day': 'd1'}, markets))
        self.assertFalse(rp.gate_v2({**stock, 'day': 'd9'}, markets))

    def test_gate_v2_reads_the_strategy_settings(self):
        """Review 4 C11: the replay hard-coded 8/5/5/4 while the live gate reads oversold_v2_* (run_v2 takes them too)."""
        kospi = {'kind': 'kr_index', 'code': '^KS11', 'day': 'd1', 'drop_pct': -8.8, 'prior_5d': 0.0, 'nasdaq_prev': -4.2}
        markets = rp.market_days([kospi])
        stock = {'kind': 'stock', 'code': '005930', 'day': 'd1', 'drop_pct': -13.0}  # 4.2%p worse than its market
        self.assertFalse(rp.gate_v2(stock, markets))  # the defaults: within 4%p
        self.assertTrue(rp.gate_v2(stock, markets, {'oversold_v2_rel_max_pct': 5.0}))
        self.assertFalse(rp.gate_v2(kospi, markets, {'oversold_v2_index_drop_pct': 10.0}))
        self.assertTrue(rp.market_crash({**kospi, 'drop_pct': -4.0, 'prior_5d': -3.5},
                                        {'oversold_v2_capit_drop_pct': 3.0, 'oversold_v2_prior5_pct': 3.0}))
        rows = rp.v2_samples([{**kospi, 'name': '코스피', 'day': '2026-11-16'}], since='2026-11-14',
                             settings={'oversold_v2_index_drop_pct': 10.0})
        self.assertFalse(rows[0]['passed'])

    def test_v2_samples_count_from_the_start_day(self):
        kospi = {'kind': 'kr_index', 'code': '^KS11', 'name': '코스피', 'day': '2026-11-16', 'drop_pct': -8.5, 'nasdaq_prev': -1.0,
                 'replay': {'close_5d': {'ret': 4.0}}}
        calm = {**kospi, 'day': '2026-11-17', 'drop_pct': -3.5, 'nasdaq_prev': 0.5, 'replay': {}}
        old = {**kospi, 'day': '2026-11-06'}
        stock = {'kind': 'stock', 'code': '005930', 'day': '2026-11-16', 'drop_pct': -9.0, 'replay': {'close_5d': {'ret': 6.0}}}
        rows = rp.v2_samples([kospi, calm, old, stock], since='2026-11-14')
        self.assertEqual([(r['day'], r['passed'], r['index_5d'], r['stocks_5d']) for r in rows],
                         [('2026-11-17', False, None, None), ('2026-11-16', True, 4.0, 6.0)])

    def test_v2_plan_adds_lower_then_sells_half_and_the_rest(self):
        # day 1 close 92 (stop 92 x 0.98 = 90.16), day 2 low 90.5 closes lower at 91 (second half), average 91.5:
        # half at 98.82, the rest at 105.225
        rows = bars([(100, 100, 100, 100), (95, 95, 90, 92), (91, 92, 90.5, 91), (91, 99, 90.5, 98), (98, 106, 97, 105), (105, 106, 104, 105)])
        r = rp.run_v2(rows, 1, 'etf')
        self.assertTrue(r['added'])
        self.assertEqual((r['exit'], r['days']), ('target', 3))
        self.assertAlmostEqual(r['ret'], ((98.82 + 105.225) / 2 / 91.5 - 1) * 100 - 0.1, places=1)
        flat = bars([(100, 100, 100, 100), (95, 95, 90, 92), (93, 94, 92, 93), (93, 94, 92, 93), (93, 94, 92, 94)])
        r = rp.run_v2(flat, 1, 'etf')
        self.assertEqual((r['added'], r['exit'], r['days'], r['ret']), (False, 'time', 3, 2.07))  # half only, out at 94
        crash = bars([(100, 100, 100, 100), (95, 95, 90, 92), (80, 81, 70, 75)] + [(75, 75, 75, 75)] * 3)
        self.assertEqual(rp.run_v2(crash, 1, 'etf')['exit'], 'stop')  # under the average 92 x 0.98
        self.assertIn('v2_plan', rp.replay(UP, 1, 'etf'))


if __name__ == '__main__':
    unittest.main()
