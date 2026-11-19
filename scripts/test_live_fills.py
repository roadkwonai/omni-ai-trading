import tempfile
import unittest

import live_fills as lf


def row(no, side, qty, price, at='093015', filled=None):
    return {'itg_orr_no': no, 'iem_cd': '005930', 'sby_dit_cd_nm': side, 'orr_qty': qty, 'tot_cns_qty': filled if filled is not None else qty,
            'cns_avg_uit_pr': price, 'orr_tmd': at}


class LiveFillsTests(unittest.TestCase):
    def test_filled_orders_are_kept_and_partial_fills_update_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            history = {'_day': '20261113', 'Output_0': [row(11, '현금매수', 2, '213,000', filled=1), row(12, '현금매도', 1, 0, filled=0),
                                                        {'itg_orr_no': 'x'}]}
            self.assertEqual(lf.record(d, 'live-1', history), 1)
            history['Output_0'][0] = row(11, '현금매수', 2, 212500)
            lf.record(d, 'live-1', history)
            lf.record(d, 'live-2', {'_day': '20261113', 'Output_0': [row(21, '현금매도', 1, 9800, '110000')]})
            self.assertEqual(lf.rows(d, 'live-1'), [{'time': '2026-11-13T09:30:15+09:00', 'symbol': '005930', 'side': 'BUY',
                                                     'qty': 2, 'price': 212500.0, 'order_no': 11}])
            self.assertEqual(lf.rows(d, 'live-2')[0]['side'], 'SELL')
            self.assertEqual(lf.rows(d, 'live-3'), [])
        self.assertEqual(lf.record('/nonexistent/dir', 'live-1', {'_day': '20261113', 'Output_0': [row(1, '매수', 1, 1)]}), 0)


if __name__ == '__main__':
    unittest.main()


def test_record_balance_keeps_totals_every_five_minutes(tmp_path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from live_fills import balances, record_balance
    at = datetime(2026, 11, 18, 9, 40, tzinfo=ZoneInfo('Asia/Seoul'))
    page = {'Output_0': {'orr_pbl_amt1': '0', 'tot_aet_amt': '1,000,000', 'tot_byn_amt': '990000', 'tot_eal_amt': '1000000'}}
    assert record_balance(tmp_path, 'live-1', page, at)  # all invested: cash 0 is a real balance
    assert not record_balance(tmp_path, 'live-1', page, at + timedelta(minutes=2))  # within 5 minutes
    no_total = {'Output_0': {'orr_pbl_amt1': '400000', 'tot_eal_amt': '610000'}}
    assert record_balance(tmp_path, 'live-1', no_total, at + timedelta(minutes=6))  # falls back to cash + evaluation
    assert not record_balance(tmp_path, 'live-1', {'Output_0': {}}, at + timedelta(minutes=20))  # nothing to record
    assert not record_balance(tmp_path, 'live-1', None, at + timedelta(minutes=30))  # never raises
    rows = balances(tmp_path, 'live-1', '2026-11-18')
    assert [r['total_assets'] for r in rows] == [1000000, 1010000] and rows[0]['cash'] == 0
    assert balances(tmp_path, 'live-2', '2026-11-18') == []


def test_rows_take_the_submission_time_when_nh_gives_no_clock(tmp_path):
    import json
    import sqlite3
    from live_fills import NAME, SCHEMA, rows
    with sqlite3.connect(tmp_path / NAME) as db:
        db.execute(SCHEMA)
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', [
            ('live-1', '20261118', 20, '005930', 'SELL', 1, 195100, '2026-11-18T00:00:00+09:00', 'x'),
            ('live-1', '20261118', 10, '005930', 'BUY', 1, 197200, '2026-11-18T00:00:00+09:00', 'x'),
            ('live-1', '20261118', 30, '114800', 'BUY', 5, 1354, '2026-11-18T00:00:00+09:00', 'x')])  # never logged
    with sqlite3.connect(tmp_path / 'live-orders.sqlite3') as db:
        db.execute('CREATE TABLE live_intents (fingerprint TEXT, day TEXT, order_number INTEGER)')
        db.executemany('INSERT INTO live_intents VALUES (?,?,?)', [('a', '20261118', 10), ('b', '20261118', 20)])
    (tmp_path / 'live-follow.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in [
        {'time': '2026-11-18T09:58:17+09:00', 'account': 'live-1', 'action': 'BUY', 'intent': 'a'},
        {'time': '2026-11-18T10:10:14+09:00', 'account': 'live-1', 'action': 'SELL', 'intent': 'b'},
        {'time': '2026-11-18T10:10:14+09:00', 'account': 'live-2', 'action': 'SELL', 'intent': 'a'}]))
    got = [(r['order_no'], r['time'][11:19]) for r in rows(tmp_path, 'live-1')]
    assert got == [(10, '09:58:17'), (20, '10:10:14'), (30, '00:00:00')]  # order-number order; unknown stays as stored


def test_rows_read_each_executor_log_once_for_any_number_of_days(tmp_path, monkeypatch):
    """Review 3 M7: every recorded live fill has no clock, so one log read per day made rows() quadratic in days.
    Order numbers repeat across days (they restart daily): a day's time must come from that day's intent only."""
    import json
    import sqlite3
    from pathlib import Path
    from live_fills import NAME, SCHEMA, order_times, rows
    days = ['2026-11-16', '2026-11-17', '2026-11-18']
    with sqlite3.connect(tmp_path / NAME) as db:
        db.execute(SCHEMA)
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', [
            ('live-1', d.replace('-', ''), 10, '005930', 'BUY', 1, 197200, f'{d}T00:00:00+09:00', 'x') for d in days])
    with sqlite3.connect(tmp_path / 'live-orders.sqlite3') as db:
        db.execute('CREATE TABLE live_intents (fingerprint TEXT, day TEXT, order_number INTEGER)')
        db.executemany('INSERT INTO live_intents VALUES (?,?,?)', [(f'fp{i}', d.replace('-', ''), 10) for i, d in enumerate(days)])
    (tmp_path / 'live-follow.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in [
        {'time': f'{d}T09:0{i}:00+09:00', 'account': 'live-1', 'action': 'BUY', 'intent': f'fp{i}'} for i, d in enumerate(days)]))
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **k: reads.append(self.name) or real(self, *a, **k))
    got = [(r['time'][:10], r['time'][11:19]) for r in rows(tmp_path, 'live-1')]
    assert got == [('2026-11-16', '09:00:00'), ('2026-11-17', '09:01:00'), ('2026-11-18', '09:02:00')]
    assert reads.count('live-follow.jsonl') == 1
    assert order_times(tmp_path, 'live-1', '2026-11-17') == {10: '2026-11-17T09:01:00+09:00'}  # the dashboard's one-day form
