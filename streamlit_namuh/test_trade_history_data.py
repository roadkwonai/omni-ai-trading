from datetime import date, time
import sqlite3

import trade_history_data as history


def test_accounts_filters_costs_and_live_order_time(tmp_path):
    paper = tmp_path / 'shared-etf-paper'
    paper.mkdir()
    with sqlite3.connect(paper / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', [
            ('p1', 'A', 'BUY', 10, 100, '2026-11-25T09:00:00+09:00'),
            ('p2', 'A', 'SELL', 5, 120, '2026-11-26T13:30:59+09:00'),
            ('p3', 'B', 'SELL', 1, 200, '2026-11-26T14:00:00+09:00'),
        ])
    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)', [
            ('live-1', '20261126', 1, 'C', 'BUY', 1, 100, '2026-11-26T09:10:00+09:00', ''),
            ('live-2', '20261126', 2, 'D', 'BUY', 1, 100, '2026-11-26T00:00:00+09:00', ''),
        ])
    rows = history.load(tmp_path)
    selected = history.select(rows, history.ACCOUNTS, date(2026, 11, 26), date(2026, 11, 26), time(9, 10), time(13, 30))
    assert [(row['account'], row['symbol']) for row in selected] == [('paper-shared', 'A'), ('live-1', 'C')]
    assert selected[0]['pnl'] == 100
    assert history.summary(selected)['return_pct'] == 20
    assert next(row for row in rows if row['symbol'] == 'B')['pnl'] is None
    assert next(row for row in rows if row['symbol'] == 'D')['time_known'] is False
    assert len(history.select(rows, ['live-2'], date(2026, 11, 26), date(2026, 11, 26))) == 1
    with sqlite3.connect(paper / 'orders.sqlite3') as db:
        db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?)', ('p4', 'A', 'SELL', 5, 110, '2026-11-26T14:10:00+09:00'))
    assert len(history.load(tmp_path)) == len(rows) + 1


def test_balance_snapshots_are_sorted_and_invalid_rows_skipped(tmp_path):
    path = tmp_path / 'live-balance-live-1.jsonl'
    path.write_text('{"time":"2026-11-26T10:00:00+09:00","total_assets":100}\n'
                    'invalid\n'
                    '{"time":"2026-11-26T09:00:00+09:00","total_assets":50}\n')
    snapshots = history.balances(tmp_path, 'live-1')
    assert [row['total_assets'] for row in snapshots] == [50, 100]


def test_balance_cache_keeps_latest_version_per_file_and_detects_recreation(tmp_path):
    import os

    first = tmp_path / 'live-balance-live-1.jsonl'
    other = tmp_path / 'live-balance-live-2.jsonl'
    first.write_text('{"time":"2026-11-26T09:00:00+09:00","total_assets":100}\n')
    other.write_text('{"time":"2026-11-26T09:00:00+09:00","total_assets":200}\n')
    other_rows = history.balances(tmp_path, 'live-2')
    for index in range(20):
        with first.open('a') as stream:
            stream.write('{"time":"2026-11-26T09:01:00+09:00","total_assets":%d}\n' % (index + 101))
        assert len(history.balances(tmp_path, 'live-1')) == index + 2
    assert history.balances(tmp_path, 'live-2') is other_rows
    assert sum(path == str(first.resolve()) for path in history._balance_cache) == 1
    first.unlink()
    assert history.balances(tmp_path, 'live-1') == ()
    first.write_text('{"time":"2026-11-26T10:00:00+09:00","total_assets":300}\n')
    assert [row['total_assets'] for row in history.balances(tmp_path, 'live-1')] == [300]
    stamp = first.stat()
    replacement = tmp_path / 'replacement.jsonl'
    replacement.write_text('{"time":"2026-11-26T10:00:00+09:00","total_assets":400}\n')
    os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    replacement.replace(first)
    assert [row['total_assets'] for row in history.balances(tmp_path, 'live-1')] == [400]


def test_streaming_summary_keeps_zero_and_unknown_sell_costs_separate():
    def row(account, side, amount, pnl=None, cost=None):
        return {'account': account, 'side': side, 'amount': amount, 'pnl': pnl, 'cost': cost}

    rows = [row('paper-shared', 'BUY', 100), row('live-1', 'SELL', 100, 0, 100),
            row('live-1', 'SELL', 50, -50, 100), row('live-2', 'SELL', 100)]
    total = history.summary(iter(rows))
    assert (total['realized'], total['return_pct'], total['known_sells'], total['flat']) == (-50, -25, 2, 1)
    groups = history.summary_by_account(iter(rows))
    assert (groups['live-1']['realized'], groups['live-1']['sold_cost']) == (-50, 200)
    assert groups['live-2']['realized'] is None and groups['live-2']['sold_cost'] == 0
    assert history.summary(iter(()))['realized'] is None


def test_balance_reads_share_one_consistent_result_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / 'live-balance-live-1.jsonl'
    path.write_text('{"time":"2026-11-26T09:00:00+09:00","total_assets":100}\n')
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: history.balances(tmp_path, 'live-1'), range(24)))
    assert all(rows is results[0] for rows in results)
    with path.open('a') as stream:
        stream.write('{"time":"2026-11-26T09:05:00+09:00","total_assets":200}\n')
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: history.balances(tmp_path, 'live-1'), range(24)))
    assert all(rows is results[0] for rows in results)
    assert [row['total_assets'] for row in results[0]] == [100, 200]


def test_live_cost_requires_flat_snapshot_and_recovers_after_gap(tmp_path):
    import json

    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)', [
            ('live-1', '20261125', 1, 'A', 'BUY', 1, 100, '2026-11-25T09:10:00+09:00', ''),
            ('live-1', '20261125', 2, 'A', 'SELL', 1, 110, '2026-11-25T09:20:00+09:00', ''),
            ('live-1', '20261126', 3, 'B', 'BUY', 1, 100, '2026-11-26T09:10:00+09:00', ''),
            ('live-1', '20261126', 4, 'B', 'SELL', 1, 120, '2026-11-26T09:20:00+09:00', ''),
            ('live-1', '20261126', 5, 'C', 'SELL', 1, 120, '2026-11-26T09:30:00+09:00', ''),
            ('live-1', '20261126', 6, 'C', 'BUY', 1, 100, '2026-11-26T09:40:00+09:00', ''),
            ('live-1', '20261126', 7, 'C', 'SELL', 1, 110, '2026-11-26T09:50:00+09:00', ''),
            ('live-1', '20261126', 8, 'C', 'BUY', 2, 100, '2026-11-26T11:00:00+09:00', ''),
            ('live-1', '20261126', 9, 'C', 'SELL', 2, 110, '2026-11-26T11:10:00+09:00', ''),
        ])
    before = history.load(tmp_path)
    assert all(row['pnl'] is None for row in before if row['side'] == 'SELL')

    path = tmp_path / 'live-balance-live-1.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in [
        {'time': '2026-11-26T09:00:00+09:00', 'total_assets': 1000, 'invested': 0, 'evaluation': 0},
        {'time': '2026-11-26T10:30:00+09:00', 'total_assets': 1000, 'invested': 0, 'evaluation': 0},
    ]))
    after = {row['order_no']: row for row in history.load(tmp_path)}
    assert after[2]['pnl'] is None
    assert after[4]['pnl'] == 20
    assert after[7]['pnl'] is None
    assert after[9]['pnl'] == 20
    assert len(after) == len(before)


def test_live_tied_anchor_and_unknown_timestamp_do_not_establish_cost(tmp_path):
    import json

    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)', [
            ('live-1', '20261126', 1, 'A', 'BUY', 1, 100, '2026-11-26T09:00:00+09:00', ''),
            ('live-1', '20261126', 2, 'A', 'SELL', 1, 110, '2026-11-26T09:10:00+09:00', ''),
            ('live-1', '20261126', 3, 'B', 'BUY', 1, 100, '2026-11-26T00:00:00+09:00', ''),
            ('live-1', '20261126', 4, 'B', 'SELL', 1, 110, '2026-11-26T10:10:00+09:00', ''),
        ])
    path = tmp_path / 'live-balance-live-1.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in [
        {'time': '2026-11-26T09:00:00+09:00', 'total_assets': 1000, 'invested': 0, 'evaluation': 0},
        {'time': '2026-11-26T10:00:00+09:00', 'total_assets': 1000, 'invested': 0, 'evaluation': 0},
    ]))
    rows = {row['order_no']: row for row in history.load(tmp_path)}
    assert rows[2]['pnl'] is None
    assert rows[4]['pnl'] is None


def test_new_guard_link_makes_earlier_submission_time_available(tmp_path):
    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)',
                   ('live-1', '20261126', 7, 'A', 'BUY', 1, 100, '2026-11-26T00:00:00+09:00', ''))
    (tmp_path / 'live-follow.jsonl').write_text(
        '{"account":"live-1","intent":"intent-7","time":"2026-11-26T09:10:00+09:00"}\n')
    assert not history.load(tmp_path)[0]['time_known']

    with sqlite3.connect(tmp_path / 'live-orders.sqlite3') as db:
        db.execute('CREATE TABLE live_intents(day TEXT, fingerprint TEXT, order_number INTEGER)')
        db.execute('INSERT INTO live_intents VALUES(?,?,?)', ('20261126', 'intent-7', 7))

    selected = history.select(history.load(tmp_path), ('live-1',), date(2026, 11, 26), date(2026, 11, 26),
                              time(9, 10), time(9, 10))
    assert [(row['order_no'], row['time_basis']) for row in selected] == [(7, '제출 기록')]


def test_flat_snapshot_cannot_clear_a_buy_first_observed_later(tmp_path):
    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)', [
            ('live-1', '20261126', 1, 'A', 'BUY', 1, 50, '2026-11-26T09:00:00+09:00', '2026-11-26T09:15:00+09:00'),
            ('live-1', '20261126', 2, 'A', 'BUY', 1, 100, '2026-11-26T09:20:00+09:00', '2026-11-26T09:20:20+09:00'),
            ('live-1', '20261126', 3, 'A', 'SELL', 1, 120, '2026-11-26T09:30:00+09:00', '2026-11-26T09:30:20+09:00'),
            ('live-1', '20261126', 4, 'B', 'BUY', 1, 100, '2026-11-26T09:40:00+09:00', '2026-11-26T09:40:20+09:00'),
            ('live-1', '20261126', 5, 'B', 'SELL', 1, 120, '2026-11-26T09:50:00+09:00', '2026-11-26T09:50:20+09:00'),
            ('live-1', '20261126', 6, 'A', 'BUY', 1, 100, '2026-11-26T10:10:00+09:00', '2026-11-26T10:10:20+09:00'),
            ('live-1', '20261126', 7, 'A', 'SELL', 1, 125, '2026-11-26T10:20:00+09:00', '2026-11-26T10:20:20+09:00'),
        ])
    (tmp_path / 'live-balance-live-1.jsonl').write_text(
        '{"time":"2026-11-26T09:10:00+09:00","total_assets":1000,"invested":0,"evaluation":0}\n'
        '{"time":"2026-11-26T10:00:00+09:00","total_assets":1000,"invested":0,"evaluation":0}\n')
    sells = history.select(history.load(tmp_path), ('live-1',), side='SELL')
    assert {row['order_no']: row['pnl'] for row in sells} == {3: None, 5: 20, 7: 25}


def test_freshness_ignores_heartbeat_and_balance_updates(tmp_path):
    before = history.freshness_revision(tmp_path)
    cache_before = history.revision(tmp_path)
    (tmp_path / 'live-follow.jsonl').write_text('{"action":"HOLD"}\n')
    (tmp_path / 'live-balance-live-1.jsonl').write_text('{"total_assets":1000}\n')
    assert history.freshness_revision(tmp_path) == before
    assert history.revision(tmp_path) != cache_before
    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_no INTEGER)')
        db.execute('INSERT INTO fills VALUES (1)')
    assert history.freshness_revision(tmp_path) != before


def test_one_account_balance_update_preserves_other_books(tmp_path, monkeypatch):
    import json

    paper = tmp_path / 'shared-etf-paper'
    paper.mkdir()
    with sqlite3.connect(paper / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
        db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?)', ('p1', 'P', 'BUY', 1, 100, '2026-11-26T09:00:00+09:00'))
    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)', [
            (key, '20261126', number, key, side, 1, price, stamp, '')
            for key in ('live-1', 'live-2')
            for number, side, price, stamp in [
                (1, 'BUY', 100, '2026-11-26T09:10:00+09:00'),
                (2, 'SELL', 120, '2026-11-26T09:20:00+09:00'),
            ]
        ])
    calls = []
    book = history._book

    def record_replay(account, records, checkpoints=()):
        calls.append(account)
        return book(account, records, checkpoints)

    monkeypatch.setattr(history, '_book', record_replay)
    before = history.load(tmp_path)
    assert len(before) == 5
    assert {r['pnl'] for r in before if r['side'] == 'SELL'} == {None}
    calls.clear()

    (tmp_path / 'live-balance-live-1.jsonl').write_text(json.dumps(
        {'time': '2026-11-26T09:00:00+09:00', 'total_assets': 1000, 'invested': 0, 'evaluation': 0}
    ) + '\n')
    after = history.load(tmp_path)
    sells = {r['account']: r['pnl'] for r in after if r['side'] == 'SELL'}
    assert sells == {'live-1': 20, 'live-2': None}
    assert calls == ['live-1']


def test_balance_snapshots_sort_by_instant_across_time_zones(tmp_path):
    path = tmp_path / 'live-balance-live-1.jsonl'
    path.write_text('{"time":"2026-11-25T23:30:00+00:00","total_assets":100}\n'
                    '{"time":"2026-11-26T08:00:00+09:00","total_assets":50}\n'
                    '{"time":"2026-11-26T08:40:00","total_assets":150}\n')
    assert [row['total_assets'] for row in history.balances(tmp_path, 'live-1')] == [50, 100, 150]


def test_nonflat_balance_does_not_replay_unchanged_history(tmp_path, monkeypatch):
    import json

    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)',
                   ('live-1', '20261126', 1, 'A', 'BUY', 1, 100, '2026-11-26T09:10:00+09:00', ''))
    calls = []
    book = history._book

    def record_replay(account, records, checkpoints=()):
        calls.append(account)
        return book(account, records, checkpoints)

    monkeypatch.setattr(history, '_book', record_replay)
    before = history.load(tmp_path)
    calls.clear()
    (tmp_path / 'live-balance-live-1.jsonl').write_text(json.dumps(
        {'time': '2026-11-26T09:15:00+09:00', 'total_assets': 1000, 'invested': 100, 'evaluation': 100}
    ) + '\n')
    assert history.load(tmp_path) is before
    assert calls == []


def test_heartbeat_does_not_replay_accounts_with_known_order_times(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / 'live-fills.sqlite3') as db:
        db.execute('CREATE TABLE fills(account TEXT, day TEXT, order_no INTEGER, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT, recorded TEXT)')
        db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?)',
                   ('live-1', '20261126', 1, 'A', 'BUY', 1, 100, '2026-11-26T09:10:00+09:00', ''))
    calls = []
    book = history._book

    def record_replay(account, records, checkpoints=()):
        calls.append(account)
        return book(account, records, checkpoints)

    monkeypatch.setattr(history, '_book', record_replay)
    before = history.load(tmp_path)
    calls.clear()
    (tmp_path / 'live-follow.jsonl').write_text('{"action":"HOLD"}\n')
    assert history.load(tmp_path) is before
    assert calls == []


def test_query_pages_stay_stable_across_changes_and_expire_explicitly(tmp_path, monkeypatch):
    import pytest

    rows = tuple({
        'account': 'paper-shared', 'time': f'2026-11-26T09:{i:02}:00+09:00',
        'time_known': True, 'side': 'BUY', 'order_no': i,
    } for i in range(5))
    version = [1]
    monkeypatch.setattr(history, 'load', lambda _: rows if version[0] == 1 else rows[::-1])
    monkeypatch.setattr(history, 'revision', lambda _: (version[0],))
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    token, query = history.open_query(tmp_path, filters)
    assert len(query) == 5
    assert [r['order_no'] for r in query.page(1, 2)] == [1, 2]
    assert history.open_query(tmp_path, filters)[0] == token

    version[0] = 2
    newer, new_query = history.open_query(tmp_path, filters)
    assert newer != token
    assert [r['order_no'] for r in history.get_query(token).page(1, 2)] == [1, 2]
    assert [r['order_no'] for r in new_query.page(1, 2)] == [3, 2]

    for version[0] in range(3, 3 + history.QUERY_LIMIT):
        history.open_query(tmp_path, filters)
    with pytest.raises(history.QueryExpired):
        history.get_query(token)


def test_query_capture_retries_a_changing_source(tmp_path, monkeypatch):
    calls = []
    versions = iter([1, 2, 2, 2])
    monkeypatch.setattr(history, 'revision', lambda _: (next(versions),))
    monkeypatch.setattr(history, 'load', lambda _: calls.append(1) or ())
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    _, query = history.open_query(tmp_path, filters)
    assert len(query) == 0
    assert len(calls) == 2


def test_concurrent_identical_queries_build_once_without_blocking_existing_reads(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event, Lock

    ready, release, seen, lock = Event(), Event(), [], Lock()
    rows = ({'account': 'paper-shared', 'time': '2026-11-26T09:00:00+09:00', 'time_known': True,
             'side': 'BUY', 'order_no': 1},)
    monkeypatch.setattr(history, 'revision', lambda _: (1,))
    monkeypatch.setattr(history, 'load', lambda _: rows)
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    cached, _ = history.open_query(tmp_path / 'existing', filters)

    def blocked_load(_):
        with lock:
            seen.append(1)
        ready.set()
        assert release.wait(3)
        return rows

    monkeypatch.setattr(history, 'load', blocked_load)
    barrier = Barrier(5)

    def acquire():
        barrier.wait()
        return history.acquire_query(tmp_path / 'new', filters)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(acquire) for _ in range(4)]
        barrier.wait()
        assert ready.wait(3)
        try:
            assert history.get_query(cached).page(0, 1)[0]['order_no'] == 1
            Event().wait(.2)
            assert len(seen) == 1
        finally:
            release.set()
        acquired = [future.result(timeout=3) for future in futures]
    assert len({lease.token for lease, _ in acquired}) == 1
    for lease, _ in acquired:
        lease.release()


def test_failed_query_builder_wakes_waiters_for_retry(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    attempts, lock, barrier = [], Lock(), Barrier(5)
    monkeypatch.setattr(history, 'revision', lambda _: (1,))

    def load(_):
        with lock:
            attempts.append(1)
            first = len(attempts) == 1
        if first:
            raise OSError('test read error')
        return ()

    monkeypatch.setattr(history, 'load', load)
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)

    def query():
        barrier.wait()
        try:
            return history.open_query(tmp_path, filters)[1]
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(query) for _ in range(4)]
        barrier.wait()
        results = [future.result(timeout=3) for future in futures]
    assert sum(result is None for result in results) == 1
    assert sum(result is not None for result in results) == 3


def test_concurrent_sessions_pin_shared_snapshot_until_last_lease_release(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import gc
    import pytest

    rows = ({'account': 'paper-shared', 'time': '2026-11-26T09:00:00+09:00', 'time_known': True,
             'side': 'BUY', 'order_no': 1},)
    version = [1]
    monkeypatch.setattr(history, 'load', lambda _: rows if version[0] == 1 else rows + (dict(rows[0], order_no=2),))
    monkeypatch.setattr(history, 'revision', lambda _: (version[0],))
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)

    with ThreadPoolExecutor(max_workers=4) as executor:
        acquired = list(executor.map(lambda _: history.acquire_query(tmp_path, filters), range(4)))
    handles = [handle for handle, _ in acquired]
    old_token = handles[0].token
    assert all(handle.token == old_token for handle in handles)
    assert all(query is acquired[0][1] for _, query in acquired)
    assert len(handles[0].__slots__) == 3

    for version[0] in range(2, 2 + history.QUERY_LIMIT + 4):
        history.open_query(tmp_path, filters)
    assert [row['order_no'] for row in history.get_query(old_token)] == [1]
    newest, new_query = history.open_query(tmp_path, filters)
    assert newest != old_token
    assert [row['order_no'] for row in new_query] == [1, 2]

    handles[0].release()
    handles[0].release()
    for handle in handles[1:]:
        handle.release()
    del handles, acquired, handle
    gc.collect()
    for version[0] in range(30, 30 + history.QUERY_LIMIT + 1):
        history.open_query(tmp_path, filters)
    with pytest.raises(history.QueryExpired):
        history.get_query(old_token)


def test_lease_automatic_release_does_not_hold_query_forever(tmp_path, monkeypatch):
    import gc
    import pytest

    monkeypatch.setattr(history, 'load', lambda _: ())
    version = [1]
    monkeypatch.setattr(history, 'revision', lambda _: (version[0],))
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    handle, _ = history.acquire_query(tmp_path, filters)
    token = handle.token
    del handle
    gc.collect()
    for version[0] in range(2, history.QUERY_LIMIT + 3):
        history.open_query(tmp_path, filters)
    with pytest.raises(history.QueryExpired):
        history.get_query(token)


def test_last_session_release_removes_only_its_snapshot(tmp_path, monkeypatch):
    import gc
    import pytest
    import weakref

    class Fill(dict):
        pass

    row = Fill(account='paper-shared', time='2026-11-26T09:00:00+09:00',
               time_known=True, side='BUY', order_no=1)
    retained = weakref.ref(row)
    fills = [row]
    monkeypatch.setattr(history, 'load', lambda _: tuple(fills))
    monkeypatch.setattr(history, 'revision', lambda _: (1,))
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    a, qa = history.acquire_query(tmp_path, filters)
    b, qb = history.acquire_query(tmp_path, filters)
    token = a.token
    assert b.token == token
    a.release()
    assert list(history.get_query(token)) == [row]
    b.release()
    with pytest.raises(history.QueryExpired):
        history.get_query(token)
    fills.clear()
    del qa, qb, row
    gc.collect()
    assert retained() is None


def test_new_versions_do_not_remain_in_pipeline_caches(tmp_path):
    paper = tmp_path / 'shared-etf-paper'
    paper.mkdir()
    with sqlite3.connect(paper / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
    filters = (('paper-shared',), date(2026, 11, 26), date(2026, 11, 26), None, None, None)
    for i in range(7):
        with sqlite3.connect(paper / 'orders.sqlite3') as db:
            db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?)',
                       (f'p{i}', 'P', 'BUY', 1, 100, f'2026-11-26T09:{i:02}:00+09:00'))
        lease, query = history.acquire_query(tmp_path, filters)
        assert len(query) == i + 1
        lease.release()
    assert history._load.cache_info().currsize <= 1
    assert history._paper_book.cache_info().currsize <= 1
    assert history._merged.cache_info().currsize <= 1


def test_simultaneous_different_account_queries_share_cold_ledger_read(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event, Lock

    gate, counts, started = Barrier(4), [], Lock()
    done = Event()
    def paper(_root):
        with started:
            counts.append(1)
        done.wait(1)
        return []

    monkeypatch.setattr(history, '_paper', paper)
    day = date(2026, 11, 26)
    def acquire(account):
        gate.wait()
        filters = ((account,), day, day, None, None, None)
        return history.acquire_query(tmp_path, filters)[0]

    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = [pool.submit(acquire, account) for account in history.ACCOUNTS]
        gate.wait()
        Event().wait(.2)
        done.set()
        leases = [future.result(timeout=4) for future in pending]
    for lease in leases:
        lease.release()
    assert len(counts) == 1
