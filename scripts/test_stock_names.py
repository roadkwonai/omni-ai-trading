import json
import sqlite3
from contextlib import closing

import stock_names


def _shared(tmp_path):
    shared = tmp_path / 'shared-etf-paper'
    shared.mkdir()
    return shared


def test_a_new_listing_is_named_though_it_was_never_in_the_watchlist(tmp_path):
    # 2026-11-25: 엠비디 was bought by the new-listing path and every screen showed 498150
    shared = _shared(tmp_path)
    (shared / 'new-listings.json').write_text(json.dumps({'rows': [{'name': '엠비디', 'code': '498150', 'listed': '20261124'}]}), encoding='utf-8')
    (shared / 'movers.json').write_text(json.dumps({'rows': {'000660': {'name': 'SK하이닉스'}, '1': None}}), encoding='utf-8')
    (shared / 'watchlist.json').write_text(json.dumps({'stocks': {'005930': {'name': '*삼성전자'}}}), encoding='utf-8')
    names = stock_names.load(tmp_path)
    assert (names['498150'], names['000660'], names['005930']) == ('엠비디', 'SK하이닉스', '삼성전자')
    assert names['379810'] and names['0261H0'] and names['114800']


def test_damaged_files_leave_the_etf_names(tmp_path):
    shared = _shared(tmp_path)
    (shared / 'new-listings.json').write_text('[]', encoding='utf-8')
    (shared / 'movers.json').write_text('{"rows": 3}', encoding='utf-8')
    (shared / 'watchlist.json').write_text('{', encoding='utf-8')
    assert stock_names.load(tmp_path)['114800']


def test_a_traded_code_nobody_named_is_asked_once_and_kept(tmp_path):
    shared, asked = _shared(tmp_path), []
    stock_names._asked.clear()
    with closing(sqlite3.connect(shared / 'orders.sqlite3')) as db, db:
        db.execute('CREATE TABLE fills (symbol TEXT)')
        db.executemany('INSERT INTO fills VALUES (?)', [('295310',), ('999990',), ('114800',)])

    def ask(codes):
        asked.append(codes)
        return [{'symbol': '295310', 'name': '에이치브이엠'}, {'symbol': '000001', 'name': '다른 종목'}]

    assert stock_names.learn(tmp_path, ask, lambda: 0.0) == {'295310': '에이치브이엠'}
    assert stock_names.learn(tmp_path, ask, lambda: 60.0) == {}  # 999990 rests ten minutes
    stock_names.learn(tmp_path, ask, lambda: 601.0)
    assert asked == [['295310', '999990'], ['999990']]
    assert stock_names.load(tmp_path)['295310'] == '에이치브이엠' and '000001' not in stock_names.load(tmp_path)


def test_a_failed_question_changes_nothing(tmp_path):
    shared = _shared(tmp_path)
    stock_names._asked.clear()
    with closing(sqlite3.connect(shared / 'orders.sqlite3')) as db, db:
        db.execute('CREATE TABLE fills (symbol TEXT)')
        db.execute("INSERT INTO fills VALUES ('295310')")

    def ask(_):
        raise OSError('down')

    assert stock_names.learn(tmp_path, ask) == {} and not (shared / stock_names.FILE).exists()
