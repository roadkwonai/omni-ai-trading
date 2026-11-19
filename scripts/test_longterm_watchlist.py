import sqlite3

import pytest

import longterm_watchlist as W


@pytest.fixture(autouse=True)
def history(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))


def test_the_list_is_empty_before_the_first_save_and_keeps_order_and_names(tmp_path):
    assert W.load(tmp_path) == {}
    W.save(tmp_path, {'005930': '삼성전자', '000660': 'SK하이닉스'})
    assert list(W.load(tmp_path).items()) == [('005930', '삼성전자'), ('000660', 'SK하이닉스')]
    rows = sqlite3.connect(tmp_path / 'h.sqlite3').execute("SELECT kind, key FROM changes").fetchall()
    assert ('vibe-longterm', 'watchlist.005930') in rows  # config_store history, kind vibe-longterm


def test_only_krx_codes_outside_the_engine_etfs_are_accepted():
    assert W.problem('00593', {}) and W.problem('005930', {'005930': 'x'}) and W.problem('379810', {})
    assert W.problem('0183J0', {})  # an engine ETF: pick it by its key instead
    assert W.problem('005930', {f'{i:06d}': 'x' for i in range(1, W.MAX + 1)})  # full
    assert W.problem('0193J0', {}) is None and W.problem('005930', {}) is None


def test_a_hand_edited_file_is_cleaned_on_load(tmp_path):
    (tmp_path / W.FILE).write_text('{"005930": "삼성전자", "379810": "engine", "bad": "x", "000660": 5}', encoding='utf-8')
    assert W.load(tmp_path) == {'005930': '삼성전자', '000660': '000660'}
    (tmp_path / W.FILE).write_text('[1, 2]', encoding='utf-8')
    assert W.load(tmp_path) == {}


def test_the_dca_buys_only_codes_still_on_the_list(tmp_path):
    import longterm_dca as D
    W.save(tmp_path, {'005930': '삼성전자'})
    cfg = D.clean({'pick_on': True, 'picks': ['NDX', '005930', '000660']})
    assert W.for_dca(cfg, tmp_path)['picks'] == ['NDX', '005930']  # 000660 was taken off the list: no new buys


def test_a_picked_yeo_etf_stays_a_dca_pick_without_being_on_the_watchlist(tmp_path):
    import longterm_dca as D
    cfg = D.clean({'on': True, 'pick_on': True, 'picks': ['NDX', '0261H0']})
    assert W.for_dca(cfg, tmp_path)['picks'] == ['NDX', '0261H0']
