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
    assert '005930 삼성전자 · 매수 2주 · 70,000원' in data.answer(data.InternalQuery('fills'), tmp_path, now)
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


def test_explanations_and_market_questions_stay_out_of_the_internal_reader():
    from liner_topics import groups

    misread = {q: r for items in groups().values() for q in items if (r := data.resolve(q))}
    assert misread == {}
    for question in (
        'Jev의 최대 낙폭은 어느 화면에서 확인할 수 있나요?',  # the empty chat's own example
        'Jev 낙폭과대 반등 조건 알려줘', '체결강도 알려줘', '오늘 체결강도 높은 종목은?', '워런 버핏 보유 종목은?',
        '현재 버핏 보유종목 알려줘', 'KODEX 200 보유종목 알려줘', '외국인 보유 비중 알려줘',
        '레버리지 ETF 장기 보유 위험 알려줘', '삼성전자를 계속 보유할 이유가 있어?', '선물 포지션이란?',
        '외국인 선물 포지션은?', '운영 상태 화면이 어디야?', '국내 계좌 수익에 세금은?', 'Jev는 왜 낙폭이 커졌나요?',
        'Jev 포지션 크기는 어떻게 정해?', '미체결이 뭐야?',
    ):
        assert data.resolve(question) is None, question


def test_own_records_still_route_to_the_internal_reader():
    live, paper = data.InternalQuery('positions', account='live-1'), data.InternalQuery('positions')
    for question, expected in {
        **{q: None for q in data.SUPPORTED_QUESTIONS},
        '지금 보유하고 있는 종목은?': paper, '뭐 보유하고 있어?': paper, '보유 종목 알려줘': paper, '모의 포지션은?': paper,
        '내 보유 종목 보여줘': paper, '실계좌 1 평가금액은?': live, '계좌 1 잔고는?': live,
        '실계좌 1 잔고 5개만 보여줘': live, '오늘 실계좌 1 체결 종목은 무엇인가요?': data.InternalQuery('fills', account='live-1'),
        'Jev 수익률 어떻게 나왔어?': data.InternalQuery('performance'),
    }.items():
        found = data.resolve(question)
        assert found is not None and found.kind != 'unsupported', question
        assert expected is None or found == expected, question


def test_unsupported_scope_is_said_instead_of_answering_with_today():
    for question in ('최근 일주일 Jev 수익률은?', '누적 Jev 수익률은?', '그제 모의 체결 내역', '월요일 체결 내역 보여줘',
                     '3개월 Jev 수익률은?', '오늘 실계좌 1 미체결은?', '미체결 내역 보여줘', '실계좌 1, 2 잔고는?',
                     'Jev 인버스 손익은?', 'Jev 낙폭과대 성과는?'):
        assert data.resolve(question) == data.InternalQuery('unsupported'), question


def test_follow_ups_understand_every_amount_the_balance_answer_shows():
    live = data.InternalQuery('positions', account='live-1')
    assert data.resolve('2번은?', previous=live) == data.InternalQuery('positions', account='live-2')
    for question in ('주문가능금액은?', '보유 평가금액은?', '잔고는 얼마야?', '총자산은요?', '예수금 알려줘'):
        assert data.resolve(question, previous=live) == live, question
    fills = data.InternalQuery('fills')
    assert data.resolve('지난주는요?', previous=fills) == data.InternalQuery('fills', '지난주')


def test_fills_show_readable_times_and_known_names(tmp_path):
    root = tmp_path / 'shared-etf-paper'
    root.mkdir()
    with sqlite3.connect(root / 'orders.sqlite3') as db:
        db.execute('CREATE TABLE fills (time TEXT, symbol TEXT, side TEXT, quantity INTEGER, price INTEGER)')
        db.executemany('INSERT INTO fills VALUES (?, ?, ?, ?, ?)', [
            ('2026-11-21T09:15:07.763032+09:00', '005930', 'BUY', 2, 70000),
            ('2026-11-21T09:16:00+09:00', '999999', 'SELL', 1, 1000)])
    db.close()
    text = data.answer(data.InternalQuery('fills'), tmp_path, datetime(2026, 11, 21, 12, tzinfo=ZoneInfo('Asia/Seoul')))
    assert '- 2026-11-21 09:15:07 · 005930 삼성전자 · 매수 2주 · 70,000원' in text
    assert '- 2026-11-21 09:16:00 · 999999 · 매도 1주' in text


def test_an_empty_or_broken_record_file_is_reported_not_raised(tmp_path):
    root = tmp_path / 'shared-etf-paper'
    root.mkdir()
    now = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo('Asia/Seoul'))
    for content in (b'', b'not a database' * 100):
        (root / 'orders.sqlite3').write_bytes(content)
        (root / 'account-state.json').write_bytes(content)
        for kind in ('fills', 'positions'):
            assert '읽을 수 없습니다' in data.answer(data.InternalQuery(kind), tmp_path, now)


Q = data.InternalQuery


def test_review_account_numbers():
    for question, expected in {
        '2번 계좌 체결 내역': Q('fills', account='live-2'), '1번 계좌 잔고는?': Q('positions', account='live-1'),
        '실계좌 1번 잔고': Q('positions', account='live-1'), '실계좌 1 2주 이상 보유 종목': Q('positions', account='live-1'),
        '실계좌 1번과 2번 잔고': Q('unsupported'), '실계좌 1번이랑 2번 보유종목': Q('unsupported'), '실계좌 1 2 잔고': Q('unsupported'),
        '장기투자 체결 내역': Q('unsupported'), 'NXT 가상 체결 내역 알려줘': Q('unsupported'), '장기투자 계좌 보유종목 알려줘': Q('unsupported'),
    }.items():
        assert data.resolve(question) == expected, question
    for question in ('퇴직연금 계좌 2개 보유하고 있어', '계좌 2개 보유종목 알려줘'):
        found = data.resolve(question)
        assert found is None or not found.account.startswith('live'), question
    assert data.resolve('2025', previous=Q('positions', account='live-1')) is None


def test_review_periods():
    for question, period in {'저번 주 Jev 수익률은?': '지난주', '저번달 Jev 손익': '지난달', '금주 Jev 수익률은?': '이번주',
                             '전날 체결 내역': '어제', '금일 체결 내역': '오늘'}.items():
        assert data.resolve(question).period == period and data.resolve(question).kind != 'unsupported', question
    for question in ('주간 Jev 수익률은?', '최근 Jev 수익률은?', 'Jev 전체 수익률은?', 'Jev 총 수익률은?', '이틀 전 체결 내역', '11/20 체결 내역'):
        assert data.resolve(question) == Q('unsupported'), question


def test_review_market_and_explanation_questions_go_out():
    for question in (
        '오늘 계약 체결 공시 종목은?', '한화 공급계약 체결 종목 알려줘', '한미 FTA 체결 내역 알려줘', '외국인 매매내역 알려줘', '시간외 체결 종목 알려줘',
        'SK하이닉스 오늘 체결가', '우리금융 보유 종목 알려줘', '우리은행 주요 주주 보유 현황은?', 'ETF내 보유종목', '외국인 몇 주 보유중',
        '삼성전자 몇 주 보유하고 있어야 대주주야?', '제브라테크놀로지 수익률', '원전 운영상태 알려줘', '실계좌 예수금이 뭐야?',
        '실계좌 예수금과 주문가능금액 차이는?', '실계좌 체결 원리 설명해줘', '모의 체결과 실제 체결 차이 알려줘', 'Jev 최대 낙폭의 의미는?',
        'Jev 승률 높이는 방법은?', 'Jev 익절 수익률 기준은?', '내 보유종목 뉴스 알려줘', '실행기 상태 코드 설명해줘',
        '이번 주 코스피 수익률은?', '오늘 외국인이 매수한 종목은?', '버핏이 산 종목은?',
    ):
        assert data.resolve(question) is None, question


def test_review_own_questions_stay_in():
    for question, expected in {
        '오늘 손익 얼마야?': Q('performance'), '이번 주 수익률은?': Q('performance', '이번주'), '모의 수익률은?': Q('performance'),
        '오늘 매수한 종목은?': Q('fills'), '실계좌 1에서 오늘 산 종목 알려줘': Q('fills', account='live-1'), '오늘 뭐 샀어?': Q('fills'),
        'Jev 지금 뭐 들고 있어?': Q('positions'), '실행기 멈췄어?': Q('status'), '모의계좌 총자산은?': Q('unsupported'),
        '오늘 기준 Jev 수익률': Q('performance'), 'Jev와 KODEX 200 수익률 차이는?': Q('performance', compare_kodex=True),
        'Jev 수익률 코스닥150과 비교': Q('unsupported'), 'Jev와 장기투자 수익률 비교': Q('unsupported'),
        '오늘 체결 종목이 뭐야?': Q('fills'), '삼성전자 몇 주 보유하고 있어?': Q('positions'),
    }.items():
        assert data.resolve(question) == expected, question


def test_review_follow_ups():
    live, fills, perf = Q('positions', account='live-1'), Q('fills', '어제'), Q('performance', '이번주')
    for previous in (live, fills, perf, Q('status')):
        assert data.resolve('다시 조회해줘', previous=previous) == previous
    for question in ('2번 계좌', '실계좌 2로 해줘', '2번'):
        assert data.resolve(question, previous=live) == Q('positions', account='live-2'), question
    assert data.resolve('현금은?', previous=live) == live
    assert data.resolve('오늘 체결 내역은?', previous=live) == Q('fills', account='live-1')
    assert data.resolve('오늘 모의 체결 내역은?', previous=live) == Q('fills')
    assert data.resolve('지금 실행기 상태는?', previous=live) == Q('status')
    assert data.resolve('이번 주 수익률은?', previous=live) == Q('unsupported')
    assert data.resolve('저번 주는?', previous=perf) == Q('performance', '지난주')
    assert data.resolve('최대 낙폭은?', previous=perf) == perf
    assert data.resolve('KODEX 200과 비교해줘', previous=perf) == Q('performance', '이번주', compare_kodex=True)


def test_general_questions_that_only_start_like_a_record_go_out():
    for question in (
        '수익률 높은 ETF 추천해줘', '오늘 수익률 좋은 종목은?', '손익비 좋은 전략은?', '승률 높은 매매법 추천', '성과급 세금은 얼마야', '최대낙폭 큰 ETF는?',
        '수익률 1위 펀드', '수익 실현은 언제 하는 게 좋아?', '포지션 사이징 알려줘', '포지션 트레이딩 장단점', '보유 종목 배당 일정',
        '테슬라 주식 일론 머스크가 몇 주 보유하고 있어?', '체결 알림 설정 알려줘', '손익분기점 계산해줘',
    ):
        assert data.resolve(question) is None, question
    for question, kind in {
        '수익률은?': 'performance', '오늘 손익': 'performance', '이번 달 최대 낙폭 얼마야?': 'performance', '승률 알려줘': 'performance', '오늘 성과 어때?': 'performance',
        '수익률과 최대 낙폭은?': 'performance', '포지션은?': 'positions', '보유 현황': 'positions', '지금 보유 종목 좀 보여줘': 'positions',
        '현재 보유 중인 종목이 뭐야?': 'positions', '오늘 산 종목은?': 'fills', '오늘 체결 알려줘': 'fills', '수익 얼마야?': 'performance',
    }.items():
        assert getattr(data.resolve(question), 'kind', None) == kind, question
