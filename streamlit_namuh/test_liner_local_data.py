from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import liner_local_data as data


def test_query_routes_and_guidance():
    performance = data.resolve('이번 주 Jev 모의 성과는?')
    assert performance == data.InternalQuery('performance', '이번주')
    assert data.resolve('Jev 수익률이 어떻게 돼?') == data.InternalQuery('performance')
    assert data.resolve('오늘 실계좌 1 체결 내역은?') == data.InternalQuery('fills', '오늘', 'live-1')
    unspecified = data.resolve('오늘 실제 체결한 종목은?')
    assert unspecified == data.InternalQuery('fills', '오늘', 'live')
    assert data.resolve('오늘 모의계좌에서 실제 체결한 종목은?') == data.InternalQuery('fills')
    assert data.resolve('실계좌 1은?', previous=unspecified) == data.InternalQuery('fills', '오늘', 'live-1')
    assert data.resolve('Jev 성과는 어디에서 보나요?') is None
    assert data.resolve('실행기 상태는 어떻게 확인해?') is None
    assert data.resolve('Jev 성과는 어떻게 비교해?') is None
    assert data.resolve('실계좌 체결내역 확인하는 법 알려줘') is None
    assert data.resolve('모의계좌 보유현황 조회 방법은?') is None
    assert data.resolve('Jev 성과를 CSV로 내려받고 싶어') is None
    assert data.resolve('이번 주 Jev 수익률과 KODEX 200 비교해줘').compare_kodex
    assert data.resolve('어제 미국 증시는 어떻게 됐어?') is None
    assert data.resolve('2025년 미국 ETF 수익률은?') is None
    assert data.resolve('오늘과 어제의 Jev 수익률을 비교해 줘') == data.InternalQuery('unsupported')
    assert data.resolve('오늘 실계좌 1과 실계좌 2 체결 내역은?') == data.InternalQuery('unsupported')
    assert data.resolve('모의계좌 보유 현황은?').kind == 'positions'
    live = data.resolve('실계좌 1의 현재 보유 종목과 잔고는?')
    assert live == data.InternalQuery('positions', account='live-1')
    assert data.resolve('내 계좌 예수금은 얼마야?') == data.InternalQuery('positions', account='live')
    assert data.resolve('총자산은?', previous=live) == live
    assert data.resolve('실계좌 2는?', previous=live) == data.InternalQuery('positions', account='live-2')
    assert data.resolve('지난주 실계좌 1 잔고는?') == data.InternalQuery('unsupported')
    last_week = data.resolve('지난주는?', previous=performance)
    assert last_week == data.InternalQuery('performance', '지난주')
    assert data.resolve('이번 달은?', previous=last_week) == data.InternalQuery('performance', '이번달')
    assert data.resolve('지난달 모의계좌 보유 종목은?') == data.InternalQuery('unsupported')


def test_missing_data_and_status_are_explicit(tmp_path, monkeypatch):
    import automation_status
    monkeypatch.setattr(automation_status, 'processes', lambda max_age: {'공유 모의 실행기': '실행 중'})
    now = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo('Asia/Seoul'))
    assert '실행 중' in data.answer(data.InternalQuery('status'), tmp_path, now)
    assert '확인할 수 없습니다' in data.answer(data.InternalQuery('positions', account='live-1'), tmp_path, now)
    assert '확인할 수 없습니다' in data.answer(data.InternalQuery('performance'), tmp_path, now)
    assert '원장을 확인할 수 없습니다' in data.answer(data.InternalQuery('fills'), tmp_path, now)


def test_saved_paper_snapshot_and_bounded_fills(tmp_path):
    root = tmp_path / 'shared-etf-paper'
    root.mkdir()
    (root / 'account-state.json').write_text(json.dumps({'time': '2026-11-21T09:00:00+09:00',
        'holdings': {'005930': {'iem_nm': '삼성전자', 'rsdl_qty': 2}}}))
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills (time TEXT, symbol TEXT, side TEXT, quantity INTEGER, price INTEGER)')
        db.execute('INSERT INTO fills VALUES (?, ?, ?, ?, ?)', ('2026-11-21T09:15:00+09:00', '005930', 'BUY', 2, 70000))
    now = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo('Asia/Seoul'))
    positions = data.answer(data.InternalQuery('positions'), tmp_path, now)
    assert '삼성전자: 2주' in positions and '2026-11-21 09:00:00' in positions
    assert '005930 · 매수 2주 · 70,000원' in data.answer(data.InternalQuery('fills'), tmp_path, now)
    assert data.resolve('2025년 Jev 수익률 알려줘').kind == 'unsupported'


def test_performance_reuses_dashboard_return_and_refreshes_on_wal_write(tmp_path):
    from contextlib import closing

    import strategy_compare

    now = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo('Asia/Seoul'))
    query = data.InternalQuery('performance', '이번주')
    with closing(sqlite3.connect(tmp_path / strategy_compare.FILE)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute(strategy_compare.SCHEMA)
        pair = ('INSERT INTO compare_daily (day,book,pnl_day,deployed,source,built,bench) VALUES (?,?,?,?,?,?,?)')
        db.executemany(pair, [
            ('2026-11-16', 'jev', 1000, 10000, '공유 모의 원장', '2026-11-21 11:00', '{"069500": 2}'),
            ('2026-11-15', 'jev', 99999, 10000, '공유 모의 원장', '2026-11-21 11:00', '{}'),
            ('2026-11-16', 'longterm', 99999, 10000, '장기 가상 계좌', '2026-11-21 11:00', '{}'),
        ])
        db.commit()
        assert '수익률 +10.00%' in data.answer(query, tmp_path, now)
        db.execute(pair, ('2026-11-20', 'jev', -2000, 10000, '공유 모의 원장', '2026-11-21 12:00', '{"069500": -1}'))
        db.commit()
        refreshed = data.answer(query, tmp_path, now)
        assert '수익률 -10.00%' in refreshed and '최대 낙폭 -18.18%' in refreshed
        assert '기록 2026-11-16 ~ 2026-11-20' in refreshed
        comparison = data.answer(data.InternalQuery('performance', '이번주', compare_kodex=True), tmp_path, now)
        assert 'KODEX 200 등락률 +0.98%' in comparison and '차이 -10.98%p' in comparison


def test_live_fills_are_scoped_to_confirmed_account_and_latest_twenty(tmp_path):
    from contextlib import closing

    from accounts import DEFAULT_BUDGET
    import live_fills

    now = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo('Asia/Seoul'))
    (tmp_path / 'accounts.json').write_text(json.dumps({'accounts': {
        'live-1': {'kind': 'live', 'engine': 'jev', 'auto': False, 'budget': DEFAULT_BUDGET['live']},
    }}))
    with closing(sqlite3.connect(tmp_path / live_fills.NAME)) as db, db:
        db.execute(live_fills.SCHEMA)
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', [
            ('live-1', '2026-11-21', i, f'AA{i:02d}', 'BUY', 1, 1000,
             '2026-11-21T00:00:00+09:00', '2026-11-21T10:15:00+09:00') for i in range(22)
        ] + [('live-2', '2026-11-21', 23, 'OTHER', 'SELL', 2, 1000,
              '2026-11-21T09:00:00+09:00', '2026-11-21T10:15:00+09:00')])
    output = data.answer(data.InternalQuery('fills', account='live-1'), tmp_path, now)
    assert '실계좌 1 확인 체결 기록' in output and '시각 미기록' in output
    assert 'OTHER' not in output and 'AA01' not in output and 'AA21' in output
    assert output.count(' · 매수 ') == 20
    assert '해당 실계좌를 확인할 수 없습니다' in data.answer(data.InternalQuery('fills', account='live-99'), tmp_path, now)
