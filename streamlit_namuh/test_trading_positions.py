import json
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import trading_positions as t


def test_grouped_symbols_keeps_latest_stop_once_and_preserves_engine_data():
    info = {'holdings': [{'symbol': '005930', 'qty': 17}], 'stops': [
        {'symbol': '005930', 'qty': 100, 'time': '2026-11-16T02:25:00+00:00'},
        {'symbol': '005930', 'qty': 17, 'time': '2026-11-16T11:42:00+09:00'},
        {'symbol': '000660', 'qty': 5, 'time': '2026-11-16T11:30:00+09:00'}]}
    groups = t.grouped_symbols(info)
    assert len(groups) == 2
    assert t.group_label(groups[0], {'005930': '삼성전자'}) == '삼성전자 (005930) · 보유 17주 · 오늘 손절 17주 · 11:42 외 1건'
    assert len(info['stops']) == 3


def test_confirmed_fill_and_unknown_signal(tmp_path):
    root = tmp_path / 'book'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INT,price REAL,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', [
            ('a', '005930', 'BUY', 2, 100, '2026-11-16T09:00:00+09:00'),
            ('b', '005930', 'SELL', 1, 99, '2026-11-16T09:01:00+09:00'),
            ('c', '005930', 'SELL', 1, 98, '2026-11-16T09:02:00+09:00'),
            ('d', '005930', 'BUY', 1, 97, '2026-11-16T09:03:00+09:00')])
        db.executemany('INSERT INTO decision_order_links VALUES (?,?)', [('b', 'a'*32), ('c', 'b'*32)])
    (root / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in [
        {'event':'v2_risk_exit','cycle_id':'a'*32,'symbol':'005930','reason':'stop_loss'},
        {'event':'v2_risk_exit','cycle_id':'b'*32,'symbol':'000660','reason':'stop_loss'}]))
    t.snapshot.clear()
    with patch.object(t, 'ledger_root', return_value=root):
        result = t.snapshot(tmp_path, datetime(2026, 11, 16, 10, tzinfo=ZoneInfo('Asia/Seoul')))['jev']
    assert result['status'] == 'ok'
    assert result['holdings'][0]['qty'] == 1
    assert len(result['stops']) == 1 and result['stops'][0]['qty'] == 1
    assert result['unknown_sells'] == 1


def test_missing_ledger_is_unavailable(tmp_path):
    t.snapshot.clear()
    with patch.object(t, 'ledger_root', return_value=tmp_path):
        assert t.snapshot(tmp_path)['jev']['status'] == 'unavailable'


def test_only_the_running_intraday_engine_is_read(tmp_path):
    t.snapshot.clear()  # the intraday vibe book is frozen since 2026-11-17; the long-term engine has its own tab
    with patch.object(t, 'ledger_root', return_value=tmp_path):
        assert set(t.snapshot(tmp_path)) == {'jev'}


def test_profit_and_loss_use_confirmed_cost_even_for_trailing_stops(tmp_path):
    root = tmp_path / 'book'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INT,price REAL,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', [
            ('a', '005930', 'BUY', 3, 100, '2026-11-16T09:00:00+09:00'),
            ('b', '005930', 'SELL', 1, 110, '2026-11-16T09:01:00+09:00'),
            ('c', '005930', 'SELL', 1, 90, '2026-11-16T09:02:00+09:00'),
            ('d', '005930', 'SELL', 1, 100, '2026-11-16T09:03:00+09:00')])
        db.executemany('INSERT INTO decision_order_links VALUES (?,?)', [('b', 'a'*32), ('c', 'b'*32)])
    (root / 'events.jsonl').write_text('\n'.join(json.dumps({'event': 'v2_risk_exit', 'cycle_id': c*32,
        'symbol': '005930', 'reason': 'trailing_stop'}) for c in 'ab'))
    t.snapshot.clear()
    with patch.object(t, 'ledger_root', return_value=root):
        result = t.snapshot(tmp_path, datetime(2026, 11, 16, 10, tzinfo=ZoneInfo('Asia/Seoul')))['jev']
    assert [row['outcome'] for row in result['sells']] == ['본전 매도', '손절', '익절']
    assert len(result['stops']) == 1 and result['stops'][0]['price'] == 90
    assert len(result['recent_stops']) == 1
    group = t.grouped_symbols(result)[0]
    assert '오늘 본전 매도 1주 · 09:03 외 2건' in t.group_label(group, {})


def test_missing_events_keep_holdings_and_do_not_label_loss_as_stop(tmp_path):
    root = tmp_path / 'book'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INT,price REAL,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', [
            ('a', '005930', 'BUY', 2, 100, '2026-11-16T09:00:00+09:00'),
            ('b', '005930', 'SELL', 1, 90, '2026-11-16T09:01:00+09:00')])
        db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('b', 'a'*32))
    t.snapshot.clear()
    with patch.object(t, 'ledger_root', return_value=root):
        result = t.snapshot(tmp_path, datetime(2026, 11, 16, 10, tzinfo=ZoneInfo('Asia/Seoul')))['jev']
    assert result['status'] == 'ok'
    assert result['holdings'][0]['qty'] == 1
    assert result['sells'][0]['outcome'] == '손실 매도'
    assert result['stops'] == [] and result['unknown_sells'] == 1



def test_generic_risk_is_unknown_until_stop_reason_identified(tmp_path):
    root = tmp_path / 'book'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INT,price REAL,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', [
            ('a', '005930', 'BUY', 1, 100, '2026-11-16T09:00:00+09:00'),
            ('b', '005930', 'SELL', 1, 99, '2026-11-16T09:01:00+09:00')])
        db.execute('INSERT INTO decision_order_links VALUES (?,?)', ('b', 'a'*32))
    (root / 'events.jsonl').write_text(json.dumps({'event': 'sell_submitted', 'cycle_id': 'a'*32,
                                                  'symbol': '005930', 'trigger': 'risk'}))
    t.snapshot.clear()
    with patch.object(t, 'ledger_root', return_value=root):
        result = t.snapshot(tmp_path, datetime(2026, 11, 16, 10, tzinfo=ZoneInfo('Asia/Seoul')))['jev']
    assert result['unknown_sells'] == 1
    assert result['stops'] == []


def test_recent_stops_include_six_previous_calendar_days_and_exclude_future(tmp_path):
    root = tmp_path / 'book'
    root.mkdir()
    now = datetime(2026, 11, 16, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INT,price REAL,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        for i, stamp in enumerate(('2026-11-09T09:00:00+09:00', '2026-11-10T09:00:00+09:00',
                                   '2026-11-16T09:30:00+09:00', '2026-11-16T11:00:00+09:00')):
            db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?)', (f'b{i}', '005930', 'BUY', 1, 100, stamp))
            db.execute('INSERT INTO fills VALUES(?,?,?,?,?,?)', (f's{i}', '005930', 'SELL', 1, 99, stamp))
            db.execute('INSERT INTO decision_order_links VALUES (?,?)', (f's{i}', str(i)*32))
    (root / 'events.jsonl').write_text('\n'.join(json.dumps({'event': 'v2_risk_exit', 'cycle_id': str(i)*32,
        'symbol': '005930', 'reason': 'stop_loss'}) for i in range(4)))
    t.snapshot.clear()
    with patch.object(t, 'ledger_root', return_value=root):
        result = t.snapshot(tmp_path, now)['jev']
    assert [row['time'][:10] for row in result['recent_stops']] == ['2026-11-16', '2026-11-10']
    assert len(result['stops']) == 1


def test_the_journal_is_scanned_again_only_after_the_ledger_or_journal_changes(tmp_path):
    # a 2-second TTL rescanned the 39 MB journal on nearly every rerun of every tab (sidebar), ~49 ms each
    root = tmp_path / 'book'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills(order_id TEXT,symbol TEXT,side TEXT,quantity INT,price REAL,time TEXT)')
        db.execute('CREATE TABLE decision_order_links(order_id TEXT,cycle_id TEXT)')
        db.executemany('INSERT INTO fills VALUES(?,?,?,?,?,?)', [('a', '005930', 'BUY', 2, 100, '2026-11-16T09:00:00+09:00'),
                                                                 ('b', '005930', 'SELL', 1, 99, '2026-11-16T09:01:00+09:00')])
        db.execute("INSERT INTO decision_order_links VALUES ('b', ?)", ('a' * 32,))
    events = root / 'events.jsonl'
    events.write_text(json.dumps({'event': 'v2_risk_exit', 'cycle_id': 'a' * 32, 'symbol': '005930', 'reason': 'take_profit'}) + '\n')
    now = datetime(2026, 11, 16, 10, tzinfo=ZoneInfo('Asia/Seoul'))
    scans = []
    real = t.ec._cycles
    with patch.object(t, 'ledger_root', return_value=root), \
         patch.object(t.ec, '_cycles', side_effect=lambda *a: scans.append(1) or real(*a)):
        first = t.snapshot(tmp_path, now)['jev']
        time.sleep(2.1)  # longer than the old 2-second TTL
        assert t.snapshot(tmp_path, now)['jev'] == first and len(scans) == 1  # files unchanged: no rescan
        with sqlite3.connect(root / 'orders.sqlite3') as db:
            db.execute("INSERT INTO fills VALUES ('c', '005930', 'SELL', 1, 98, '2026-11-16T09:05:00+09:00')")
        again = t.snapshot(tmp_path, now)['jev']
    assert len(scans) == 2 and again['holdings'] == []  # the new sale is seen at once
