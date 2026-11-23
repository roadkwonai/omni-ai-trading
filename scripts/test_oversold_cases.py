import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import oversold_cases as oc


def test_versioned_refresh_archives_old_result_and_keeps_other_replays(tmp_path):
    import json
    import sqlite3
    import oversold_replay

    db = tmp_path / 'cases.sqlite3'
    case = {'kind': 'stock', 'replay': {'v2_plan': {'ret': 7}, 'close_1d': {'ret': 3}}}
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE cases (key TEXT PRIMARY KEY, code TEXT, day TEXT, kind TEXT, data TEXT)')
        conn.execute('CREATE TABLE series (symbol TEXT, day TEXT, o REAL, h REAL, l REAL, c REAL, v REAL)')
        conn.execute('INSERT INTO cases VALUES (?,?,?,?,?)', ('k', '005930', '2026-11-02', 'stock', json.dumps(case)))
        for day in range(1, 7):
            conn.execute('INSERT INTO series VALUES (?,?,?,?,?,?,?)',
                         (oc.symbol_of('005930'), f'2026-11-{day:02d}', 100, 101, 99, 100, 100))
    result = oc.refresh_v2_versioned(db, {'oversold_v2_stop_pct': 2})
    with sqlite3.connect(db) as conn:
        saved = json.loads(conn.execute('SELECT data FROM cases').fetchone()[0])
        previous, updated = conn.execute('SELECT previous,updated FROM v2_replay_history').fetchone()
    assert json.loads(previous) == {'ret': 7}
    assert saved['replay']['close_1d'] == {'ret': 3}
    assert json.loads(updated) == saved['replay']['v2_plan']
    assert saved['replay']['v2_plan']['rule_version'] == oversold_replay.V2_RULE_VERSION
    assert saved['replay']['v2_plan']['settings']['oversold_v2_stop_pct'] == 2
    assert result['updated'] == 1

KST = ZoneInfo('Asia/Seoul')
START = date(2026, 9, 1)


def series(closes, lows=None):
    """Daily bars (dt, o, h, l, c, v) on consecutive days from closes; lows default to 1% under the close."""
    out = []
    for n, c in enumerate(closes):
        lo = (lows or {}).get(n, c * 0.99)
        out.append((datetime.combine(START + timedelta(days=n), datetime.min.time(), KST), c, c * 1.01, lo, c, 1000))
    return out


def case(day, drop, r5, after='rebound', kind='stock', code='005930', **kw):
    return {'code': code, 'name': 'x', 'kind': kind, 'day': day, 'drop_pct': drop, 'atr_mult': -drop / 3, 'r1': 1.0,
            'r3': r5 / 2, 'r5': r5, 'after': after, **kw}


class FeatureTests(unittest.TestCase):
    def test_features_and_outcomes_of_a_fall(self):
        closes = [118] * 30 + [118, 116, 114, 120, 121, 122, 123, 124]  # day 30 dips to 110 and closes flat
        daily = series(closes, {30: 110.0})
        f = oc.daily_features(daily, 30, {daily[30][0].date(): 2.0})
        self.assertEqual((f['drop_pct'], f['atr_mult'], f['down_streak']), (-6.78, 3.39, 0))
        self.assertEqual(f['close_pct'], 0.0)
        o = oc.outcomes(daily, 30)
        self.assertEqual((o['r1'], o['r3'], o['low_broken_5'], o['after']), (-1.69, 1.69, False, 'rebound'))
        self.assertEqual(o['days_to_prev_close'], 3)  # 116, 114, then 120 >= 118

    def test_further_fall_is_a_low_broken_by_3_percent(self):
        closes = [100] * 25 + [94, 93, 92, 91, 90, 89, 88]
        daily = series(closes, {25: 93.0})
        self.assertEqual(oc.outcomes(daily, 25)['after'], 'further_fall')
        self.assertEqual(oc.outcomes(daily, 29)['after'], 'pending')  # fewer than 5 days after

    def test_down_streak_counts_lower_closes_before(self):
        daily = series([100] * 22 + [99, 98, 97, 90])
        self.assertEqual(oc.daily_features(daily, 25, {})['down_streak'], 3)


class LookupTests(unittest.TestCase):
    CASES = ([case(f'2020-01-{n:02d}', -6 - n * 0.1, 4.0) for n in range(1, 13)]
             + [case(f'2021-01-{n:02d}', -14 - n * 0.1, -6.0, 'further_fall') for n in range(1, 13)]
             + [case('2022-01-01', -7, 9.0, kind='etf', code='069500')])

    def test_similar_finds_the_nearest_falls_of_the_same_kind(self):
        shallow = oc.similar('005930', {'drop_pct': -6.5}, k=10, cases=self.CASES)
        deep = oc.similar('005930', {'drop_pct': -15.0}, k=10, cases=self.CASES)
        self.assertEqual((shallow['n'], shallow['r5_med'], shallow['further_fall_pct']), (10, 4.0, 0))
        self.assertEqual((deep['r5_med'], deep['further_fall_pct']), (-6.0, 100))
        self.assertEqual(oc.similar('069500', {'drop_pct': -7}, cases=self.CASES)['n'], 0)  # too few ETF cases

    def test_stats_and_breakdown(self):
        rows = dict(oc.breakdown(self.CASES, 'stock'))
        self.assertEqual(rows['전체']['n'], 24)
        self.assertEqual((rows['−5~7%']['n'], rows['−5~7%']['win_r5']), (9, 100))  # -6.1 .. -6.9
        self.assertEqual(rows['−10~15%']['further_fall'], 100)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'cases.sqlite3'
        today = datetime.now(KST).date()
        prev = series([100.0] * 30)
        prev = [(datetime.combine(today - timedelta(days=30 - n), datetime.min.time(), KST), *b[1:]) for n, b in enumerate(prev)]
        oc.write(self.db, LookupTests.CASES, {'005930.KS': prev, '069500.KS': prev, '^IXIC': prev})
        oc._CACHE.clear()
        last = prev[-1][0].date()  # calendar days here: the test must not depend on the weekday it runs on
        self.session = patch.object(oc, 'previous_session', return_value=last)
        self.session.start()
        self.addCleanup(self.session.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_calm_day_adds_nothing_and_a_fall_adds_its_past(self):
        self.assertEqual(oc.context({'005930': -1.0}, db=self.db), {})
        out = oc.context({'005930': -2.0, '114800': -9.0}, lows={'005930': -6.4}, db=self.db)
        self.assertEqual(list(out), ['005930'])  # inverse ETFs fall when the market rises: never listed
        self.assertEqual(out['005930']['low_pct'], -6.4)
        self.assertEqual(out['005930']['past_similar_falls']['n'], 24)  # k=25 over a pool of 24

    def test_bars_context_uses_the_stored_previous_close(self):
        bars = {'K200': [{'bsop_time': '100100', 'stck_prpr': '98', 'stck_lwpr': '97'}],
                '005930': [{'bsop_time': '100100', 'stck_prpr': '95', 'stck_lwpr': '93.5'}]}
        out = oc.bars_context(bars, db=self.db)
        self.assertIn('about', out)
        self.assertEqual(out['falls']['005930']['low_pct'], -6.5)
        self.assertEqual(out['falls']['005930']['market_low_pct'], -3.0)  # KODEX 200's low as the KOSPI proxy
        self.assertNotIn('069500', out['falls'])  # ETF pool has one case: no reference
        self.assertEqual(oc.bars_context({'005930': [{'bad': 1}]}, db=self.db), {})
        self.assertEqual(oc.bars_context(bars, db=Path(self.tmp.name) / 'missing.sqlite3'), {})



class WriteTests(unittest.TestCase):
    def test_replace_drops_stale_cases_and_nonpositive_bars(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'c.sqlite3'
            bad = (datetime(2020, 4, 20, tzinfo=KST), 18.0, 18.0, -40.0, -37.6, 0)
            oc.write(db, [case('2020-04-20', -320.0, 5.0, code='CL=F'), case('2020-04-17', -12.0, 5.0, code='CL=F')],
                     {'CL=F': [bad, (datetime(2020, 4, 17, tzinfo=KST), 20.0, 21.0, 17.0, 18.0, 0)]})
            oc.write(db, [case('2020-04-17', -12.0, 5.0, code='CL=F')], {}, replace=({'CL=F'}, ''))
            oc._CACHE.clear()
            self.assertEqual([c['day'] for c in oc.load(db)], ['2020-04-17'])
            import sqlite3
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute('SELECT day FROM series').fetchall(), [('2020-04-17',)])

    def test_market_of_an_unknown_code_is_never_guessed(self):
        self.assertEqual((oc.market_of_code('005930'), oc.market_of_code('196170'), oc.market_of_code('229200')), ('kospi', 'kosdaq', 'kosdaq'))
        self.assertIsNone(oc.market_of_code('900100'))
        self.assertEqual((oc.market_of_code('900100', 'KOSDAQ150'), oc.market_of_code('900100', 'KOSPI200')), ('kosdaq', 'kospi'))
        from datetime import date
        with patch('kr_swing_v5_daily_data.krx_holidays', return_value=set()):
            oc._SESSIONS.clear()
            self.assertEqual(oc.previous_session(date(2026, 11, 16)), date(2026, 11, 13))  # Monday -> Friday
        oc._SESSIONS.clear()

    def test_yahoo_symbols_use_the_checked_market_else_try_both(self):
        self.assertEqual(oc.yahoo_symbols('207940'), ['207940.KS'])  # starts with 2 but trades on KOSPI
        self.assertEqual(oc.yahoo_symbols('196170'), ['196170.KQ'])
        self.assertEqual(oc.yahoo_symbols('^IXIC'), ['^IXIC'])
        self.assertEqual(oc.yahoo_symbols('900100'), ['900100.KS', '900100.KQ'])  # unknown: never guessed from the digits


NY = ZoneInfo('America/New_York')
US = [(datetime(2026, 11, d, tzinfo=NY), 1, 1, 1, c, 0) for d, c in
      ((12, 25400.0), (13, 25136.27), (16, 24509.76), (17, 24328.38), (18, 24700.0))]  # 11/18 US closes 06:00 KST 11/19


def series_db(path, rows):
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE series (symbol TEXT, day TEXT, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (symbol, day))')
        db.executemany('INSERT INTO series VALUES (?,?,?,?,?,?,?)', rows)


class SeriesTimingTests(unittest.TestCase):
    """Review 4 C1: the after-close collect runs at 15:25, before the closing auction and the US session."""

    def test_live_nasdaq_is_the_us_session_the_cases_use(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'c.sqlite3'
            series_db(path, [('^IXIC', b[0].date().isoformat(), 1, 1, 1, b[4], 0) for b in US[:4]])  # the 15:25 store of 11/18
            on = lambda day: oc.live_features('005930', -6.0, today=day, db=path).get('nasdaq_prev')  # noqa: E731
            case = lambda day: round(oc.prev_session(US)(day)[0], 2)  # noqa: E731 - nasdaq_prev of a case on that KR day
            self.assertIsNone(on(date(2026, 11, 19)))  # US 11/18 not stored yet: not the 11/17 move under the 11/18 name
            self.assertEqual(on(date(2026, 11, 18)), case(date(2026, 11, 18)))
            self.assertEqual(on(date(2026, 11, 16)), case(date(2026, 11, 16)))  # Monday: the US Friday

    def test_pre_open_refresh_stores_the_final_close_and_the_us_night(self):
        final = {'^KS11': [(datetime(2026, 11, d, tzinfo=KST), 1, 1, 1, c, 0) for d, c in ((17, 4968.48), (18, 5064.24))],
                 '^IXIC': US[3:]}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'c.sqlite3'
            series_db(path, [('^KS11', '2026-11-17', 1, 1, 1, 4968.48, 0), ('^KS11', '2026-11-18', 1, 1, 1, 5054.36, 0),  # 15:25
                             ('^IXIC', '2026-11-16', 1, 1, 1, 24509.76, 0), ('^IXIC', '2026-11-17', 1, 1, 1, 24328.38, 0)])
            with patch('oversold_research.fetch', lambda symbol, interval, rng, volume=False: final.get(symbol, [])), \
                    patch('kr_swing_v5_daily_data.krx_holidays', return_value=set()):
                oc._SESSIONS.clear()
                oc.refresh_series(db=path)
                self.assertEqual(oc.prev_closes(['^KS11'], date(2026, 11, 19), path), {'^KS11': 5064.24})
                self.assertEqual(oc.live_features('^KS11', -3.0, today=date(2026, 11, 19), db=path)['nasdaq_prev'], 1.53)
            oc._SESSIONS.clear()


class KiwoomBarsTests(unittest.TestCase):
    def test_kiwoom_minutes_only_within_a_year_and_never_raise(self):
        import kiwoom_data
        bar = (datetime(2026, 3, 3, 9, tzinfo=KST), 10, 11, 9, 10, 5)
        today = date(2026, 11, 17)
        with patch.object(kiwoom_data, 'configured', return_value=True), \
             patch.object(kiwoom_data, 'day_bars', return_value={'bars': [bar], 'prev_close': 9}) as day_bars:
            self.assertEqual(oc.kiwoom_bars('005930', date(2026, 3, 3), today), [bar[:5]])  # (dt, o, h, l, c) for simulate
            self.assertEqual(oc.kiwoom_bars('005930', date(2025, 1, 2), today), [])
        self.assertEqual(day_bars.call_args.kwargs, {'adjusted': True})
        with patch.object(kiwoom_data, 'configured', return_value=True), \
             patch.object(kiwoom_data, 'day_bars', side_effect=OSError):
            self.assertEqual(oc.kiwoom_bars('005930', date(2026, 3, 3), today), [])
        self.assertEqual(oc.kiwoom_bars('005930', date(2026, 3, 3), today), [])  # no key in tests: nothing called


if __name__ == '__main__':
    unittest.main()


def test_bars_context_reads_todays_rows_when_the_previous_session_is_mixed_in():
    """11-19 opening review rows carry bsop_date; yesterday's 15:30 bar was taken as the latest (no gap shown)."""
    from datetime import date
    from unittest.mock import patch
    import oversold_cases as c
    row = lambda d, t, price: {'bsop_date': d, 'bsop_time': t, 'stck_prpr': str(price), 'stck_lwpr': str(price)}
    rows = [row('20261106', '090100', 9000), row('20261105', '153000', 10000), row('20261105', '152900', 9500)]
    seen = {}
    with patch.object(c, 'prev_closes', return_value={'069500': 10000.0}), \
         patch.object(c, 'context', side_effect=lambda changes, *a, **k: seen.update(changes=changes) or {}):
        c.bars_context({'K200': rows}, today=date(2026, 11, 6))
    assert round(seen['changes']['069500'], 2) == -10.0
