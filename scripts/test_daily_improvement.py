import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_improvement as d


class DailyImprovementTests(unittest.TestCase):
    def test_entry_study_preserves_exploratory_limits_and_reports_missing_data(self):
        evidence = {'day': '2026-11-16', 'mode': 'read_only_exploratory',
                    'hypothesis_selection': '사후 선정', 'groups': {}, 'decisions': []}
        with patch('vibe_entry_study.load_and_analyze', return_value=evidence) as study:
            result = d.vibe_entry_study(Path('/tmp/research'), '2026-11-16')
        self.assertEqual(result, {'status': 'ok', **evidence})
        study.assert_called_once_with(Path('/tmp/research'), '2026-11-16')
        with patch('vibe_entry_study.load_and_analyze', side_effect=sqlite3.OperationalError):
            self.assertEqual(d.vibe_entry_study(Path('/tmp/research'), '2026-11-16')['status'], 'unavailable')

    def test_call_stats_exclude_legacy_vibe_without_changing_records(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                db.execute('CREATE TABLE decision_outcomes (key TEXT, time TEXT, engine, kind, direction, r30, b30, status)')
                db.executemany('INSERT INTO decision_outcomes VALUES (?,?,?,?,?,?,?,?)', [
                    ('jev:t', '2026-11-16T10:00:00+09:00', 'jev', 'entry', 1, 0.5, 0.1, 'settled'),
                    ('vibe:old:005930', '2026-11-16T10:00:00+09:00', 'vibe', 'score', 1, 10.0, 0.0, 'settled'),
                    ('vibe_available:new:005930', '2026-11-16T10:05:00+09:00', 'vibe', 'score', 1, -1.0, 0.0, 'settled'),
                ])
            calls = d.jev_calls(root, '2026-11-16')
            self.assertEqual((calls['jev entry']['n'], calls['vibe score']['n']), (1, 1))
            self.assertEqual(calls['vibe score']['avg_vs_kodex200_30m_pct'], -1.0)
            self.assertEqual(d.unverified_calls(root, '2026-11-16'), 1)
            with sqlite3.connect(root / 'market-research.sqlite3') as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM decision_outcomes').fetchone()[0], 3)

    def test_reentries_count_only_trips_after_a_closed_one(self):
        trips = [{'symbol': 'A', 'open': '09:50:00', 'close': '11:00:00', 'pnl': 100},
                 {'symbol': 'A', 'open': '12:30:00', 'close': '12:40:00', 'pnl': -50},
                 {'symbol': 'B', 'open': '13:00:00', 'close': None, 'pnl': 0},
                 {'symbol': 'A', 'open': '13:20:00', 'close': '13:30:00', 'pnl': -70}]
        self.assertEqual({k: v for k, v in d.reentries(trips).items() if k != 'trips'}, {'n': 2, 'realized_krw': -120})

    def test_stats_group_by_route(self):
        trips = [{'entry_route': 'x', 'pnl': 10, 'return_pct': 1.0}, {'entry_route': 'x', 'pnl': -4, 'return_pct': -0.5},
                 {'entry_route': None, 'pnl': 1, 'return_pct': 0.1}]
        self.assertEqual(d._stats(trips, 'entry_route')['x'], {'n': 2, 'wins': 1, 'realized_krw': 6, 'avg_return_pct': 0.25})
        self.assertIn('기록 없음', d._stats(trips, 'entry_route'))

    def test_guard_limits_follow_the_day(self):
        events = [{'time': '2026-11-13T09:10:00', 'event': 'strategy_settings', 'settings': {}},
                  {'time': '2026-11-13T13:33:00', 'event': 'v2_execution_gate', 'reason': 'order_guard_refused'},
                  {'time': '2026-11-13T14:43:00', 'event': 'strategy_settings', 'settings': {'daily_buy_krw': 9}}]
        out = d.guard_usage('2026-11-13', events)
        self.assertEqual([(r['from'], r['daily_buy_krw']) for r in out['limits_over_the_day']], [('09:10:00', 6_000_000), ('14:43:00', 9)])
        self.assertEqual(out['refusal_times'], ['13:33:00'])


if __name__ == '__main__':
    unittest.main()


def test_signal_calibration_bands_and_flag(tmp_path):
    import sqlite3
    import daily_improvement as d
    db = sqlite3.connect(tmp_path / 'market-research.sqlite3')
    db.execute('CREATE TABLE decision_outcomes (key TEXT PRIMARY KEY, time, engine, kind, symbol, choice, value, direction, '
               'input_digest, price, r5, b5, r15, b15, r30, b30, r_last, b_last, status)')
    rows = []
    for n in range(40):  # 0.6~0.7 calls hit 75%, 0.8+ calls 25%: higher confidence did worse
        rows.append((f'a{n}', '2026-11-17T10:00:00+09:00', 'jev', 'entry', '114800', 'INV', 0.65, 1, 'd', 1000,
                     None, None, None, None, 0.3 if n % 4 else -0.3, 0.0, None, None, 'settled'))
        rows.append((f'b{n}', '2026-11-17T10:00:00+09:00', 'jev', 'entry', '114800', 'INV', 0.85, 1, 'd', 1000,
                     None, None, None, None, 0.3 if n % 4 == 0 else -0.3, 0.0, None, None, 'settled'))
    db.executemany('INSERT INTO decision_outcomes VALUES (' + ','.join('?' * 19) + ')', rows)
    db.commit(); db.close()
    result = d.signal_calibration(tmp_path, '2026-11-17', 10)['today']['jev entry']
    assert result['bands']['0.6~0.7']['hit_rate_30m'] == 0.75 and result['bands']['0.8 이상']['hit_rate_30m'] == 0.25
    assert result['calibrated'] is False


def test_add_sizing_measures_each_add_to_its_exit():
    import daily_improvement as d
    row = lambda t, side, qty, price: {'time': f'2026-11-17T{t}+09:00', 'symbol': '114800', 'side': side, 'qty': qty,
                                       'price': price, 'amount': qty * price}
    rows = [row('10:00:00', 'BUY', 10, 1000), row('10:05:00', 'BUY', 10, 990), row('10:30:00', 'SELL', 20, 1010)]
    out = d.add_sizing(rows, '2026-11-17', 10, {'add_size_pct': 50}, [], {})
    assert [a['to_exit_pct'] for a in out['adds']] == [2.02]  # only the second buy is an add
    assert out['today_adds'] == 1 and out['settled'] == 1 and '표본 부족' in out['hint']


def test_blocked_outcomes_settle_and_candidates(tmp_path):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import blocked_outcomes as b
    k = ZoneInfo('Asia/Seoul')
    bars = [(datetime(2026, 11, 17, h, m, tzinfo=k), 100, hi, lo, c) for h, m, hi, lo, c in
            [(10, 0, 101, 99, 100), (10, 30, 103, 100, 102), (11, 0, 104, 98, 99), (14, 55, 100, 97, 98)]]
    r = b.settle('2026-11-17T10:05:00+09:00', bars)  # bars are stamped at their start: 10:00 closes at 10:05 (Review 4 C6)
    assert (r['price'], r['r30'], r['r_last'], r['mfe'], r['mae']) == (100, 2.0, -2.0, 4.0, -3.0)
    events = [{'time': '2026-11-17T10:00:00+09:00', 'event': 'strength_shadow', 'symbol': '084370', 'blocked': 'trend_not_rising'},
              {'time': '2026-11-17T10:01:00+09:00', 'event': 'strength_shadow', 'symbol': '084370', 'blocked': 'trend_not_rising'},
              {'time': '2026-11-17T10:02:00+09:00', 'event': 'v2_entry_gate', 'ipo_reason': 'below_base'}]
    (tmp_path / 'new-listings.json').write_text(json.dumps({'rows': [{'code': '493260', 'listed': '20261117'},
                                                                     {'code': '123456', 'listed': '20261101'}]}))
    rows = b.candidates(tmp_path, '2026-11-17', events, {'ipo_start': '09:20', 'ipo_days': 2})
    assert rows == [('strength', '084370', '2026-11-17T10:00:00+09:00', 'trend_not_rising', None),
                    ('ipo', '493260', '2026-11-17T09:20:00+09:00', 'below_base', 0)]  # once per name; old listing out


def test_a_morning_crash_belongs_to_the_day_it_happened(tmp_path):
    """Review 4 (2026-11-19): the real 11-17 -> 11-18 log; the 11-18 08:12 start failed on the NH account list and was
    reported under 11-17 (the anchor kept the previous day's last status)."""
    (tmp_path / 'com.vibetrading.shared-etf-paper.log').write_text('\n'.join([
        '{"status": "wait", "time": "2026-11-17T15:17:32.278449+09:00"}',
        'Traceback (most recent call last):',
        "    raise ValueError('paper account list unavailable')",
        'ValueError: paper account list unavailable',
        '{"status": "wait", "time": "2026-11-18T09:00:08.538815+09:00"}',
        'Traceback (most recent call last):',
        'RuntimeError: midday',
        '{"status": "wait", "time": "2026-11-18T10:00:00+09:00"}']))
    assert d.runner_health([], tmp_path, '2026-11-17')['crashes'] == []
    assert d.runner_health([], tmp_path, '2026-11-18')['crashes'] == [
        '09:00:08 이전 · ValueError: paper account list unavailable', '09:00:08 이후 · RuntimeError: midday']


def test_undersized_buys_leave_out_buys_an_approved_rule_sized_down():
    buy = lambda t, code, amount: {'time': f'2026-11-17T{t}+09:00', 'symbol': code, 'amount': amount, 'path': 'Jev 판단'}  # noqa: E731
    sent = lambda t, code, **kw: {'time': f'2026-11-17T{t}+09:00', 'event': 'submitted', 'symbol': code, **kw}  # noqa: E731
    events = [sent('09:59:58', 'A', sizing={'factor': 0.5}), sent('10:09:58', 'B', add_reason='confidence_up'),
              sent('10:19:58', 'C', careful_reentry={'size': 50.0}), sent('10:29:58', 'D', add_reason='first')]
    buys = [buy('10:00:00', 'A', 100_000), buy('10:10:00', 'B', 100_000), buy('10:20:00', 'C', 100_000),
            buy('10:30:00', 'D', 100_000), buy('10:40:00', 'E', 900_000)]
    assert d.undersized_buys(buys, 1_000_000, events) == ['D 10:30:00 100,000원']


def test_v2_samples_use_the_runner_settings():
    """4th review C11: the samples were judged with the hard-coded 8/5/5/4 even when 선별 v2 settings were changed."""
    from unittest.mock import patch
    import daily_improvement as di
    import oversold_cases
    import oversold_replay
    seen = {}

    def fake(cases, since=None, settings=None):
        seen['settings'] = settings
        return []
    with patch.object(oversold_cases, 'load', return_value=[]), patch.object(oversold_replay, 'v2_samples', fake):
        di.oversold_section('2026-11-19', [], {'oversold_v2_index_drop_pct': 12})
    assert seen['settings'] == {'oversold_v2_index_drop_pct': 12}
