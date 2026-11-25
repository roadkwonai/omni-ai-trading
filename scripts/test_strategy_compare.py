import json
import sqlite3

import pytest

import strategy_compare as S


def fill(time, symbol, side, qty, price, pnl=None):
    return {'time': time, 'symbol': symbol, 'side': side, 'qty': qty, 'price': price, 'amount': qty * price, 'pnl': pnl,
            'cost': None if pnl is None else qty * price - pnl, 'after': 0, 'path': '', 'basis': ''}


def test_jev_days_use_the_largest_money_in_positions_net_of_fees_and_count_quiet_days_as_zero():
    rows = [fill('2026-11-11T09:30:00+09:00', '069500', 'BUY', 10, 10_000),
            fill('2026-11-11T09:40:00+09:00', '005930', 'BUY', 1, 50_000),
            fill('2026-11-11T10:00:00+09:00', '069500', 'SELL', 10, 10_100, pnl=1_000),
            fill('2026-11-11T10:30:00+09:00', '005930', 'SELL', 1, 49_000, pnl=-1_000)]
    for r, after in zip(rows, (10, 1, 0, 0)):
        r['after'] = after
    out = S.jev_days(rows, {'2026-11-10', '2026-11-12'}, {}, etfs={'069500'})
    assert [d['day'] for d in out] == ['2026-11-11', '2026-11-12']  # nothing before the first fill
    day = out[0]
    assert day['deployed'] == 150_000 and day['realized_day'] == 0 and (day['buys'], day['sells'], day['wins']) == (2, 2, 1)
    turnover = 100_000 + 50_000 + 101_000 + 49_000
    assert day['fees_est'] == pytest.approx(turnover * 0.015 / 100 + 49_000 * 0.20 / 100)  # tax on the stock sale only
    assert day['pnl_day'] == pytest.approx(-day['fees_est'])
    assert day['avg_hold_min'] == pytest.approx((30 + 50) / 2)
    assert (out[1]['trades'], out[1]['pnl_day'], out[1]['deployed']) == (0, 0, 0)
    finished = S.finish(out)
    assert finished[1]['ret_day'] == 0.0 and finished[0]['ret_day'] == pytest.approx(day['pnl_day'] / 150_000)


def test_jev_unrealized_change_counts_on_the_day_it_happens():
    rows = [fill('2026-11-17T14:00:00+09:00', '069500', 'BUY', 10, 10_000)]
    rows[0]['after'] = 10
    snaps = {'2026-11-17': {'evaluation': 101_000, 'invested': 100_000}, '2026-11-18': {'evaluation': 99_000, 'invested': 100_000}}
    out = S.jev_days(rows, {'2026-11-18'}, snaps, etfs={'069500'})
    assert out[0]['unrealized'] == 1_000 and out[1]['pnl_day'] == pytest.approx(-2_000)
    assert out[1]['deployed'] == 100_000  # carried positions are money in use on a day without fills


def test_longterm_days_take_out_paid_in_money_and_only_the_actual_book_has_trades():
    nav = [('2026-11-17', 'actual', 100_000_000), ('2026-11-18', 'actual', 101_500_000), ('2026-11-18', 'base', 100_200_000),
           ('2026-11-17', 'base', 100_000_000)]
    fills = [{'time': '2026-11-18T12:02:00+09:00', 'side': 'BUY', 'amount': 5_000_000, 'pnl': None}]
    out = S.longterm_days(nav, {'2026-11-18': 1_000_000}, fills)
    second = out['longterm'][1]
    assert second['pnl_day'] == 500_000 and second['deployed'] == 101_000_000 and second['pnl_cum'] == 500_000
    assert (second['trades'], second['buys'], second['turnover']) == (1, 1, 5_000_000)
    assert out['longterm_base'][1]['trades'] is None and out['longterm_base'][1]['pnl_day'] == -800_000  # shadows get the same 적립


def test_finish_tracks_growth_and_drawdown():
    rows = S.finish([{'pnl_day': 10, 'deployed': 100}, {'pnl_day': -22, 'deployed': 110}, {'pnl_day': 0, 'deployed': 0}])
    assert [round(r['nav_index'], 4) for r in rows] == [1.1, 0.88, 0.88]
    assert rows[1]['drawdown'] == pytest.approx(0.88 / 1.1 - 1) and rows[2]['pnl_cum'] == -12


def test_build_writes_one_row_per_day_and_book_and_rebuilds_the_same(tmp_path, monkeypatch):
    import engine_compare
    import vibe_longterm
    import vibe_longterm_view
    jev = tmp_path / 'shared-etf-paper'
    jev.mkdir()
    rows = [fill('2026-11-18T09:30:00+09:00', '069500', 'BUY', 10, 10_000), fill('2026-11-18T10:00:00+09:00', '069500', 'SELL', 10, 10_200, 2_000)]
    rows[0]['after'] = 10
    monkeypatch.setattr(engine_compare, 'journal', lambda *a, **k: rows)
    monkeypatch.setattr(vibe_longterm, 'journal_rows', lambda root: [])
    monkeypatch.setattr(vibe_longterm_view, 'nav_history', lambda root: ([('2026-11-18', 'actual', 100_100_000)], {}))
    monkeypatch.setattr('engine_roles.ledger_root', lambda runtime, engine: jev)
    assert S.build(tmp_path) == {'jev': 1, 'longterm': 1}
    first = S.rows(tmp_path)
    assert {r['book'] for r in first} == {'jev', 'longterm'} and all(set(r['bench']) == {'069500', 'NDX'} for r in first)
    S.build(tmp_path)
    assert [(r['day'], r['book'], r['pnl_day']) for r in S.rows(tmp_path)] == [(r['day'], r['book'], r['pnl_day']) for r in first]
    assert S.check(tmp_path) == {'ledger': 2000, 'table': 2000, 'ok': True}
    assert not S.stale(tmp_path)
    late = rows + [fill('2099-01-01T10:00:00+09:00', '069500', 'SELL', 1, 9_000, -500)]  # after the build: not in the table yet
    monkeypatch.setattr(engine_compare, 'journal', lambda *a, **k: late)
    assert S.check(tmp_path) == {'ledger': 2000, 'table': 2000, 'ok': True}
    rows[1]['pnl'] = 1500  # a sale the table already counted
    assert S.check(tmp_path)['ok'] is False
    with sqlite3.connect(tmp_path / S.FILE) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'


def test_regime_days_keep_the_last_call_of_each_day(tmp_path):
    import vibe_longterm as LT
    db = LT.connect(tmp_path)
    for day, regime in (('2026-11-18', 'bull'), ('2026-11-19', 'bull'), ('2026-11-19', 'correction')):
        LT.log(db, 'regime', day, regime=regime)
    assert S.regime_days(tmp_path / 'longterm.sqlite3') == {'2026-11-18': 'bull', '2026-11-19': 'correction'}
    assert S.regime_days(tmp_path / 'none.sqlite3') == {}
