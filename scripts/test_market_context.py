import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import market_context as m

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo('Asia/Seoul'))


class RegimeTests(unittest.TestCase):
    def test_up_down_and_sideways(self):
        self.assertEqual(m.regime([100 + i for i in range(80)])['label'], '상승장')
        down = m.regime([200 - i for i in range(80)])
        self.assertEqual((down['label'], down['score']), ('하락장', -3))
        self.assertEqual(m.regime([100, 101] * 40)['label'], '횡보장')
        with self.assertRaises(ValueError):
            m.regime([1] * 60)

    def test_drawdown_uses_recent_peak(self):
        self.assertEqual(m.regime([100] * 70 + [50] * 10)['drawdown_pct'], -50.0)

    def test_today_flow_needs_change_and_breadth(self):
        self.assertEqual(m.today_flow(2.0, 600, 300), '강세')
        self.assertEqual(m.today_flow(-2.0, 200, 600), '약세')
        self.assertEqual(m.today_flow(2.0, 400, 350), '혼조')
        self.assertEqual(m.today_flow(None, 1, 1), '판단 불가')

    def test_num_parses_signed_commas(self):
        self.assertEqual((m.num('+6,555'), m.num('-1,119'), m.num('5,375.36'), m.num(None)), (6555, -1119, 5375.36, None))


class ThemeTests(unittest.TestCase):
    def test_groups_are_count_weighted_and_first_match_wins(self):
        rows = [{'name': 'HBM(고대역폭메모리)', 'change_pct': 10.0, 'count': 30, 'rise': 30, 'fall': 0},
                {'name': '반도체 장비', 'change_pct': 0.0, 'count': 10, 'rise': 5, 'fall': 5},
                {'name': '피지컬 AI/휴머노이드 로봇', 'change_pct': 4.0, 'count': 5, 'rise': 4, 'fall': 1},
                {'name': '여행', 'change_pct': -1.0, 'count': 9, 'rise': 1, 'fall': 8}]
        groups = {g['group']: g for g in m.theme_groups(rows)}
        self.assertEqual(groups['반도체']['change_pct'], 7.5)
        self.assertEqual(groups['반도체']['leader'], 'HBM(고대역폭메모리)')
        self.assertIn('로봇', groups)  # matched before the AI group
        self.assertNotIn('AI·데이터센터', groups)
        self.assertEqual(len(groups), 2)


class SummaryTests(unittest.TestCase):
    def payload(self, at):
        regime = m.regime([200 - i for i in range(80)])
        return {'time': at.isoformat(), 'indices': {'KOSPI': {
            'name': '코스피', 'regime': regime, 'change_pct': 1.2, 'flow': '혼조',
            'investors_100m': {'개인': -1.0, '외국인': 2.0, '기관': -1.0}, 'program_100m': {'전체': 3.0},
            'breadth': {'상승': 500, '하락': 300}}},
            'themes': {'groups': [{'group': '반도체', 'change_pct': 3.0}]},
            'global': {'나스닥100': {'change_pct': -0.5}}}

    def test_fresh_summary_and_stale_or_broken_is_none(self):
        view = m.summary(self.payload(NOW - timedelta(minutes=5)), NOW)
        self.assertEqual(view['코스피']['regime'], '하락장')
        self.assertEqual(view['theme_groups_today_pct'], {'반도체': 3.0})
        self.assertEqual(view['global_last_daily_change_pct'], {'나스닥100': -0.5})
        self.assertIsNone(m.summary(self.payload(NOW - timedelta(minutes=30)), NOW))
        self.assertIsNone(m.summary(None, NOW))
        self.assertIsNone(m.summary({'time': 'bad'}, NOW))


class FreshnessTests(unittest.TestCase):
    def test_pre_open_snapshot_does_not_report_yesterdays_tone_as_today(self):
        regime = m.regime([200 - i for i in range(80)])
        early = NOW.replace(hour=8, minute=50)
        payload = {'time': early.isoformat(), 'indices': {'KOSPI': {'name': '코스피', 'regime': regime, 'change_pct': -2.0, 'flow': '약세',
                   'investors_100m': {}, 'program_100m': {}, 'breadth': {'상승': 1, '하락': 9}}}}
        view = m.summary(payload, early.replace(minute=55))
        self.assertEqual((view['코스피']['today_tone'], view['코스피']['today_change_pct']), ('장전(전일 흐름)', None))



class ReuseTests(unittest.TestCase):
    def test_a_failed_index_reuses_a_recent_fetch_but_never_renews_an_old_one(self):
        from unittest.mock import patch
        kept = {'name': '코스피', 'regime': {'label': '상승장'}}
        def collect(previous):
            with patch.object(m, 'load', return_value=previous), patch.object(m, 'index_snapshot', side_effect=OSError), \
                 patch.object(m, 'themes', side_effect=OSError), patch.object(m, 'global_prices', side_effect=OSError), \
                 patch.object(m, 'program_intraday', side_effect=OSError):
                return m.collect(NOW)
        recent = collect({'time': (NOW - timedelta(minutes=5)).isoformat(), 'indices': {'KOSPI': kept}})
        self.assertEqual(recent['indices']['KOSPI']['reused_from'], (NOW - timedelta(minutes=5)).isoformat())
        chained = collect({'time': (NOW - timedelta(minutes=5)).isoformat(),
                           'indices': {'KOSPI': {**kept, 'reused_from': (NOW - timedelta(minutes=40)).isoformat()}}})
        self.assertNotIn('KOSPI', chained['indices'])  # originally fetched 40 minutes ago: dropped, not renewed


class ProgramIntradayTests(unittest.TestCase):
    def test_kiwoom_rows_become_oldest_first_100m_krw(self):
        from unittest.mock import patch
        import kiwoom_data
        raw = [{'cntr_tm': '094400', 'dfrt_trde_netprps': '+32997', 'ndiffpro_trde_netprps': '--196599',
                'all_netprps': '--163602', 'kospi200': '+77829', 'basis': '-1.49'},
               {'cntr_tm': '094200', 'dfrt_trde_netprps': '+36796', 'ndiffpro_trde_netprps': '--196900',
                'all_netprps': '--160104', 'kospi200': '+77885', 'basis': '-1.95'}]
        with patch.object(kiwoom_data, 'call', return_value=raw) as call:
            out = m.program_intraday(NOW)
        self.assertEqual(out['KOSPI'], {'as_of': '09:44', 'rows': [['09:42', 368.0, -1969.0, -1601.0, 778.9, -1.95],
                                                                  ['09:44', 330.0, -1966.0, -1636.0, 778.3, -1.49]]})
        self.assertEqual({c.args[1]['mrkt_tp'] for c in call.call_args_list}, {'P00101', 'P10102'})

    def test_section_absent_without_key_and_failure_is_an_error(self):
        from unittest.mock import patch
        import kiwoom_data
        base = [patch.object(m, 'load', return_value=None), patch.object(m, 'index_snapshot', side_effect=OSError),
                patch.object(m, 'themes', side_effect=OSError), patch.object(m, 'global_prices', side_effect=OSError)]
        for p in base:
            p.start()
        self.addCleanup(patch.stopall)
        with patch.object(kiwoom_data, 'configured', return_value=False):
            payload = m.collect(NOW)
        self.assertFalse({'program_intraday', 'foreign_desks', 'index_intraday', 'investor_rankings'} & set(payload))  # no keys in tests
        self.assertFalse([e for e in payload['errors'] if '키움' in e or '토스' in e])
        with patch.object(kiwoom_data, 'configured', return_value=True), \
             patch.object(m, 'program_intraday', side_effect=OSError), patch.object(m, 'foreign_desks', side_effect=ValueError):
            payload = m.collect(NOW)
        self.assertFalse({'program_intraday', 'foreign_desks'} & set(payload))
        self.assertIn('프로그램 시계열(키움): OSError', payload['errors'])
        self.assertIn('외국계창구(키움): ValueError', payload['errors'])

    def test_a_failed_program_reading_reuses_the_last_one_for_minutes_only(self):
        # 11-24 10:11: one Kiwoom 502 took the program series out of Jev's market view for five minutes
        from unittest.mock import patch
        import kiwoom_data
        section = {'source': 'kiwoom:ka90005', 'markets': {'KOSPI': {'as_of': '13:02', 'rows': [['13:02', 1.0, 2.0, 3.0, 0, 0]]}}}

        def collect(minutes, **extra):
            previous = {'time': (NOW - timedelta(minutes=minutes)).isoformat(), 'program_intraday': {**section, **extra},
                        'foreign_desks': {'net_buy': [{'name': 'x'}]}}
            with patch.object(m, 'load', return_value=previous), patch.object(m, 'index_snapshot', side_effect=OSError), \
                 patch.object(m, 'themes', side_effect=OSError), patch.object(m, 'global_prices', side_effect=OSError), \
                 patch.object(m, 'global_intraday', side_effect=OSError), patch.object(kiwoom_data, 'configured', return_value=True), \
                 patch.object(m, 'index_fallback', side_effect=OSError), \
                 patch.object(m, 'program_intraday', side_effect=OSError), patch.object(m, 'foreign_desks', side_effect=OSError):
                return m.collect(NOW)
        kept = collect(5)
        self.assertEqual(kept['program_intraday'], {**section, 'reused_from': (NOW - timedelta(minutes=5)).isoformat()})
        self.assertIn('프로그램 시계열(키움): OSError', kept['errors'])  # the failure is still told
        self.assertNotIn('foreign_desks', kept)  # its rows carry no time, so nothing could tell an old one
        self.assertNotIn('program_intraday', collect(11))
        self.assertNotIn('program_intraday', collect(5, reused_from=(NOW - timedelta(minutes=12)).isoformat()))  # never renewed
        self.assertNotIn('program_intraday', collect(5, reused_from='broken'))

    def test_foreign_desks_top_rows_in_100m_krw(self):
        from unittest.mock import patch
        import kiwoom_data
        rows = {'1': [{'stk_cd': '009150', 'stk_nm': '삼성전기', 'flu_rt': '-0.76', 'netprps_prica': '+20780'}],
                '2': [{'stk_cd': '005930', 'stk_nm': '삼성전자', 'flu_rt': '-2.08', 'netprps_prica': '--272448'}] * 30}
        with patch.object(kiwoom_data, 'call', side_effect=lambda api, body, key, **kw: rows[body['trde_tp']]):
            got = m.foreign_desks()
        self.assertEqual(got['net_buy'], [{'code': '009150', 'name': '삼성전기', 'change_pct': -0.76, 'net_100m': 207.8}])
        self.assertEqual((len(got['net_sell']), got['net_sell'][0]['net_100m']), (20, -2724.5))


class IndexIntradayTests(unittest.TestCase):
    def test_path_summary_from_toss_minutes(self):
        from unittest.mock import patch
        import toss_data
        at = lambda h, mi: NOW.replace(hour=h, minute=mi)  # noqa: E731
        bars = [(at(9, 1), 100, 101, 99, 100, 1)] + [(at(9, 1) + timedelta(minutes=i), 100 + i, 100 + i, 100 + i, 100 + i, 1)
                                                      for i in range(1, 41)]
        with patch.object(toss_data, 'index_minutes', side_effect=lambda code, now: {'bars': bars if code == 'KOSPI' else [],
                                                                                      'prev_close': 110.0}):
            got = m.index_intraday(NOW)
        self.assertEqual(list(got), ['KOSPI'])  # no bar today: left out
        k = got['KOSPI']
        self.assertEqual((k['as_of'], k['open'], k['high'], k['low'], k['last']), ('09:41', 100, 140, 99, 140))
        self.assertEqual((k['change_pct'], k['from_open_pct'], k['change_30m_pct'], k['range_position_pct']),
                         (27.27, 40.0, 27.27, 100))
        self.assertEqual((len(k['last_30']), k['last_30'][-1]), (30, ['09:41', 140]))

    def test_toss_minutes_today_only_and_previous_close(self):
        from unittest.mock import patch
        import toss_data
        page = {'candles': [{'timestamp': '2026-11-12T09:02:00+09:00', 'openPrice': '2', 'highPrice': '3', 'lowPrice': '1', 'closePrice': '2.5', 'volume': '7'},
                            {'timestamp': '2026-11-12T09:01:00+09:00', 'openPrice': '1', 'highPrice': '2', 'lowPrice': '1', 'closePrice': '2', 'volume': '5'},
                            {'timestamp': '2026-11-11T15:30:00+09:00', 'openPrice': '9', 'highPrice': '9', 'lowPrice': '9', 'closePrice': '9', 'volume': '1'}],
                'nextBefore': 'x'}
        with patch.object(toss_data, 'get', autospec=True, return_value=page) as get:
            got = toss_data.index_minutes('KOSPI', NOW)
        self.assertEqual(([b[0].strftime('%H:%M') for b in got['bars']], got['prev_close'], get.call_count), (['09:01', '09:02'], 9.0, 1))
        with self.assertRaises(toss_data.TossError):
            toss_data.get('/api/v1/holdings')


class NaverFallbackTests(unittest.TestCase):
    def sources(self):
        from unittest.mock import patch
        import kiwoom_data
        import toss_data
        sect = {'cur_prc': '-5058.29', 'flu_rt': '-0.83', 'trde_prica': '7486649', 'rising': '220', 'stdns': '36', 'fall': '656',
                'upl': '3', 'lst': '0', '52wk_hgst_pric': '+9385.59', '52wk_lwst_pric': '-3833.35', 'return_code': 0}
        candles = [{'timestamp': (NOW - timedelta(days=i)).strftime('%Y-%m-%dT00:00:00+09:00'), 'closePrice': str(6000 - i * 0)}
                   for i in range(100)]
        return [patch.object(kiwoom_data, 'configured', return_value=True), patch.object(toss_data, 'configured', return_value=True),
                patch.object(kiwoom_data, 'call', side_effect=lambda api, body, key, **kw: sect if api == 'ka20001' else [
                    {'thema_nm': 'HBM', 'flu_rt': '+3.0', 'stk_num': '10', 'rising_stk_num': '8', 'fall_stk_num': '2'}]),
                patch.object(toss_data, 'get', autospec=True, return_value={'candles': candles}),
                patch.object(toss_data, 'market_investors', return_value={'개인': 1.0, '외국인': -2.0, '기관': 3.0}),
                patch.object(m, 'program_intraday', return_value={'KOSPI': {'rows': [['12:00', 1.0, -2.0, -1.0, 780.0, 0.5]]}}),
                patch.object(m, 'index_snapshot', side_effect=OSError), patch.object(m, 'themes', side_effect=OSError),
                patch.object(m, 'global_prices', side_effect=OSError), patch.object(m, 'foreign_desks', return_value={}),
                patch.object(m, 'index_intraday', return_value={}), patch.object(toss_data, 'investor_rankings', return_value={})]

    def test_index_and_themes_from_kiwoom_and_toss_when_naver_fails(self):
        from unittest.mock import patch
        recent = {'time': (NOW - timedelta(minutes=5)).isoformat(), 'indices': {'KOSPI': {'name': '코스피', 'regime': {'label': '상승장'}}}}
        patches = self.sources() + [patch.object(m, 'load', return_value=recent)]
        for p in patches:
            p.start()
        self.addCleanup(patch.stopall)
        payload = m.collect(NOW)
        k = payload['indices']['KOSPI']
        self.assertNotIn('reused_from', k)  # a fresh fallback beats reusing the older Naver reading
        self.assertEqual((k['close'], k['change_pct'], k['value_100m'], k['high52'], k['low52'], k['status']),
                         (5058.29, -0.83, 74866, 9385.59, 3833.35, 'FALLBACK'))
        self.assertEqual((k['breadth']['상승'], k['breadth']['하락'], k['investors_100m'], k['program_100m']),
                         (220, 656, {'개인': 1.0, '외국인': -2.0, '기관': 3.0}, {'차익': 1.0, '비차익': -2.0, '전체': -1.0}))
        self.assertEqual(k['history'][-1], [NOW.strftime('%Y-%m-%d'), 5058.29])  # today's bar at the level just read
        self.assertIn('kiwoom:ka20001', k['fallback']['source'])
        self.assertEqual(payload['themes']['source'], 'kiwoom:ka90001')
        self.assertEqual(payload['themes']['groups'][0]['group'], '반도체')
        self.assertIn('코스피: OSError', payload['errors'])
        self.assertIn('fallback_source', m.summary(payload, NOW)['코스피'])

    def test_failed_fallback_still_reuses_a_recent_naver_reading(self):
        from unittest.mock import patch
        recent = {'time': (NOW - timedelta(minutes=5)).isoformat(), 'indices': {'KOSPI': {'name': '코스피', 'regime': {'label': '상승장'}}}}
        patches = self.sources() + [patch.object(m, 'load', return_value=recent), patch.object(m, 'index_fallback', side_effect=ValueError)]
        for p in patches:
            p.start()
        self.addCleanup(patch.stopall)
        payload = m.collect(NOW)
        self.assertEqual(payload['indices']['KOSPI']['reused_from'], recent['time'])
        self.assertIn('코스피 대체(키움·토스): ValueError', payload['errors'])


class IndexFallbackTests(unittest.TestCase):
    def fallback(self, now, candle_days):
        from unittest.mock import patch
        import kiwoom_data
        import toss_data
        level = {'cur_prc': '+2500.00', 'flu_rt': '-1.20', 'rising': '300', 'fall': '600', 'trde_prica': '1234500'}
        candles = {'candles': [{'timestamp': f'{d}T00:00:00+09:00', 'closePrice': str(2400 + i)} for i, d in enumerate(candle_days)]}
        program = lambda: {'KOSPI': {'rows': [['10:00', 1.0, 2.0, 3.0, 300.0, 0.1]]}}  # noqa: E731
        with patch.object(kiwoom_data, 'call', return_value=level) as call, patch.object(toss_data, 'get', autospec=True, return_value=candles), \
             patch.object(toss_data, 'market_investors', return_value={}):
            got = m.index_fallback('KOSPI', now, program)
        return got, call

    def days(self, last):
        from datetime import date
        return [(date.fromisoformat(last) - timedelta(days=99 - i)).isoformat() for i in range(100)]

    def test_before_the_open_the_level_is_dated_by_the_last_session(self):
        got, _ = self.fallback(datetime(2026, 11, 18, 8, 10, tzinfo=m.KST), self.days('2026-11-17'))
        self.assertEqual(got['traded_at'], '2026-11-17T15:30:00+09:00')
        self.assertEqual(got['history'][-1], ['2026-11-17', 2500.0])  # the level read is that session's close

    def test_in_session_without_a_toss_bar_today_the_level_is_added_as_today(self):
        now = datetime(2026, 11, 18, 10, 5, tzinfo=m.KST)
        got, call = self.fallback(now, self.days('2026-11-17'))
        self.assertEqual((got['traded_at'], got['history'][-1]), (now.isoformat(timespec='seconds'), ['2026-11-18', 2500.0]))
        self.assertEqual(got['program_100m'], {'차익': 1.0, '비차익': 2.0, '전체': 3.0})
        self.assertEqual(call.call_count, 1)  # program trading came from the shared reading, not another Kiwoom request


class KiwoomDataTests(unittest.TestCase):
    def test_signed_strings_and_allowlist(self):
        import kiwoom_data as k
        self.assertEqual([k.num(v) for v in ('+32997', '--196599', '-1.49', '1,234', '', None)],
                         [32997, -196599, -1.49, 1234, None, None])
        with self.assertRaises(k.KiwoomError):
            k.call('kt10000', {}, 'x')  # an order TR never reaches the network


if __name__ == '__main__':
    unittest.main()


def test_global_prices_reports_missing_tickers_as_a_response_error(monkeypatch):
    import sys
    import types
    import pandas as pd
    import market_context as m
    noted = []
    monkeypatch.setattr(m.api_calls, 'problem', lambda provider, endpoint, detail: noted.append((provider, detail)))
    first, second = list(m.GLOBAL.values())[:2]
    index = pd.to_datetime(['2026-11-16', '2026-11-17'])
    frame = pd.DataFrame({first: [100.0, 101.0], second: [float('nan'), float('nan')]}, index=index)
    monkeypatch.setitem(sys.modules, 'yfinance', types.SimpleNamespace(download=lambda *a, **k: {'Close': frame}))
    got = m.global_prices()
    assert [v['ticker'] for v in got.values()] == [first]
    assert noted == [('yahoo', f'{len(m.GLOBAL) - 1}개 종목 없음')]


def test_summary_carries_the_intraday_sections_the_engines_did_not_see():
    """User 2026-11-18: index path, last-30-minute program flow, foreign desks and investor rankings reach Jev."""
    from datetime import datetime
    import market_context as mc
    payload = {'time': '2026-11-18T13:03:22+09:00', 'indices': {}, 'global': {},
               'index_intraday': {'markets': {'KOSPI': {'as_of': '13:04', 'change_pct': 1.18, 'from_open_pct': -1.41, 'change_30m_pct': 0.5,
                                                        'range_position_pct': 44, 'open': 5098.8, 'high': 5133.1, 'low': 4943.2, 'last': 5027.0, 'bars': 244}}},
               'program_intraday': {'markets': {'KOSPI': {'as_of': '13:02', 'rows': [['12:30', 0, 0, 300.0, 0, 0], ['12:32', 0, 0, 350.0, 0, 0],
                                                                                    ['13:02', 0, 0, 415.6, 0, 0]]}}},
               'foreign_desks': {'net_buy': [{'code': '000660', 'name': 'SK하이닉스', 'change_pct': 4.01, 'net_100m': 2659.5}]},
               'investor_rankings': {'investors': {'외국인': {'ranked_at': '2026-11-18T13:03:02+09:00', 'net_buy': [{'code': '1', 'name': 'A', 'net_100m': 9}]}}}}
    view = mc.summary(payload, datetime(2026, 11, 18, 13, 5, tzinfo=mc.KST))
    assert view['index_intraday']['KOSPI']['from_open_pct'] == -1.41 and 'bars' not in view['index_intraday']['KOSPI']
    assert view['program_intraday']['KOSPI'] == {'as_of': '13:02', 'cumulative_net_100m': 415.6, 'last_30m_net_100m': 65.6}  # since 12:32
    assert view['foreign_desks_net_buy_top'] == [{'name': 'SK하이닉스', 'change_pct': 4.01, 'net_100m': 2659.5}]
    assert view['investor_net_buy_rankings']['외국인'] == {'ranked_at': '13:03', 'net_buy_top': [{'name': 'A', 'net_100m': 9}]}
    lagging = {**payload, 'investor_rankings': {'investors': {'기관': {'ranked_at': '2026-11-18T11:05:00+09:00',
                                                                       'net_buy': [{'name': 'B', 'net_100m': 1}]}}}}
    assert 'investor_net_buy_rankings' not in mc.summary(lagging, datetime(2026, 11, 18, 13, 5, tzinfo=mc.KST))  # 2 hours behind
    early = mc.summary({**payload, 'time': '2026-11-18T08:40:00+09:00'}, datetime(2026, 11, 18, 8, 41, tzinfo=mc.KST))
    assert 'index_intraday' not in early  # before the open these are yesterday's


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_yahoo_or_naver(monkeypatch):
    """No test here reaches Yahoo or Naver: a section that would is a failed fetch (an error), as in production."""
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: (_ for _ in ()).throw(OSError('no network in tests')))


def _offline(monkeypatch, *names):
    for name in names or ('index_snapshot', 'themes', 'global_prices'):
        monkeypatch.setattr(m, name, lambda *a, **k: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(m, 'load', lambda *a, **k: None)


def test_global_intraday_change_is_against_the_previous_session_close(monkeypatch):
    """User 2026-11-18: futures and FX now, not yesterday's daily change. change_pct = last vs Yahoo's previousClose
    (the previous session's settlement), not chartPreviousClose; change_60m_pct = last vs the bar 60+ minutes earlier."""
    import types
    import requests
    stamp = lambda h, mi: int(datetime(2026, 11, 18, h, mi, tzinfo=m.KST).timestamp())  # noqa: E731
    stamps = [stamp(12, 0) + 300 * i for i in range(24)]  # 12:00 .. 13:55
    closes = [round(100 + 0.1 * i, 1) for i in range(24)]  # 12:55 -> 101.1, 13:55 -> 102.3
    spark = {'NQ=F': {'timestamp': stamps, 'close': closes, 'previousClose': 100.0, 'chartPreviousClose': 50.0},
             'GC=F': {'timestamp': stamps, 'close': closes, 'previousClose': None},
             'KRW=X': {'timestamp': stamps[:3], 'close': [1330.0, 1331.0, None], 'previousClose': 1334.7}}
    asked, noted = [], []

    def get(url, params=None, **kw):
        asked.append(params['symbols'])
        return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: spark)
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(m.api_calls, 'problem', lambda provider, endpoint, detail: noted.append(detail))
    got = m.global_intraday()
    assert asked == [','.join(m.GLOBAL_INTRADAY.values())]  # one request for every symbol
    assert got['나스닥100 선물'] == {'ticker': 'NQ=F', 'price': 102.3, 'previous_close': 100.0, 'as_of': '2026-11-18T13:55+09:00',
                                'change_pct': 2.3, 'change_60m_pct': 1.19}
    assert got['원/달러 환율'] == {'ticker': 'KRW=X', 'price': 1331.0, 'previous_close': 1334.7, 'as_of': '2026-11-18T12:05+09:00',
                              'change_pct': -0.28, 'change_60m_pct': None}  # the empty newest bar skipped; nothing 60 minutes back
    assert set(got) == {'나스닥100 선물', '원/달러 환율'}  # no previous close or no bars: left out, never zeros
    assert noted == ['4개 종목 없음']


def test_collect_lists_missing_overseas_quotes_and_a_failed_request(monkeypatch):
    _offline(monkeypatch)
    payload = m.collect(NOW)  # the request fails (no network here)
    assert 'global_intraday' not in payload and '해외 선물·환율 5분봉: OSError' in payload['errors']
    quote = {'ticker': 'GC=F', 'price': 3787.8, 'previous_close': 3864.2, 'as_of': '2026-11-12T09:48+09:00', 'change_pct': -1.98,
             'change_60m_pct': -0.5}
    monkeypatch.setattr(m, 'global_intraday', lambda: {'금 선물': quote})
    payload = m.collect(NOW)
    assert payload['global_intraday']['markets'] == {'금 선물': quote}
    assert [e for e in payload['errors'] if e.startswith('해외 선물')] == [f'해외 선물·환율 5분봉: {name} 없음'
                                                                       for name in m.GLOBAL_INTRADAY if name != '금 선물']


def test_summary_shows_overseas_quotes_by_their_own_time():
    quote = lambda at, change: {'as_of': at, 'change_pct': change, 'change_60m_pct': 0.1, 'price': 1.0}  # noqa: E731
    payload = {'time': '2026-11-18T10:00:00+09:00', 'indices': {}, 'global': {'금 선물': {'change_pct': 0.29}},
               'global_intraday': {'markets': {'나스닥100 선물': quote('2026-11-18T09:47+09:00', 0.38),  # CME ~10 min late: kept
                                               '금 선물': quote('2026-11-18T09:20+09:00', -2.0),  # 40 minutes behind
                                               '원/달러 환율': quote('2026-11-17T09:55+09:00', -0.2)}}}  # same clock, a day old
    view = m.summary(payload, datetime(2026, 11, 18, 10, 1, tzinfo=m.KST))
    assert view['global_intraday'] == {'나스닥100 선물': {'as_of': '09:47', 'change_pct': 0.38, 'change_60m_pct': 0.1}}
    assert view['global_last_daily_change_pct'] == {'금 선물': 0.29}  # the previous US session's daily change stays
    early = {**payload, 'time': '2026-11-18T08:50:00+09:00',
             'global_intraday': {'markets': {'나스닥100 선물': quote('2026-11-18T08:38+09:00', 0.4)}}}
    assert m.summary(early, datetime(2026, 11, 18, 8, 51, tzinfo=m.KST))['global_intraday']['나스닥100 선물']['as_of'] == '08:38'  # live overnight


def test_theme_leaders_of_the_strongest_themes_reach_the_summary(monkeypatch):
    """User 2026-11-18: the 2-3 leading stocks of today's top 5 themes, with change and trading value, at most 5 calls."""
    calls = []

    def member(code, change, value, at):
        return {'itemCode': code, 'stockName': f'S{code}', 'fluctuationsRatio': change, 'accumulatedTradingValueRaw': value,
                'localTradedAt': f'2026-11-12T{at}:10+09:00'}

    def naver(path):
        calls.append(path)
        if path.startswith('stocks/theme?'):
            return {'groups': [{'no': no, 'name': f'T{no}', 'totalCount': 5, 'changeRate': str(10 - no), 'riseCount': 3, 'fallCount': 1}
                               for no in range(1, 8)]}
        no = int(path.split('?')[0].rsplit('/', 1)[1])
        if no == 2:
            raise OSError
        at = '09:30' if no == 3 else '09:58'
        return {'stocks': [member('A', '3.10', '120000000', at), member('B', '29.96', '52061000000', at),
                           member('C', '-1.00', '0', at), member('D', '11.82', None, at)]}
    _offline(monkeypatch, 'index_snapshot', 'global_prices')  # themes() parses the fake Naver list
    monkeypatch.setattr(m, 'naver', naver)
    payload = m.collect(NOW)
    assert sum(p.startswith('stocks/theme/') for p in calls) == 5  # the five strongest themes only
    themes = payload['theme_leaders']['themes']
    assert [t['theme'] for t in themes] == ['T1', 'T3', 'T4', 'T5']  # T2's call failed: left out
    assert themes[0] == {'theme': 'T1', 'change_pct': 9.0, 'as_of': '09:58', 'leaders': [
        {'code': 'B', 'name': 'SB', 'change_pct': 29.96, 'value_100m': 520.6}, {'code': 'D', 'name': 'SD', 'change_pct': 11.82, 'value_100m': None},
        {'code': 'A', 'name': 'SA', 'change_pct': 3.1, 'value_100m': 1.2}]}
    assert '테마 주도주(네이버) T2: OSError' in payload['errors']
    view = m.summary(payload, NOW)
    assert [t['theme'] for t in view['theme_leaders']] == ['T1', 'T4', 'T5']  # T3 stopped at 09:30: 30 minutes behind
    assert view['theme_leaders'][0]['leaders'][0] == {'name': 'SB', 'change_pct': 29.96, 'value_100m': 520.6}


def test_a_stale_30_minute_index_change_is_not_offered_as_current():
    """r3 E7: the payload may be 20 minutes old and its Toss section 20 more, so the inverse gate's "not rebounding over
    the last 30 minutes" took a 40-minute-old reading. Older than 10 minutes, the 30-minute change is left out."""
    from datetime import datetime, timedelta
    import market_context as M
    paid = datetime(2026, 11, 18, 10, 20, tzinfo=M.KST)
    item = {'name': '코스피', 'regime': {'label': '하락장', 'reasons': [], 'drawdown_pct': -5}, 'change_pct': -1.2,
            'flow': '약세', 'investors_100m': {}, 'program_100m': {'전체': -800}, 'breadth': {'상승': 100, '하락': 700}}
    payload = {'time': paid.isoformat(), 'indices': {'KOSPI': item},
               'index_intraday': {'markets': {'KOSPI': {'as_of': '10:00', 'change_pct': -1.2, 'change_30m_pct': -0.4, 'high': 2500}}}}
    late = M.summary(payload, now=paid + timedelta(minutes=20))['index_intraday']['코스피']  # read at 10:40
    assert late['change_30m_pct'] is None and late['high'] == 2500 and late['as_of'] == '10:00'  # the rest stays
    payload['index_intraday']['markets']['KOSPI']['as_of'] = '10:18'
    assert M.summary(payload, now=paid + timedelta(minutes=5))['index_intraday']['코스피']['change_30m_pct'] == -0.4
