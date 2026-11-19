import sqlite3

import position_sizing as ps

VIEW = {'코스피': {'regime': '하락장'}, '코스닥': {'regime': '횡보장'}}


def test_buy_factor_shrinks_with_holdings_and_against_the_market():
    on = {'size_holding_on': 1, 'size_market_on': 1}
    assert ps.buy_factor(on, VIEW, '코스피', True, 0, 20_000_000)[0] == 1.0  # inverse in a 하락장, nothing held
    assert ps.buy_factor(on, VIEW, '코스피', False, 0, 20_000_000)[0] == 0.5  # long against the 하락장
    assert ps.buy_factor(on, VIEW, '코스닥', False, 0, 20_000_000)[0] == 0.75  # 횡보장
    assert ps.buy_factor(on, VIEW, '코스피', True, 12_000_000, 20_000_000)[0] == 0.4  # past half of the limit
    assert ps.buy_factor(on, VIEW, '코스피', False, 16_000_000, 20_000_000)[0] == 0.25  # floor
    assert ps.buy_factor({'size_holding_on': 0, 'size_market_on': 0}, VIEW, '코스피', False, 16_000_000, 20_000_000)[0] == 1.0
    assert ps.buy_factor(on, {}, '코스피', False, 0, 20_000_000)[0] == 1.0  # unknown regime: no cut


def test_regime_trim_once_after_the_market_turns(tmp_path):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE fills (symbol TEXT, side TEXT, quantity INTEGER)')
    db.executemany('INSERT INTO fills VALUES (?,?,?)', [('114800', 'BUY', 100), ('114800', 'BUY', 100)])
    on = {'regime_trim_on': 1}
    assert ps.regime_trim(tmp_path, db, '114800', 200, VIEW, '코스피', True, on) == 0  # bought in its own regime
    assert ps.regime_trim(tmp_path, db, '114800', 200, {'코스피': {'regime': '상승장'}}, '코스피', True, on) == 100
    ps.mark_trimmed(tmp_path, '114800')
    assert ps.regime_trim(tmp_path, db, '114800', 200, {'코스피': {'regime': '상승장'}}, '코스피', True, on) == 0  # once
    db.execute("INSERT INTO fills VALUES ('005930', 'BUY', 10)")
    assert ps.regime_trim(tmp_path, db, '005930', 10, VIEW, '코스피', False, on) == 0  # bought already against: no trim
    assert ps.regime_trim(tmp_path, db, '114800', 200, {'코스피': {'regime': '상승장'}}, '코스피', True, {'regime_trim_on': 0}) == 0
