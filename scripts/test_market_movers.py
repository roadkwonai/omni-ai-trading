import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import market_movers as m

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))


class Reader:
    def __init__(self, quotes): self.quotes = quotes
    def call_tool(self, tool, params):
        q = self.quotes.get(params.get('stock_code'))
        return {'status': 'ok', 'data': {'Output_0': q}} if q else {'status': 'error'}


def quote(code, price, prev, vol, value):
    return {'iem_cd': code, 'stck_prpr': price, 'stck_prdy_clpr': prev, 'acml_vol': vol, 'acml_tr_pbmn': value}


class MoversTests(unittest.TestCase):
    def test_signed_change_rankings_and_candidate_reasons(self):
        quotes = {'000001': quote('000001', 110, 100, 10, 500), '000002': quote('000002', 90, 100, 50, 100),
                  '000003': quote('000003', 100, 100, 5, 900), '000004': quote('000004', 0, 100, 5, 1)}
        universe = {c: {'name': '*N' + c, 'index': 'KOSPI200'} for c in [*quotes, '000005']}
        rows = m.scan(Reader(quotes), universe)
        self.assertEqual({r['code']: r['change_pct'] for r in rows}, {'000001': 10.0, '000002': -10.0, '000003': 0.0})
        rankings, candidates, _ = m.rank(rows)
        self.assertEqual(rankings['rise'][0], '000001')
        self.assertEqual(rankings['fall'][0], '000002')
        self.assertEqual(rankings['trading_value'][0], '000003')
        self.assertIn('급하락', candidates['000002']['reasons'])
        self.assertEqual(candidates['000001']['name'], 'N000001')

    def test_context_watch_refreshes_until_stopped(self):
        from unittest.mock import patch
        from threading import Event
        stop = Event()
        with patch.object(m, 'collect_context', side_effect=stop.set) as collect:
            m.context_watch(stop, 300)
        collect.assert_called_once()

    def test_research_snapshot_leaves_out_the_index_history(self):
        """Review 4 C12: every 5-minute research snapshot repeated each index's 120-day close history."""
        import market_context
        payload = {'time': '2026-11-19T10:00:00+09:00',
                   'indices': {'KOSPI': {'name': '코스피', 'close': 5000.0, 'history': [['2026-11-18', 5064.24]]}}}
        written, recorded = [], []
        with patch.object(market_context, 'collect', return_value=payload), patch.object(market_context, 'write', side_effect=written.append), \
                patch.object(m.research_store, 'record', side_effect=lambda kind, p: recorded.append((kind, p))):
            m.collect_context()
        self.assertEqual(recorded, [('market_context', {'time': '2026-11-19T10:00:00+09:00',
                                                        'indices': {'KOSPI': {'name': '코스피', 'close': 5000.0}}})])
        self.assertEqual(written[0]['indices']['KOSPI']['history'], [['2026-11-18', 5064.24]])  # market-context.json keeps it

    def test_pre_open_start_snapshots_then_waits_for_the_open(self):
        from unittest.mock import patch
        slept = []
        early = datetime(2026, 11, 12, 8, 50, tzinfo=ZoneInfo('Asia/Seoul'))
        with patch.object(m, 'collect_context') as collect, patch.object(m, 'datetime') as clock, \
                patch.object(m, 'refresh_cases'):  # no Yahoo reads in tests (covered below)
            clock.now.return_value = early
            self.assertTrue(m.wait_for_open(early, sleep=slept.append))
            collect.assert_called_once()
            self.assertEqual(slept, [600.0])
            self.assertFalse(m.wait_for_open(early.replace(hour=9, minute=5), sleep=slept.append))
            self.assertFalse(m.wait_for_open(early.replace(day=14), sleep=slept.append))  # Saturday
            collect.assert_called_once()

    def test_pre_open_case_series_refresh_never_delays_the_open(self):
        """Review 4 C1: the case library's daily series are rewritten before the open, in the background."""
        import threading
        started, release, slept = threading.Event(), threading.Event(), []
        early = datetime(2026, 11, 12, 8, 50, tzinfo=ZoneInfo('Asia/Seoul'))
        with patch.object(m, 'collect_context'), patch.object(m, 'datetime') as clock, \
                patch('oversold_cases.refresh_series', side_effect=lambda: (started.set(), release.wait(5))[1]):
            clock.now.return_value = early
            self.assertTrue(m.wait_for_open(early, sleep=slept.append))
            self.assertTrue(started.wait(5))
            self.assertEqual(slept, [600.0])  # waited for the open while the refresh was still running
            release.set()


if __name__ == '__main__':
    unittest.main()


class KiwoomPreselectTests(unittest.TestCase):
    def test_preselect_keeps_universe_codes_per_ranking_in_kiwoom_order(self):
        import kiwoom_data
        import market_movers as mm
        universe = {f'{i:06d}': {'name': str(i), 'index': 'KOSPI200'} for i in range(100)}
        pages = {'ka10032': [{'stk_cd': '999999'}, {'stk_cd': '000001_AL'}, {'stk_cd': '000002'}],
                 'ka10030': [{'stk_cd': '000002'}, {'stk_cd': '000003'}]}
        def call(api_id, body, key, max_pages=1):
            if api_id == 'ka10027':
                return [{'stk_cd': '000004'}] if body['sort_tp'] == '1' else [{'stk_cd': '000005'}, {'stk_cd': '000001'}]
            return pages[api_id]
        with patch.object(kiwoom_data, 'call', side_effect=call):
            self.assertEqual(mm.preselect(universe), ['000001', '000002', '000003', '000004', '000005'])

    def test_targets_fall_back_to_the_full_scan(self):
        import kiwoom_data
        import market_movers as mm
        universe = {f'{i:06d}': {} for i in range(100)}
        logs = []
        self.assertEqual(mm.targets(universe, logs.append), (universe, 'nh_scan'))  # no Kiwoom key in tests
        with patch.object(kiwoom_data, 'configured', return_value=True):
            with patch.object(mm, 'preselect', return_value=['000001'] * 5):
                self.assertEqual(mm.targets(universe, logs.append)[1], 'nh_scan')  # too few: scan everything
            with patch.object(mm, 'preselect', side_effect=OSError):
                self.assertEqual(mm.targets(universe, logs.append)[1], 'nh_scan')
            picked = [f'{i:06d}' for i in range(mm.PRESELECT * 2)]
            with patch.object(mm, 'preselect', return_value=picked):
                quoted, source = mm.targets(universe, logs.append)
        self.assertEqual((source, list(quoted)), ('kiwoom_preselect', picked))
        self.assertEqual(len(logs), 2)
