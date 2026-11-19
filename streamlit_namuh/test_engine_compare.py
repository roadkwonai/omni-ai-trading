import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import engine_compare as ec


def ledger(folder, fills, links=(), events=()):
    db = sqlite3.connect(Path(folder) / 'orders.sqlite3')
    db.execute('CREATE TABLE fills (order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
    db.execute('CREATE TABLE decision_order_links (order_id TEXT PRIMARY KEY, cycle_id TEXT)')
    db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', fills)
    db.executemany('INSERT INTO decision_order_links VALUES (?,?)', links)
    db.commit()
    db.close()
    (Path(folder) / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))


C1, C2, C3 = 'a' * 32, 'b' * 32, 'c' * 32


class JournalTests(unittest.TestCase):
    def test_fills_carry_cost_results_and_the_decision_of_their_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger(tmp, [('o1', '036540', 'BUY', 100, 9660.0, '2026-11-13T09:53:46+09:00'),
                         ('o2', '036540', 'BUY', 100, 9548.0, '2026-11-13T10:05:36+09:00'),
                         ('o3', '036540', 'SELL', 100, 9830.0, '2026-11-13T11:00:26+09:00'),
                         ('o4', '036540', 'SELL', 100, 9500.0, '2026-11-14T09:30:00+09:00')],
                   [('o1', C1), ('o2', C2), ('o3', C3)],
                   [{'event': 'watchlist_strength', 'cycle_id': C1, 'confidence': 0.19, 'change_pct': 10.06},
                    {'event': 'v2_entry_gate', 'cycle_id': C1, 'source': 'watchlist_strength', 'permitted': True},
                    {'event': 'split_add', 'cycle_id': C2, 'tranche': 2, 'qty': 100},
                    {'event': 'split_take_profit', 'cycle_id': C3},
                    {'event': 'sell_submitted', 'cycle_id': C3, 'trigger': 'take_profit'}])
            rows = ec.journal(Path(tmp) / 'orders.sqlite3', Path(tmp) / 'events.jsonl')
        self.assertEqual([r['path'] for r in rows], ['관심종목 강도', '분할 추가 2차', '분할 익절', '확인 불가'])
        self.assertIn('Jev 확률 0.19', rows[0]['basis'])
        self.assertEqual((round(rows[2]['pnl']), rows[2]['return_pct']), (22600, 2.35))
        day = ec.summary(ec.in_period(rows, '2026-11-13'))
        self.assertEqual((day['sells'], day['wins'], day['pnl'], day['return_pct']), (1, 1, 22600, 2.35))
        total = ec.summary(rows)
        self.assertEqual((total['sells'], total['losses'], total['pnl']), (2, 1, 22600 - 10400))
        self.assertEqual(ec.positions(rows), {})
        self.assertEqual(ec.positions(rows[:3])['036540']['qty'], 100)

    def test_vibe_buys_cite_the_advice_that_ordered_them(self):
        advice = [{'time': '2026-11-12T14:00:00+09:00', 'scores': {'251340': {'score': 0.5, 'evidence': '코스닥 약세'}},
                   'decisions': [{'action': 'BUY', 'symbol': '251340'}]}]
        with tempfile.TemporaryDirectory() as tmp:
            ledger(tmp, [('v1', '251340', 'BUY', 526, 2850.0, '2026-11-12T14:02:25+09:00')], [('v1', C1)],
                   [{'event': 'submitted', 'cycle_id': C1, 'source': 'vibe-trading'}])
            rows = ec.journal(Path(tmp) / 'orders.sqlite3', Path(tmp) / 'events.jsonl', 'vibe', advice)
        self.assertEqual((rows[0]['path'], rows[0]['basis']), ('vibe 점수', 'vibe 점수 +0.50 · 코스닥 약세'))
        self.assertEqual(ec.journal(Path('/nonexistent/orders.sqlite3')), [])

    def test_opinions_put_both_engines_on_one_row_per_symbol(self):
        events = [{'event': 'jev_shadow', 'time': 't', 'jev': {'candidate': {'choice': 'INV', 'confidence': 0.34,
                                                                             'probabilities': {'WAIT': 0.3, 'INV': 0.6, '005930': 0.1}}}},
                  {'event': 'jev_holding_decision', 'symbol': '005930', 'choice': 'SELL', 'confidence': 0.36}]
        advice = {'scores': {'114800': {'score': 0.6, 'evidence': 'e'}, '005930': {'score': -0.4}},
                  'technical_basic': {'114800': {'vote': 1}}}
        candidate, rows = ec.opinions(events, advice)
        self.assertEqual(candidate['choice'], 'INV')
        self.assertEqual((rows['114800']['jev_prob'], rows['114800']['vibe_score'], rows['114800']['vote']), (0.6, 0.6, 1))
        self.assertEqual(rows['005930']['jev_holding'], 'SELL 0.36')


class LiveJournalTests(unittest.TestCase):
    def test_live_mirror_trades_keep_the_engine_reason_and_their_own_results(self):
        from unittest import mock
        days = {'2026-11-12': [{'time': '2026-11-12T10:00:00+09:00', 'symbol': '005930', 'side': 'BUY', 'qty': 1, 'price': 212000, 'reason': '모의 원장 매수'}],
                '2026-11-13': [{'time': '2026-11-13T10:00:00+09:00', 'symbol': '005930', 'side': 'SELL', 'qty': 1, 'price': 208000, 'reason': '2% 손절'}]}
        engine = [{'time': '2026-11-12T10:00:00+09:00', 'symbol': '005930', 'side': 'BUY', 'path': '관심종목 강도', 'basis': 'Jev 확률 0.49'}]
        with mock.patch('nh_live_shadow.account_shadow', side_effect=lambda record, events, day: {'trades': days.get(day, [])}):
            rows = ec.live_journal({}, engine, '2026-11-13')
        self.assertEqual([(r['path'], r['basis']) for r in rows], [('관심종목 강도', 'Jev 확률 0.49'), ('2% 손절', '')])
        self.assertEqual((rows[1]['pnl'], rows[1]['return_pct'], rows[1]['after']), (-4000, -1.89, 0))


class RealJournalTests(unittest.TestCase):
    def test_broker_fills_take_the_engine_reason_and_count_only_known_cost(self):
        engine = [{'time': '2026-11-14T09:50:28+09:00', 'symbol': '005930', 'side': 'BUY', 'path': '관심종목 강도', 'basis': 'Jev 확률 0.49'}]
        fills = [{'time': '2026-11-14T09:30:00+09:00', 'symbol': '114800', 'side': 'SELL', 'qty': 1, 'price': 1250.0},
                 {'time': '2026-11-14T09:51:10+09:00', 'symbol': '005930', 'side': 'BUY', 'qty': 1, 'price': 213000.0},
                 {'time': '2026-11-14T11:00:00+09:00', 'symbol': '005930', 'side': 'SELL', 'qty': 1, 'price': 215000.0}]
        rows = ec.real_journal(fills, engine)
        self.assertEqual(rows[0]['pnl'], None)  # bought before recording: unknown cost
        self.assertEqual(rows[0]['path'], '엔진 거래와 연결 안 됨(수동·기타 주문)')
        self.assertEqual((rows[1]['path'], rows[1]['basis'], rows[1]['source']), ('관심종목 강도', 'Jev 확률 0.49', '실체결'))
        self.assertEqual((rows[2]['pnl'], rows[2]['return_pct']), (2000.0, 0.94))


class EpisodeTests(unittest.TestCase):
    def test_each_holding_runs_from_first_buy_to_flat(self):
        row = lambda t, side, after, pnl=None, cost=None, path='p': {'time': t, 'symbol': '036540', 'side': side, 'after': after,
                                                                    'pnl': pnl, 'cost': cost, 'path': path}
        rows = [row('09:53', 'BUY', 100, path='관심종목 강도'), row('10:05', 'BUY', 200), row('11:00', 'SELL', 100, 22600, 960400, '분할 익절'),
                row('11:17', 'SELL', 0, 12000, 960400, '추적 손절'), row('13:00', 'BUY', 50)]
        done, held = ec.episodes(rows)
        self.assertEqual((done['open'], done['close'], done['qty'], done['entry'], done['exit']), ('09:53', '11:17', 200, '관심종목 강도', '추적 손절'))
        self.assertEqual((done['pnl'], done['return_pct']), (34600, 1.8))
        self.assertEqual((held['close'], held['return_pct']), (None, None))



class ApplyFillTests(unittest.TestCase):
    def test_average_cost_partial_and_uncovered_sales(self):
        held, avg, *_ = ec.apply_fill(0, 0.0, 'BUY', 10, 100)
        held, avg, *_ = ec.apply_fill(held, avg, 'BUY', 10, 110)
        self.assertEqual((held, avg), (20, 105.0))
        self.assertEqual(ec.apply_fill(held, avg, 'SELL', 5, 115), (15, 105.0, 525.0, 50.0, 9.52))
        self.assertEqual(ec.apply_fill(5, 105.0, 'SELL', 8, 100), (0, 0.0, 525.0, -25.0, -4.76))  # only 5 covered
        self.assertEqual(ec.apply_fill(0, 0.0, 'SELL', 3, 100), (0, 0.0, None, None, None))


class ReadEventsCacheTests(unittest.TestCase):
    def test_only_the_newest_parse_of_a_file_is_kept(self):
        import os, tempfile, time
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'events.jsonl')
            with open(path, 'w') as f:
                f.write('{"a": 1}\n')
            self.assertEqual(ec.read_events(path, 10), [{'a': 1}])
            time.sleep(0.01)
            with open(path, 'a') as f:
                f.write('{"a": 2}\n')
            self.assertEqual(ec.read_events(path, 10), [{'a': 1}, {'a': 2}])
            self.assertEqual([key for key in ec._TAILS if key == path], [path])  # one entry per file, not per limit

    def test_one_parse_serves_every_limit_and_each_gets_its_own_tail(self):
        # P3: events.jsonl (31 MB) was parsed once per (path, limit) pair; 5000 and 20000 parsed it twice per change
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'events.jsonl')
            with open(path, 'w') as f:
                f.write(''.join(f'{{"n": {i}}}\n' for i in range(30)))
            self.assertEqual(ec.read_events(path, 20), [{'n': i} for i in range(10, 30)])
            with patch('builtins.open', side_effect=AssertionError('parsed again')):
                self.assertEqual(ec.read_events(path, 3), [{'n': 27}, {'n': 28}, {'n': 29}])
            self.assertEqual(ec.read_events(path, 50), [{'n': i} for i in range(30)])

if __name__ == '__main__':
    unittest.main()


def test_one_corrupt_line_does_not_hide_the_other_events(tmp_path):
    # review4 D-3: a runner killed mid-write leaves half a line; recent_events went [] and every events view was blank
    good = [{'time': f'2026-11-18T10:{i:02d}:00+09:00', 'event': 'decision', 'n': i} for i in range(50)]
    lines = [json.dumps(e) for e in good]
    lines.insert(25, lines[0][:40])
    path = tmp_path / 'events.jsonl'
    path.write_text('\n'.join(lines) + '\n')
    assert ec.recent_events(path, 3000) == good
    assert ec.bad_lines(path) == 1


def test_an_append_parses_only_the_new_lines_and_a_rewrite_starts_over(tmp_path):
    # review4 D-7: every runner append (about every 17 s) re-parsed the whole 39 MB events.jsonl on the next rerun
    import os
    path = tmp_path / 'events.jsonl'
    path.write_text(''.join(json.dumps({'n': i}) + '\n' for i in range(30)))
    assert ec.read_events(path, 5) == [{'n': i} for i in range(25, 30)]
    with open(path, 'a') as f:
        f.write(json.dumps({'n': 30}) + '\n{"n": 3')  # the runner is still writing the next line
    with patch.object(ec.json, 'loads', wraps=json.loads) as loads:
        assert ec.read_events(path, 3) == [{'n': 28}, {'n': 29}, {'n': 30}]
    assert loads.call_count <= 2  # the appended lines, not the 30 before them
    with open(path, 'a') as f:
        f.write('1}\n')
    assert ec.read_events(path, 2) == [{'n': 30}, {'n': 31}] and ec.bad_lines(path) == 0  # unfinished is not corrupt
    path.write_text(json.dumps({'n': 'new'}) + '\n')  # truncated and rewritten in place
    assert ec.read_events(path, 5) == [{'n': 'new'}]
    rotated = tmp_path / 'next.jsonl'
    rotated.write_text(''.join(json.dumps({'m': i}) + '\n' for i in range(3)))
    os.replace(rotated, path)  # a new file under the same name
    assert ec.read_events(path, 5) == [{'m': 0}, {'m': 1}, {'m': 2}]
