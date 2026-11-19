import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import issue_candidates as ic
import kiwoom_data
import local_env

KST = ZoneInfo('Asia/Seoul')
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=KST)
UNIVERSE = {'000660': {'name': 'SK하이닉스', 'index': 'KOSPI200'}}


def quote(code, price=10500, prev=10000, value=20000, vwap=10400, high=10800, low=10000, upper=13000,
          over='0', alarm='0', group='코스피', texts=('증거금 40%',)):
    q = {'iem_cd': code, 'iem_nm': '*N' + code, 'stck_prpr': price, 'stck_prdy_clpr': prev, 'acml_vol': 1000,
         'acml_tr_pbmn': value, 'wghn_avrg_prc': vwap, 'stck_hgpr': high, 'stck_lwpr': low, 'stck_mxpr': upper,
         'short_over_code': over, 'mrkt_alrm_code': alarm, 'scrt_grp_isnm': group, 'mrkt_div_isnm': '코스피'}
    q.update({f'market{i}': t for i, t in enumerate(texts, 1)})
    return q


class Reader:
    def __init__(self, quotes):
        self.quotes, self.calls = quotes, []

    def call_tool(self, tool, params):
        self.calls.append(params['stock_code'])
        q = self.quotes.get(params['stock_code'])
        return {'status': 'ok', 'data': {'Output_0': q}} if q else {'status': 'error'}


def ranks(value=(), rise=()):
    """Fake kiwoom_data.call: the value ranking (ka10030) and the rise ranking (ka10027) as stock codes."""
    def call(api_id, body, key, max_pages=1):
        codes = value if api_id == 'ka10030' else rise
        return [{'stk_cd': c, 'stk_nm': '이름' + c} for c in codes]
    return call


class Response:
    def __init__(self, data):
        self.status_code, self._data = 200, data

    def json(self):
        return self._data


def filing(number, code, title, cls='Y'):
    return {'rcept_no': number, 'stock_code': code, 'corp_name': '회사' + code, 'report_nm': title, 'corp_cls': cls,
            'rcept_dt': '20261118'}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.db = self.root / 'research.sqlite3'
        self.env = self.root / 'test.env'
        self.env.write_text('')  # no DART key unless a test writes one
        self.state = {}
        for target in (patch.object(local_env, 'ENV', self.env), patch.object(kiwoom_data, 'configured', return_value=True),
                       patch.object(ic.requests, 'get', side_effect=AssertionError('no network in tests'))):
            target.start()
            self.addCleanup(target.stop)

    def tearDown(self):
        self.dir.cleanup()

    def observe(self, reader, now=NOW, value=(), rise=()):
        with patch.object(kiwoom_data, 'call', side_effect=ranks(value, rise)):
            return ic.observe(reader, now, UNIVERSE, root=self.root, db=self.db, state=self.state)

    def rows(self):
        with sqlite3.connect(self.db) as db:
            db.row_factory = sqlite3.Row
            return {r['code']: dict(r) for r in db.execute('SELECT * FROM shadow_candidates')}


class ObserveTests(Base):
    def test_records_hot_stocks_with_reasons_and_universe_flag(self):
        reader = Reader({'180640': quote('180640'), '000660': quote('000660', price=20500, prev=20000, vwap=20400,
                                                                     high=20800, low=20000, upper=26000)})
        result = self.observe(reader, value=['000660', '180640', '005935', '123450_AL'], rise=['180640'])
        rows = self.rows()
        self.assertEqual(set(rows), {'180640', '000660'})
        hot = rows['180640']
        self.assertEqual(json.loads(hot['reasons']), ['rank:rise', 'rank:trading_value'])
        self.assertEqual((hot['in_universe'], hot['price'], hot['change_pct'], hot['value_mil'], hot['name']),
                         (0, 10500, 5.0, 20000, 'N180640'))
        self.assertEqual(hot['first_seen'], NOW.isoformat(timespec='seconds'))
        self.assertEqual(rows['000660']['in_universe'], 1)
        self.assertNotIn('005935', reader.calls)  # a preferred share is never quoted
        self.assertEqual(result['dropped']['005935'], 'not_common')
        self.assertEqual(reader.calls[0], '180640')  # outside the universe first

    def test_filters_drop_and_keep(self):
        cases = {'100010': (quote('100010', over='2'), 'overheat'), '100020': (quote('100020', over='1'), None),
                 '100030': (quote('100030', alarm='2'), 'alarm'), '100040': (quote('100040', alarm='1'), None),
                 '100050': (quote('100050', value=9999), 'value'), '100060': (quote('100060', group='ETF'), 'not_common'),
                 '100070': (quote('100070', texts=('관리종목',)), 'managed'),
                 '100080': (quote('100080', price=12800, upper=13000), 'upper_limit'),
                 '100090': (quote('100090', price=12700, upper=13000), None)}
        result = self.observe(Reader({c: q for c, (q, _) in cases.items()}), value=list(cases))
        self.assertEqual(set(self.rows()), {c for c, (_, why) in cases.items() if why is None})
        self.assertEqual({c: result['dropped'][c] for c, (_, why) in cases.items() if why}, {c: why for c, (_, why) in cases.items() if why})

    def test_a_dropped_code_is_quoted_again_only_after_the_recheck_time(self):
        reader = Reader({'100050': quote('100050', value=9999)})
        self.observe(reader, value=['100050'])
        self.observe(reader, now=NOW + timedelta(minutes=5), value=['100050'])
        self.assertEqual(reader.calls, ['100050'])
        reader.quotes['100050'] = quote('100050')
        self.observe(reader, now=NOW + timedelta(minutes=31), value=['100050'])
        self.assertEqual(reader.calls, ['100050', '100050'])
        self.assertEqual(self.rows()['100050']['first_seen'], (NOW + timedelta(minutes=31)).isoformat(timespec='seconds'))

    def test_a_failed_read_is_retried_next_round(self):
        reader = Reader({})
        self.observe(reader, value=['100050'])
        reader.quotes['100050'] = quote('100050')
        self.observe(reader, now=NOW + timedelta(minutes=5), value=['100050'])
        self.assertEqual(reader.calls, ['100050', '100050'])
        self.assertIn('100050', self.rows())

    def test_duplicate_sightings_keep_the_first_and_add_reasons(self):
        reader = Reader({'180640': quote('180640')})
        self.observe(reader, value=['180640'])
        reader.quotes['180640'] = quote('180640', price=11500)
        result = self.observe(reader, now=NOW + timedelta(minutes=5), rise=['180640'])
        row = self.rows()['180640']
        self.assertEqual((row['first_seen'], row['price']), (NOW.isoformat(timespec='seconds'), 10500))
        self.assertEqual(json.loads(row['reasons']), ['rank:rise', 'rank:trading_value'])
        self.assertEqual(reader.calls, ['180640'])
        self.assertEqual(result['recorded'], [])

    def test_quotes_per_round_are_capped(self):
        outside = [f'2{i:04d}0' for i in range(ic.MAX_QUOTES + 5)]  # common shares end in 0
        reader = Reader({c: quote(c) for c in outside})
        self.observe(reader, value=outside)
        self.assertEqual(len(reader.calls), ic.MAX_QUOTES)
        self.observe(reader, now=NOW + timedelta(minutes=5), value=outside)
        self.assertEqual(set(self.rows()), set(outside))

    def test_would_buy_applies_the_change_band_and_chase_limits(self):
        cases = {'300010': (quote('300010'), None),  # +5%, 0.96% over VWAP, 62% of the range
                 '300020': (quote('300020', vwap=10200), 'above_vwap'),  # 2.94% over VWAP
                 '300030': (quote('300030', high=10550), 'near_range_top'),  # 91% of the range
                 '300040': (quote('300040', price=10100, vwap=10100, high=10200), 'change_band')}  # +1%
        self.observe(Reader({c: q for c, (q, _) in cases.items()}), value=list(cases))
        verdicts = {c: json.loads(r['would_buy']) for c, r in self.rows().items()}
        self.assertEqual({c: v['blocked'] for c, v in verdicts.items()}, {c: why for c, (_, why) in cases.items()})
        self.assertEqual({c: v['ok'] for c, v in verdicts.items()}, {c: why is None for c, (_, why) in cases.items()})
        self.assertEqual((verdicts['300010']['vs_vwap_pct'], verdicts['300010']['range_position_pct']), (0.96, 62))

    def test_dart_keyword_filings_once_per_interval(self):
        self.env.write_text('OPENDART_API_KEY=test-key\n')
        page = {'status': '000', 'total_page': 3, 'list': [
            filing('20261118000001', '180640', '주식등의대량보유상황보고서(일반)'),
            filing('20261118000002', '035720', '임원ㆍ주요주주특정증권등소유상황보고서'),
            filing('20261118000003', '', '최대주주변경', cls='E')]}
        with patch.object(ic.requests, 'get', return_value=Response(page)) as get:
            reader = Reader({'180640': quote('180640'), '035720': quote('035720')})
            self.observe(reader)
            self.assertEqual(get.call_count, 2)  # page 2 holds only filings already seen: no page 3
            self.observe(reader, now=NOW + timedelta(minutes=5))
            self.assertEqual(get.call_count, 2)
            self.observe(reader, now=NOW + timedelta(minutes=16))
            self.assertEqual(get.call_count, 3)
        self.assertEqual(json.loads(self.rows()['180640']['reasons']), ['dart:대량보유상황보고서(일반)'])
        self.assertEqual(set(self.rows()), {'180640'})

    def test_ranked_codes_are_quoted_before_dart_only_ones(self):
        self.env.write_text('OPENDART_API_KEY=test-key\n')
        page = {'status': '000', 'total_page': 1, 'list': [filing('20261118000001', '035720', '최대주주변경')]}
        reader = Reader({'035720': quote('035720'), '000660': quote('000660')})
        with patch.object(ic.requests, 'get', return_value=Response(page)), patch.object(ic, 'MAX_QUOTES', 1):
            self.observe(reader, value=['000660'])  # 000660 is inside the universe, 035720 outside
        self.assertEqual(reader.calls, ['000660'])

    def test_theme_leaders_when_market_context_has_them(self):
        (self.root / 'market-context.json').write_text(json.dumps(
            {'time': (NOW - timedelta(minutes=2)).isoformat(), 'themes': {},
             'theme_leaders': {'source': 'naver', 'themes': [{'theme': 'AI', 'change_pct': 3.1, 'as_of': '09:57', 'leaders': [{'code': '035720', 'name': '카카오', 'change_pct': 5.2, 'value_100m': 120.0}]}]}}))
        self.observe(Reader({'035720': quote('035720')}))
        self.assertEqual(json.loads(self.rows()['035720']['reasons']), ['theme:AI'])
        (self.root / 'market-context.json').write_text(json.dumps({'themes': {}}))
        self.assertEqual(self.observe(Reader({}), now=NOW + timedelta(minutes=5))['errors'], {})

    def test_theme_leaders_must_be_current(self):
        """Review 4 C8: yesterday's 15:04 theme leaders were recorded as today's candidates (market_context.summary's checks)."""
        leaders = lambda as_of: {'themes': [{'theme': '로봇', 'as_of': as_of, 'leaders': [{'code': '123450', 'name': 'X'}]}]}  # noqa: E731
        for time, as_of in (('2026-11-17T15:04:17+09:00', '15:04'),  # yesterday's file
                            ((NOW - timedelta(minutes=2)).isoformat(), '09:30')):  # today's file, the theme read stopped at 09:30
            (self.root / 'market-context.json').write_text(json.dumps({'time': time, 'theme_leaders': leaders(as_of)}))
            self.assertEqual(self.observe(Reader({'123450': quote('123450')}))['recorded'], [])

    def test_a_failing_source_only_skips_itself(self):
        (self.root / 'market-context.json').write_text(json.dumps(
            {'time': NOW.isoformat(), 'theme_leaders': {'themes': [{'theme': 'AI', 'as_of': '10:00', 'leaders': [{'code': '035720', 'name': '카카오'}]}]}}))
        with patch.object(kiwoom_data, 'call', side_effect=kiwoom_data.KiwoomError('down')):
            result = ic.observe(Reader({'035720': quote('035720')}), NOW, UNIVERSE, root=self.root, db=self.db, state=self.state)
        self.assertEqual(result['errors'], {'rankings': 'KiwoomError'})
        self.assertEqual(set(self.rows()), {'035720'})


def bars(day, closes):
    """5-minute Yahoo bars from 09:00: (time, open, high, low, close) with high/low 1 around the close unless given."""
    start = datetime.fromisoformat(f'{day}T09:00:00+09:00')
    out = []
    for i, c in enumerate(closes):
        close, high, low = c if isinstance(c, tuple) else (c, c + 1, c - 1)
        out.append((start + timedelta(minutes=5 * i), close, high, low, close))
    return out


class OutcomeTests(Base):
    def test_outcomes_after_the_first_sighting_and_the_summary(self):
        reader = Reader({'180640': quote('180640'), '200010': quote('200010', vwap=10200)})
        self.observe(reader, value=['180640', '200010'], rise=['180640'])
        # 09:00..15:00 every 5 minutes, stamped at the bar's start: 100 by 10:00, 103 by 10:30, high 106 and low 97 later,
        # 98 by 15:00 (blocked_outcomes.settle reads closes of bars that had ended, Review 4 C6)
        closes = [100] * 13 + [101, 101, 102, 102, 103, 103] + [(104, 106, 103), (98, 99, 97)] + [99] * 50 + [98, 98]
        series = {'180640.KS': bars('2026-11-18', closes), '200010.KQ': bars('2026-11-18', [100] * 73)}
        fetch = lambda symbol, interval, rng: series.get(symbol, [])
        for _ in range(2):  # a rerun recomputes the same values
            self.assertEqual(ic.update_outcomes('2026-11-18', db=self.db, fetch=fetch), {'day': '2026-11-18', 'rows': 2, 'settled': 2})
        row = self.rows()['180640']
        self.assertEqual((row['base_price'], row['r30'], row['r_close'], row['max_pct'], row['min_pct']), (100, 3.0, -2.0, 6.0, -3.0))
        summary = ic.summary(db=self.db)
        self.assertEqual(summary['rank:rise'], {'n': 1, 'avg_r30': 3.0, 'avg_r_close': -2.0, 'up_share': 0.0,
                                                'would_buy': {'n': 1, 'avg_r30': 3.0, 'avg_r_close': -2.0, 'up_share': 0.0}})
        self.assertEqual(summary['rank:trading_value']['n'], 2)
        self.assertEqual(summary['rank:trading_value']['avg_r_close'], -1.0)
        self.assertEqual(summary['rank:trading_value']['would_buy']['n'], 1)  # 200010 was chasing (2.94% over VWAP)


class MoversHookTests(unittest.TestCase):
    def test_a_failing_shadow_round_never_breaks_the_movers_scan(self):
        import market_movers
        import tempfile
        logs, root = [], Path(tempfile.mkdtemp())
        with patch.object(ic, 'observe', side_effect=sqlite3.OperationalError('locked')), patch.object(market_movers, 'ROOT', root):
            market_movers.shadow_round(Reader({}), UNIVERSE, logs.append)
        self.assertEqual([json.loads(line) for line in logs], [{'issue_candidates': {'recorded': 0, 'errors': {'round': 'OperationalError'}}}])
        status = json.loads((root / 'shadow-status.json').read_text())  # the audit's heartbeat (shadow.silent / source_error)
        self.assertEqual(status['errors'], {'round': 'OperationalError'})
        self.assertTrue(status['time'])


if __name__ == '__main__':
    unittest.main()
