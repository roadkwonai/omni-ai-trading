from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jev_candidate_evidence import flow_at, minute_snapshot, program_at, store_minutes, store_program


KST = ZoneInfo('Asia/Seoul')


def t(minute, second=0):
    return datetime(2026, 11, 26, 9, minute, second, tzinfo=KST)


def tick(identifier, minute, price, quantity, received=None):
    return {'id': identifier, 'source_at': t(minute, 10), 'received_at': received or t(minute, 11),
            'price': price, 'signed_qty': quantity, 'venue': 'KRX'}


def bucket(minute, ticks=(), *, complete=True, received=None):
    return minute_snapshot('005930', t(minute), list(ticks), complete=complete, received_at=received or t(minute + 1))


def test_contiguous_windows_include_zero_trading_and_preserve_sides(tmp_path):
    path = tmp_path / 'research.sqlite3'
    rows = [bucket(0, [tick('a', 0, 100, 2), tick('b', 0, 120, -3)]),
            bucket(1), bucket(2, [tick('c', 2, 110, 1)])]
    assert store_minutes(path, rows) == 3
    assert store_minutes(path, rows) == 0
    got = flow_at(path, '2026-11-26', '005930', t(3, 20))
    assert got['windows'][3] == {'status': 'ready', 'buy_krw': 310, 'sell_krw': 360, 'buy_qty': 3, 'sell_qty': 3}
    assert got['windows'][5]['status'] == 'coverage_missing'


def test_as_of_never_uses_late_or_unfinished_observation(tmp_path):
    path = tmp_path / 'research.sqlite3'
    store_minutes(path, [bucket(0, [tick('a', 0, 100, -2)], received=t(3)),
                         bucket(1, [tick('b', 1, 100, -3)]), bucket(2, [tick('c', 2, 100, -4)])])
    assert flow_at(path, '2026-11-26', '005930', t(2, 30))['windows'][3]['status'] == 'coverage_missing'
    assert flow_at(path, '2026-11-26', '005930', t(3))['windows'][3]['sell_krw'] == 900


def test_gap_and_mixed_venue_are_never_interpreted_as_zero(tmp_path):
    path = tmp_path / 'research.sqlite3'
    store_minutes(path, [bucket(0), bucket(1, complete=False), bucket(2)])
    assert flow_at(path, '2026-11-26', '005930', t(3))['windows'][3]['status'] == 'coverage_missing'
    item = tick('a', 0, 100, 1)
    item['venue'] = 'NXT'
    with pytest.raises(ValueError):
        bucket(0, [item])


def test_duplicate_identity_conflict_and_future_received_rejected():
    row = tick('a', 0, 100, 1)
    with pytest.raises(ValueError):
        bucket(0, [row, {**row, 'signed_qty': -1}])
    with pytest.raises(ValueError):
        bucket(0, [tick('late', 0, 100, 1, received=t(2))])


def test_conflicting_snapshot_and_naive_time_rejected(tmp_path):
    path = tmp_path / 'research.sqlite3'
    row = bucket(0)
    store_minutes(path, [row])
    with pytest.raises(ValueError):
        store_minutes(path, [{**row, 'buy_krw': 5}])
    with pytest.raises(ValueError):
        flow_at(path, '2026-11-26', '005930', datetime(2026, 11, 26, 9, 5))


def test_correction_is_visible_only_after_arrival(tmp_path):
    path = tmp_path / 'research.sqlite3'
    original = bucket(0, [tick('a', 0, 100, -2)])
    correction = bucket(0, [tick('a', 0, 100, -2), tick('b', 0, 100, -1, received=t(2))], received=t(2))
    store_minutes(path, [original, correction])
    assert flow_at(path, '2026-11-26', '005930', t(1, 30))['windows'][1]['sell_krw'] == 200
    # At 09:02 the correction is observable for the 3-minute window once all three buckets exist.
    store_minutes(path, [bucket(1), bucket(2)])
    assert flow_at(path, '2026-11-26', '005930', t(3))['windows'][3]['sell_krw'] == 300


def test_conflict_rolls_back_entire_batch(tmp_path):
    path = tmp_path / 'research.sqlite3'
    first = bucket(0)
    store_minutes(path, [first])
    with pytest.raises(ValueError):
        store_minutes(path, [bucket(1), {**first, 'sell_qty': 4}])
    assert flow_at(path, '2026-11-26', '005930', t(2))['windows'][1]['status'] == 'coverage_missing'


def test_outside_regular_session_rejected():
    with pytest.raises(ValueError):
        minute_snapshot('005930', datetime(2026, 11, 26, 8, 59, tzinfo=KST), [], complete=True, received_at=t(0))


def program_row(minute, buy, sell):
    return {'tm': f'09{minute:02}00', 'prm_buy_qty': str(buy), 'prm_sell_qty': str(sell),
            'prm_netprps_qty': str(buy - sell), 'stex_tp': 'KRX'}


def test_program_point_in_time_and_cumulative_difference(tmp_path):
    path = tmp_path / 'research.sqlite3'
    first = [program_row(0, 100, 200), program_row(5, 120, 240)]
    assert store_program(path, '005930', '2026-11-26', first, t(6)) == 2
    assert store_program(path, '005930', '2026-11-26', first, t(6)) == 0
    assert program_at(path, '2026-11-26', '005930', t(5))['status'] == 'program_missing_or_stale'
    got = program_at(path, '2026-11-26', '005930', t(6))
    assert got['status'] == 'ready'
    assert (got['recent_buy_shares'], got['recent_sell_shares'], got['recent_net_shares']) == (20, 40, -20)


def test_program_missing_revision_and_reset(tmp_path):
    path = tmp_path / 'research.sqlite3'
    store_program(path, '005930', '2026-11-26', [program_row(0, 100, 200), program_row(5, 120, 240)], t(5))
    store_program(path, '005930', '2026-11-26', [program_row(3, 5, 10)], t(5, 10))
    assert program_at(path, '2026-11-26', '005930', t(5))['status'] == 'ready'
    assert program_at(path, '2026-11-26', '005930', t(5, 15))['status'] == 'program_cumulative_reset'
    assert program_at(path, '2026-11-26', '005930', t(8))['status'] == 'program_missing_or_stale'


def test_program_rejects_future_and_conflicting_revision(tmp_path):
    path = tmp_path / 'research.sqlite3'
    with pytest.raises(ValueError):
        store_program(path, '005930', '2026-11-26', [program_row(5, 10, 20)], t(4))
    store_program(path, '005930', '2026-11-26', [program_row(0, 10, 20)], t(1))
    with pytest.raises(ValueError):
        store_program(path, '005930', '2026-11-26', [program_row(0, 10, 30)], t(1))


def test_program_late_revision_keeps_prior_view(tmp_path):
    path = tmp_path / 'research.sqlite3'
    store_program(path, '005930', '2026-11-26', [program_row(0, 10, 20), program_row(5, 30, 50)], t(5))
    store_program(path, '005930', '2026-11-26', [program_row(5, 40, 70)], t(5, 10))
    assert program_at(path, '2026-11-26', '005930', t(5, 5))['recent_net_shares'] == -10
    assert program_at(path, '2026-11-26', '005930', t(5, 15))['recent_net_shares'] == -20
