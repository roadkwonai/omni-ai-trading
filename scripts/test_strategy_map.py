import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

import nh_paper_isolated as runner
import strategy_map as m

KST = ZoneInfo('Asia/Seoul')
AT = lambda h, mi, day=12: datetime(2026, 11, day, h, mi, tzinfo=KST)
SETTINGS = {'entry_confidence': 0.6, 'sell_confidence': 0.7, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0,
            'stop_loss_pct': 2.0, 'add_step_pct': 0.7, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5, 'overnight_max_manwon': 300}


def state(**extra):
    base = {'time': AT(10, 0).isoformat(), 'phase': m.phase(AT(10, 0)), 'engine': 'jev', 'auto': True, 'budget': {},
            'settings': SETTINGS, 'settings_status': 'default', 'modes': None, 'market_fresh': True,
            'indices': {'코스피': {'regime': '하락장', 'tone': '약세', 'change_pct': -1.2}},
            'holdings': {}, 'exit_plans': [], 'stop_file': False, 'services': {},
            'sources': [{'name': n, 'time': None, 'age_min': None, 'limit_min': 1, 'used_for': '', 'fresh': False}
                        for n in ('NH 시세·1분봉', '시장 상황(국면·수급·테마·해외)', '순위·관심종목')]}
    return {**base, **extra}


class StrategyMapTests(unittest.TestCase):
    def test_phase_boundaries_match_the_runner(self):
        self.assertEqual((m.ENTRY_CUTOFF, m.FLATTEN_AT), (runner.ENTRY_CUTOFF, runner.FLATTEN_AT))
        self.assertEqual([m.phase(AT(h, mi))[0] for h, mi in ((8, 59), (9, 0), (14, 29), (14, 30), (14, 40), (15, 0))],
                         ['pre_open', 'entry', 'entry', 'no_new_buys', 'flatten', 'closed'])  # orders from 09:00 (2026-11-19)
        self.assertEqual(m.phase(AT(10, 0, day=14))[0], 'closed_day')

    def test_route_follows_engine_switch_phase_and_holdings(self):
        self.assertEqual(m.active_route(state())[0][:2], ['nh', 'jev'])
        self.assertEqual(m.active_route(state(auto=False))[0], ['halt', 'entry'])
        self.assertEqual(m.active_route(state(phase=m.phase(AT(14, 45)), holdings={'069500': 3}))[0][0], 'quote')
        self.assertEqual(m.active_route(state(phase=m.phase(AT(20, 0))))[0], ['order', 'ledger'])

    def test_main_path_moves_forward_over_real_edges(self):
        for st in (state(), state(auto=False), state(phase=m.phase(AT(14, 45)), holdings={'1': 1})):
            diagram = m.spec(st)
            cols = {n['id']: n['col'] for n in diagram['nodes']}
            edges = {(e['from'], e['to']) for e in diagram['edges']}
            route = diagram['mainPath']
            for a, b in zip(route, route[1:]):
                self.assertIn((a, b), edges)
                self.assertLessEqual(cols[a], cols[b])

    def test_sideways_and_down_use_tighter_stop_without_widening_user_setting(self):
        for configured in (2.0, 0.5):
            st = state()
            st['settings']['stop_loss_pct'] = configured
            for row in m.regime_rows(st):
                expected = min(configured, 1.0) if row['국면'] in ('횡보장', '하락장') else configured
                self.assertIn(f"−{expected:g}%", row['청산'])

    def test_regime_rows_mark_the_current_row_and_mirror_the_runner_rule(self):
        rows = m.regime_rows(state())
        current = [r for r in rows if r['현재']]
        self.assertEqual(len(current), 1)
        self.assertEqual((current[0]['국면'], current[0]['추가 매수(롱)']), ('하락장', '중단'))
        self.assertEqual(m.averaging_down(state()), 'blocked')
        self.assertTrue(runner.averaging_down_blocked({'코스피': {'regime': '하락장', 'today_tone': '약세'}}, '069500', False))
        self.assertEqual(m.averaging_down(state(market_fresh=False)), 'unknown')
        self.assertFalse(any(r['현재'] for r in m.regime_rows(state(market_fresh=False))))

    def test_dot_fallback_highlights_the_route(self):
        text = m.dot(m.spec(state()))
        self.assertIn('"nh" -> "jev"', text)
        self.assertIn('penwidth=2.5', text)

    def test_premarket_context_is_shown_to_jev(self):
        diagram = m.spec(state())
        self.assertIn('오늘 장전 수집 자료', [node['label'] for node in diagram['nodes']])
        edges = {(edge['from'], edge['to']) for edge in diagram['edges']}
        self.assertIn(('premarket', 'jev'), edges)

    def test_the_retired_intraday_vibe_engine_is_not_drawn(self):
        diagram = m.spec(state())  # vibe became the long-term engine on 2026-11-17 (its own tab)
        self.assertNotIn('vibe', {node['id'] for node in diagram['nodes']})
        self.assertFalse([e for e in diagram['edges'] if 'vibe' in (e['from'], e['to'])])

    def test_diagram_uses_saved_values_and_does_not_claim_execution(self):
        st = state(limits={'order_max_krw': 10_000_000, 'daily_buy_krw': 1_000_000_000,
                           'daily_new_symbols': 10, 'daily_loss_krw': 5_000_000},
                   live_switches=[True, False], premarket_available=True, premarket_time=AT(8, 40).isoformat())
        diagram = m.spec(st)
        nodes = {node['id']: node for node in diagram['nodes']}
        self.assertIn('1,000만', nodes['gate']['sublabel'])
        self.assertIn('100,000만', nodes['gate']['sublabel'])
        self.assertIn('ON 1 · OFF 1', nodes['live']['tag'])
        self.assertIn('08:40', nodes['premarket']['tag'])
        self.assertIn('입력 별도 확인', nodes['premarket']['tag'])
        self.assertIn('예상 경로', diagram['meta']['title'])
        self.assertIn('입력 미확인', {n['id']: n for n in m.spec(state())['nodes']}['premarket']['tag'])

    def test_opening_observation_never_shows_entry_route(self):
        with patch('session_times.SESSION_OPEN', '09:30'):  # observation remains only before a later 장 시작
            route, description = m.active_route(state(phase=m.phase(AT(9, 0))))
        self.assertNotIn('order', route)
        self.assertIn('새 주문 없음', description)

    def test_state_reads_account_limits_and_live_switches(self):
        records = {'paper-shared': {'engine': 'jev', 'auto': True, 'budget': {},
                                    'limits': {'order_max_krw': 10_000_000}},
                   'live-a': {'kind': 'live', 'live_orders': True},
                   'live-b': {'kind': 'live', 'invalid': True, 'live_orders': False}}
        with tempfile.TemporaryDirectory() as directory, patch('accounts.load', return_value=records):
            actual = m.state(directory, AT(10, 0))
        self.assertEqual(actual['limits']['order_max_krw'], 10_000_000)
        self.assertEqual(actual['live_switches'], [True, None])

    def test_intraday_sources_are_idle_outside_the_session_not_stale(self):
        records = {'paper-shared': {'engine': 'jev', 'auto': True, 'budget': {}}}
        with tempfile.TemporaryDirectory() as directory, patch('accounts.load', return_value=records):
            before, during = m.state(directory, AT(8, 56)), m.state(directory, AT(10, 0))
        self.assertNotIn('vibe-trading 분석', [src['name'] for src in during['sources']])
        self.assertTrue(all(src['idle'] for src in before['sources']))  # 08:56: nothing runs yet
        self.assertFalse(any(src['idle'] for src in during['sources']))  # in the session a stale source is a warning

    @unittest.skipUnless(shutil.which('node') and m.ARCHIFY.exists(), 'Archify not installed')
    def test_archify_accepts_every_route_and_caches_by_content(self):
        with tempfile.TemporaryDirectory() as d:
            for st in (state(), state(auto=False), state(phase=m.phase(AT(20, 0)))):
                first = m.render(m.spec(st), d)
                self.assertEqual(m.render(m.spec(st), d), first)
                self.assertTrue(first.read_text().lstrip().lower().startswith('<!doctype html'))

    def test_diagram_content_does_not_change_with_the_clock(self):
        # D9: an 'N분 전' tag made every minute a new digest and a new ~770 KB HTML under the runtime folder
        def aged(minutes):
            return state(sources=[{'name': n, 'time': AT(9, 50).isoformat(), 'age_min': minutes, 'limit_min': 45,
                                   'used_for': '', 'fresh': True, 'idle': False}
                                  for n in ('NH 시세·1분봉', '시장 상황(국면·수급·테마·해외)', '순위·관심종목')])
        self.assertEqual(json.dumps(m.spec(aged(3)), sort_keys=True), json.dumps(m.spec(aged(4)), sort_keys=True))

    @unittest.skipUnless(shutil.which('node') and m.ARCHIFY.exists(), 'Archify not installed')
    def test_concurrent_renders_of_one_diagram_all_succeed_and_leave_no_temp_folders(self):
        # D9: two sessions drawing the same new state made Archify publish over the other's file; the losers failed and
        # an interrupted publish left .archify-remove-* folders behind
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            diagram = m.spec(state())
            with ThreadPoolExecutor(max_workers=4) as pool:
                paths = list(pool.map(lambda _: m.render(diagram, d), range(4)))
            self.assertEqual(len(set(paths)), 1)
            self.assertEqual(sorted(p.suffix for p in Path(d).iterdir()), ['.html', '.json'])

    def test_the_regime_table_says_what_the_runner_does_for_longs_and_inverses(self):
        # 11-18 review: the table hand-copied the rules: inverse stops long-only (−2% in 상승장, −1% in 하락장), inverse adds
        # 허용 where inverse_gate blocks them, "인버스는 추세형" where the runner uses inverse_quick off a crash day
        from etf_universe import SYMBOLS
        stop = SETTINGS['stop_loss_pct']
        for row in m.regime_rows(state()):
            for tone in row['오늘 흐름'].split('·'):
                view = {'코스피': {'regime': row['국면'], 'today_tone': tone}}
                blocked = runner.averaging_down_blocked(view, SYMBOLS['K200'], False, '코스피')
                self.assertEqual(row['추가 매수(롱)'], '중단' if blocked else '허용', (row['국면'], tone))
            inverse = min(stop, 1.0) if row['국면'] in runner.tightening_regimes(SYMBOLS['INV']) else stop
            self.assertIn(f'손절 −{inverse:g}%', row['인버스 청산'], row['국면'])
            self.assertIn(f"+{runner.INVERSE['quick_tp_pct']:g}% 전량", row['인버스 청산'])
            gate = runner.inverse_gate({'코스피': {'regime': row['국면']}}, '코스피')
            self.assertEqual(row['추가 매수(인버스)'] == '중단', gate == 'inverse_regime_not_falling', row['국면'])
            self.assertNotIn('인버스', row['청산'])  # the long exit alone; the inverse one has its own field

    def test_averaging_down_follows_the_runner_on_every_tone(self):
        for tone, expected in (('약세', 'blocked'), ('혼조', 'blocked'), ('판단 불가', 'blocked'), ('강세', 'allowed')):
            st = state(indices={'코스피': {'regime': '하락장', 'tone': tone, 'change_pct': -1.0}})
            self.assertEqual(m.averaging_down(st), expected, tone)
        self.assertEqual(m.averaging_down(state(indices={'코스피': {'regime': '상승장', 'tone': '약세', 'change_pct': -1.0}})), 'allowed')

    def test_the_last_event_is_found_behind_a_long_last_line(self):
        # a market_context line can run to 21 KB; a 4 KB tail held no whole line and the NH source looked missing
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'events.jsonl'
            first = {'time': AT(10, 0).isoformat(), 'event': 'v2_cycle'}
            long = {'time': AT(10, 1).isoformat(), 'event': 'market_context', 'blob': 'x' * 20_000}
            path.write_text(json.dumps(first) + '\n' + json.dumps(long) + '\n' + '{"time": "2026-11-12T10:02')  # still being written
            self.assertEqual(m._last_event(d), long)
            path.write_text('')
            self.assertIsNone(m._last_event(d))

if __name__ == '__main__':
    unittest.main()
