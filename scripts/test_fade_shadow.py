import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import fade_shadow as f

KST = ZoneInfo('Asia/Seoul')
DAY = '2026-11-25'
AT = lambda hhmm, s=0: datetime.fromisoformat(f'{DAY}T{hhmm}:{s:02d}+09:00')  # noqa: E731


def _bars(closes, volumes, start='10:00'):
    return [(AT(start) + timedelta(minutes=i), closes[max(0, i - 1)], c, c, c, v) for i, (c, v) in enumerate(zip(closes, volumes))]


def test_the_signals_use_only_minutes_that_had_ended():
    bars = _bars([100, 101, 102, 103, 104, 105, 106, 107, 50, 1], [10] * 8 + [999, 999])
    got = f.tape(bars, AT('10:08', 30))  # the 10:08 bar (50) is still forming
    assert got['ma5_gap_pct'] > 0 and got['pressure'] == 1.0 and got['vol_ratio'] == 1.0
    assert f.tape(bars, AT('10:07', 30)) == {}


def test_weak_tape_reads_as_fading():
    got = f.tape(_bars([100, 101, 102, 103, 104, 105, 104, 103, 102], [10, 10, 90, 90, 90, 90, 9, 9, 9]), AT('10:09'))
    assert got['vol_ratio'] == 0.1 and got['ma5_gap_pct'] < 0 and got['pressure'] == -1.0


def test_the_rest_of_a_position_is_priced_by_its_own_sales():
    fills = [('BUY', 3, 100.0, AT('10:00')), ('SELL', 2, 102.0, AT('10:10')), ('SELL', 1, 99.0, AT('10:20')),
             ('BUY', 5, 90.0, AT('11:00'))]
    assert f.rest_of_position(fills, AT('10:05'), 3) == (0, (2 * 102 + 99) / 3)
    assert f.rest_of_position(fills, AT('10:15'), 1) == (0, 99.0)
    assert f.rest_of_position(fills, AT('11:05'), 5) == (1, None)  # still held
    assert f.rest_of_position([('BUY', 3, 100.0, AT('10:00')), ('BUY', 3, 101.0, AT('10:06'))], AT('10:05'), 3) == (0, None)  # added to


def _setup(tmp_path):
    with closing(sqlite3.connect(tmp_path / 'orders.sqlite3')) as db, db:
        db.execute('CREATE TABLE fills (order_id, symbol, side, quantity, price, time)')
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [
            ('1', '053800', 'BUY', 3, 1000.0, AT('10:00').isoformat()), ('2', '053800', 'SELL', 3, 990.0, AT('10:20').isoformat())])
    looks = [('10:08', 1010), ('10:09', 1012), ('10:10', 1004)]
    rows = [{'time': AT(t).isoformat(), 'event': 'holding_quote', 'symbol': '053800', 'qty': 3, 'bid': b, 'avg': 1000} for t, b in looks]
    rows.append({'time': AT('10:10').isoformat(), 'event': 'holding_quote', 'symbol': '114800', 'qty': 3, 'bid': 5, 'avg': 4})  # an ETF
    (tmp_path / 'events.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n{broken', encoding='utf-8')
    (tmp_path / 'holding-book.jsonl').write_text(json.dumps({'time': AT('10:08').isoformat(), 'symbol': '053800', 'bid_qty': 50, 'ask_qty': 100}) + '\n')


def test_each_look_is_recorded_once_and_compared_with_the_real_sale(tmp_path):
    _setup(tmp_path)
    bars = lambda code, day: _bars([1000 + i for i in range(15)], [10] * 15, '09:55')  # noqa: E731
    strength = lambda code, day: {'1007': (90.0, 110.0)}  # noqa: E731
    db = tmp_path / 'research.sqlite3'
    for _ in range(2):  # a rerun replaces the day
        got = f.update(tmp_path, DAY, db, bars, strength)
    assert got == {'day': DAY, 'looks': 3, 'stocks': 1, 'failed_reads': 0}
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute('SELECT profit_pct, book_ratio, strength_1m, strength_5m, actual_pct FROM fade_looks ORDER BY time').fetchone() == (1.0, 0.5, 90.0, 110.0, -1.0)
    rules = f.summary(db, DAY)['rules']
    assert rules['book >= 0.8%'] == {'n': 1, 'vs_actual_pct': 2.0, 'late_vs_actual_pct': 2.2, 'better': 1, 'worse': 0}
    assert rules['any >= 0.3%']['n'] == 1 and 'trend >= 0.3%' not in rules


def test_a_failed_read_keeps_the_look_without_its_signals(tmp_path):
    _setup(tmp_path)

    def bars(code, day):
        if code == '053800':
            raise OSError('down')
        return []

    def strength(code, day):
        raise OSError('down')

    assert f.update(tmp_path, DAY, tmp_path / 'r.sqlite3', bars, strength)['failed_reads'] == 2
    with closing(sqlite3.connect(tmp_path / 'r.sqlite3')) as conn:
        assert conn.execute('SELECT COUNT(*), COUNT(vol_ratio), COUNT(actual_pct) FROM fade_looks').fetchone() == (3, 0, 3)


def test_no_record_yet_is_an_empty_summary(tmp_path):
    _setup(tmp_path)
    (tmp_path / 'events.jsonl').write_text('')
    f.update(tmp_path, DAY, tmp_path / 'r.sqlite3', lambda *_: [], lambda *_: {})
    assert f.summary(tmp_path / 'r.sqlite3', DAY) == {'days': 0, 'positions': 0, 'rules': {}}


def test_the_runner_keeps_the_waiting_quantities_and_never_fails_on_it(tmp_path):
    import nh_paper_isolated as n
    n.holding_book(tmp_path, '053800', {'total_bidp_rsqn': 1095, 'total_askp_rsqn': 769}, AT('10:00'))
    n.holding_book(tmp_path / 'missing', '053800', None, AT('10:00'))
    assert json.loads((tmp_path / 'holding-book.jsonl').read_text()) == {'time': AT('10:00').isoformat(), 'symbol': '053800', 'bid_qty': 1095, 'ask_qty': 769}
