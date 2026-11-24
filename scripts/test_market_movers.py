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

    def test_the_index_series_are_completed_in_the_background_and_a_failure_is_only_printed(self):
        with patch('oversold_cases.fill_index_series', return_value=['^KS11']) as fill, patch('builtins.print') as out:
            m.fill_index(datetime(2026, 11, 24, 10, tzinfo=ZoneInfo('Asia/Seoul')))
            fill.assert_called_once()
            self.assertIn('^KS11', out.call_args.args[0])
        with patch('oversold_cases.fill_index_series', side_effect=OSError), patch('builtins.print') as out:
            m.fill_index(datetime(2026, 11, 24, 10, tzinfo=ZoneInfo('Asia/Seoul')))
            self.assertIn('OSError', out.call_args.args[0])


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


def test_the_scan_paces_its_quotes_and_asks_again_after_a_rate_limit():
    """11-19: 219 of 9,037 quotes failed (IGW42902, NH's 5 calls a second per app key): the 60-stock burst every five
    minutes also refused 110 of the 144 runner, mirror and dashboard calls refused that day within 3 s of it, and a refused
    stock silently left the rankings."""
    clock = [0.0]
    starts = []

    class Limited(Reader):
        refused = set()

        def call_tool(self, tool, params):
            starts.append(clock[0])
            clock[0] += 0.1  # a call's own time
            code = params['stock_code']
            if code == '000002' and code not in self.refused:
                self.refused.add(code)
                return {'status': 'error', 'error': 'tool error: rate_limit IGW42902'}
            return super().call_tool(tool, params)

    def sleep(seconds):
        clock[0] += seconds
    quotes = {c: quote(c, 110, 100, 10, 500) for c in ('000001', '000002', '000003')}
    rows = m.scan(Limited(quotes), {c: {'name': c, 'index': 'KOSPI200'} for c in quotes}, gap=m.SCAN_GAP,
                  clock=lambda: clock[0], sleep=sleep)
    assert [r['code'] for r in rows] == ['000001', '000002', '000003']  # the refused stock is asked again, not dropped
    assert all(b - a >= m.SCAN_GAP - 1e-9 for a, b in zip(starts, starts[1:]))  # never faster than SCAN_GAP
    assert len(starts) == 4 and m.SCAN_GAP >= 0.5  # 2 a second at most: the rest of NH's 5 is the runners'


# 주도 테마 동반주 (user 2026-11-19, docs/theme-slot-plan-2026-11-19.md)
GROUPS = [{'group': '엔터·게임', 'change_pct': 5.0}, {'group': '화장품·소비', 'change_pct': 2.3}, {'group': '가상자산', 'change_pct': 1.6},
          {'group': '로봇', 'change_pct': 2.0}]
MEMBERS = {'엔터·게임': ['035760'], '화장품·소비': ['090430', '051900', '008770', '999999'], '가상자산': ['111111', '222222'],
           '로봇': ['108490']}
FIGURES = {'035760': (24.0, 400e8), '090430': (6.0, 300e8), '051900': (4.0, 220e8), '008770': (10.0, 250e8), '999999': (9.0, 900e8),
           '111111': (30.0, 99e8), '222222': (1.0, 80e8), '108490': (6.0, 600e8)}
UNIVERSE = {c: {'name': c, 'index': 'KOSPI200'} for c in ('035760', '090430', '051900', '008770', '111111', '222222', '108490')}
asked = []


def figures(codes, groups):
    asked.append(list(codes))
    return {c: FIGURES[c] for c in codes}


def quoted(codes):
    return [{'code': c, 'name': c, 'index': 'KOSPI200', 'price': 1000, 'change_pct': 5.0, 'volume': 10, 'value_mil': 1} for c in codes]


def test_a_theme_slot_adds_the_busiest_riser_of_each_strong_group_not_yet_among_the_candidates():
    asked.clear()
    candidates = {'035760': {'code': '035760'}}  # 엔터·게임 is already seen through CJ ENM
    picks = m.theme_picks(GROUPS, UNIVERSE, candidates, MEMBERS.get, figures, quoted)
    # 화장품·소비: 090430 has the most trading value of its universe risers (999999 is outside the universe);
    # 가상자산: 111111 is up 30%, past the 25% cap, 222222 under +2%: no pick; only the top three groups are looked at
    assert [(p['code'], p['theme_group']) for p in picks] == [('090430', '화장품·소비')]
    assert picks[0]['reasons'] == ['주도 테마 동반주 (화장품·소비)'] and picks[0]['price'] == 1000  # NH figures
    assert len(asked) == 1 and set(asked[0]) == {'090430', '051900', '008770', '111111', '222222'}  # one figures call


def test_at_most_two_slots_and_a_falling_group_or_a_failed_lookup_gives_none():
    groups = [{'group': '화장품·소비', 'change_pct': 2.3}, {'group': '로봇', 'change_pct': 1.0}, {'group': '엔터·게임', 'change_pct': -0.5}]
    assert [p['code'] for p in m.theme_picks(groups, UNIVERSE, {}, MEMBERS.get, figures, quoted)] == ['090430', '108490']

    def broken(*a):
        raise OSError('down')
    assert m.theme_picks(groups, UNIVERSE, {}, broken, figures, quoted) == []
    assert m.theme_picks(groups, UNIVERSE, {}, MEMBERS.get, broken, quoted) == []
    assert m.theme_picks(groups, UNIVERSE, {}, MEMBERS.get, figures, lambda codes: []) == []  # NH quote failed


def test_theme_figures_fall_back_to_naver_when_kiwoom_fails(monkeypatch):
    import theme_data
    monkeypatch.setattr(theme_data, 'kiwoom_quotes', lambda codes: (_ for _ in ()).throw(OSError('kiwoom')))
    monkeypatch.setattr(theme_data, 'naver_members', lambda group, universe: ({'090430': {'change_pct': 6.0, 'value_krw': 3e10}}, 9))
    assert m.theme_figures(UNIVERSE)(['090430'], ['화장품·소비']) == {'090430': (6.0, 3e10)}
    monkeypatch.setattr(theme_data, 'kiwoom_quotes', lambda codes: ({'090430': {'change_pct': 7.0, 'value_krw': 4e10}}, 1))
    assert m.theme_figures(UNIVERSE)(['090430'], ['화장품·소비']) == {'090430': (7.0, 4e10)}


def test_theme_picks_are_kept_by_day_with_their_first_time(tmp_path):
    path = tmp_path / 'theme-picks.json'
    first, later = datetime(2026, 11, 19, 10, 16, 40, tzinfo=ZoneInfo('Asia/Seoul')), datetime(2026, 11, 19, 10, 21, 5, tzinfo=ZoneInfo('Asia/Seoul'))
    m.note_theme_picks(path, first, [{'code': '090430', 'theme_group': '화장품·소비'}])
    m.note_theme_picks(path, later, [{'code': '090430', 'theme_group': '화장품·소비'}, {'code': '008770', 'theme_group': '화장품·소비'}])
    import json
    data = json.loads(path.read_text())
    assert data['2026-11-19'] == {'090430': {'group': '화장품·소비', 'first': '2026-11-19T10:16:40+09:00'},
                                  '008770': {'group': '화장품·소비', 'first': '2026-11-19T10:21:05+09:00'}}  # to the second


def test_theme_quotes_keep_the_scan_pace():
    """11-19 review: the theme slot's NH quotes went out right after the paced scan (shared key, 5 calls a second)."""
    slept = []
    quote = m.theme_quote(Reader({'090430': quote_row('090430')}), {'090430': {'name': 'x', 'index': 'KOSPI200'}}, sleep=slept.append)
    assert quote(['090430'])[0]['code'] == '090430' and slept == [m.SCAN_GAP]


def quote_row(code):
    return quote(code, 1000, 950, 10, 500)


def test_a_failed_quote_of_the_busiest_riser_tries_the_next_one():
    # r5: one failed NH quote left the whole group's slot empty for the round
    groups = [{'group': '화장품·소비', 'change_pct': 2.3}]
    tried = []

    def flaky(codes):
        tried.extend(codes)
        return [] if codes == ['090430'] else quoted(codes)
    picks = m.theme_picks(groups, UNIVERSE, {}, MEMBERS.get, figures, flaky)
    assert tried[0] == '090430' and len(picks) == 1 and picks[0]['code'] != '090430'


def test_kiwoom_scan_gives_the_nh_scan_row_shape_in_one_call_per_hundred(monkeypatch):
    # 2026-11-20 NH call plan P2: the ranking figures from one Kiwoom ka10095 call instead of ~65 NH quotes
    import kiwoom_data
    asked = []
    answer = [{'stk_cd': '005930', 'cur_prc': '-186900', 'base_pric': '196900', 'trde_qty': '4352032', 'trde_prica': '814604'},
              {'stk_cd': '090430_AL', 'cur_prc': '+120000', 'base_pric': '110000', 'trde_qty': '1000', 'trde_prica': '120'},
              {'stk_cd': '000660', 'cur_prc': '0', 'base_pric': '0', 'trde_qty': '0', 'trde_prica': '0'}]  # halted: dropped
    monkeypatch.setattr(kiwoom_data, 'call', lambda api, body, key, max_pages=1: asked.append((api, body['stk_cd'])) or answer)
    universe = {'005930': {'name': '삼성전자', 'index': 'KOSPI200'}, '090430': {'name': '*아모레퍼시픽', 'index': 'KOSPI200'},
                '000660': {'name': 'SK하이닉스', 'index': 'KOSPI200'}}
    rows = m.kiwoom_scan(universe)
    assert asked == [('ka10095', '005930|090430|000660')]
    assert rows == [{'code': '005930', 'name': '삼성전자', 'index': 'KOSPI200', 'price': 186900, 'change_pct': -5.08,
                     'volume': 4352032, 'value_mil': 814604},
                    {'code': '090430', 'name': '아모레퍼시픽', 'index': 'KOSPI200', 'price': 120000, 'change_pct': 9.09,
                     'volume': 1000, 'value_mil': 120}]


def test_quote_rows_use_kiwoom_and_fall_back_to_nh_when_it_fails_or_prices_too_few(monkeypatch):
    quoted = {f'00000{i}': {'name': 'n', 'index': 'KOSPI200'} for i in range(10)}
    nh = lambda read, q, gap=0: [{'code': c} for c in q]  # noqa: E731
    monkeypatch.setattr(m, 'scan', nh)
    monkeypatch.setattr(m, 'kiwoom_scan', lambda q: [{'code': c} for c in q])
    assert m.quote_rows(None, quoted, lambda line: None)[1] == 'kiwoom'
    monkeypatch.setattr(m, 'kiwoom_scan', lambda q: [{'code': c} for c in list(q)[:8]])  # 80%: too few
    assert m.quote_rows(None, quoted, lambda line: None)[1] == 'nh'
    monkeypatch.setattr(m, 'kiwoom_scan', lambda q: (_ for _ in ()).throw(OSError('down')))
    rows, source = m.quote_rows(None, quoted, lambda line: None)
    assert source == 'nh' and len(rows) == 10


def test_kiwoom_ranked_candidates_are_quoted_again_from_nh_and_the_rest_marked(monkeypatch):
    universe = {c: {'name': c, 'index': 'KOSPI200'} for c in ('A00001', 'A00002', 'A00003')}
    kw = lambda c: {'code': c, 'name': c, 'index': 'KOSPI200', 'price': 99, 'change_pct': 1.0, 'volume': 1, 'value_mil': 1}  # noqa: E731
    by_code = {c: kw(c) for c in universe}
    candidates = {'A00001': {**kw('A00001'), 'reasons': ['급상승']}, 'A00002': {**kw('A00002'), 'reasons': ['거래대금 상위']}}
    monkeypatch.setattr(m, 'scan', lambda read, q, gap=0: [{**kw('A00001'), 'price': 100}])  # NH missed A00002
    m.nh_candidates(None, universe, candidates, by_code)
    assert by_code['A00001']['price'] == 100 and 'source' not in by_code['A00001']
    assert candidates['A00001'] == {**kw('A00001'), 'price': 100, 'reasons': ['급상승']}
    assert by_code['A00003']['source'] == 'kiwoom' and candidates['A00002']['source'] == 'kiwoom'
