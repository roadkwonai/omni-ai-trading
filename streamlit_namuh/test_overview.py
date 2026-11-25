import json
import tempfile
import unittest
from pathlib import Path

import live_labels as L
import overview


class OverviewTests(unittest.TestCase):
    def test_alerts_name_pending_requests_live_blocks_and_failing_runners(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'shared-etf-paper').mkdir()
            (root / 'shared-etf-paper' / 'exit-requests.json').write_text('["114800"]')
            (root / 'live-exit-requests-live-1.json').write_text('["005930"]')
            (root / 'live-follow.jsonl').write_text(json.dumps({'time': '2026-11-13T10:00:00+09:00', 'account': 'live-2',
                                                                'action': 'BLOCK'}) + '\n')
            found = overview.alerts(root, {'공유 모의 ETF 실행기': '확인 불가', '실계좌 1 미러 실행기': '실행 중'}, {}, '2026-11-13')
        self.assertEqual(found, ['공유 모의 ETF 실행기: 확인 불가', '정리 요청 대기 · 모의계좌: 114800',
                                 '정리 요청 대기 · live-1: 005930', 'live-2 미러 차단·오류 1회 (운영 → AI·실행 기록에서 확인)'])

    def test_an_open_mirror_lag_alert_is_listed_first(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'live-follow').mkdir()
            (root / 'live-follow' / 'lag-alert-live-1.json').write_text(json.dumps(
                {'since': '2026-11-17T12:11:58+09:00', 'minutes': 13.0, 'excess': {'114800': 496},
                 'last_action': 'BLOCK', 'last_reason': 'market data stale or market closed'}))
            found = overview.alerts(root, {}, {}, '2026-11-17')
            stale = overview.alerts(root, {}, {}, '2026-11-18')
        self.assertEqual(found, ["실계좌 미러 지연 · live-1: 모의보다 더 보유 {'114800': 496} · 13.0분째 (market data stale or market closed)"])
        self.assertEqual(stale, [])

    def test_activity_counts_judgments_and_plans_per_ten_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'jev').mkdir()
            (root / 'jev' / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in [
                {'time': '2026-11-13T09:51:00+09:00', 'event': 'jev_shadow'}, {'time': '2026-11-13T09:58:00+09:00', 'event': 'jev_shadow'},
                {'time': '2026-11-13T10:01:00+09:00', 'event': 'submitted'}, {'time': '2026-11-12T10:01:00+09:00', 'event': 'submitted'}]))
            (root / 'live-follow.jsonl').write_text(json.dumps({'time': '2026-11-13T09:52:00+09:00', 'account': 'live-1', 'action': 'BUY'}) + '\n')
            rows = overview.activity(root, '2026-11-13', root / 'jev')
        self.assertCountEqual(rows, [{'구분': 'Jev 판단', '시각': '09:50', '건수': 2}, {'구분': 'live-1 매매 계획', '시각': '09:50', '건수': 1},
                                {'구분': '엔진 주문', '시각': '10:00', '건수': 1}])

    def test_live_labels_translate_plan_values(self):
        self.assertEqual((L.ACTIONS['HOLD'], L.EXECUTIONS['DRY_RUN'], L.STATES['terminal']), ('유지', '계획만 기록(관찰)', '완료'))
        self.assertEqual(L.reason('2% stop from average price'), '평균가 대비 2% 손절')
        self.assertEqual(L.reason('within 1% of the paper fill'), '엔진 체결가 +1% 이내')
        self.assertEqual(L.reason('not bought by the paper runner today or price moved past its fill'),
                         '오늘의 새 매수 또는 기존 보유 조정 조건·가격 확인 대기')
        self.assertEqual((L.reason('something new'), L.day('20261106')), ('something new', '2026-11-06'))


    def test_timeline_merges_judgments_orders_and_live_plan_changes(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for folder in ('jev', 'vibe'):
                (root / folder).mkdir()
                (root / folder / 'events.jsonl').write_text('')
            (root / 'jev' / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in [
                {'time': '2026-11-13T09:50:27+09:00', 'event': 'jev_shadow', 'jev': {'candidate': {'choice': 'WAIT', 'confidence': 0.31}}},
                {'time': '2026-11-13T09:50:28+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'volume_not_rising'},
                {'time': '2026-11-13T09:50:31+09:00', 'event': 'submitted', 'symbol': '005930', 'qty': 4, 'limit': 213500,
                 'source': 'watchlist_strength'}]))
            (root / 'live-follow.jsonl').write_text(''.join(json.dumps({'time': f'2026-11-13T09:5{i}:00+09:00', 'account': 'live-1',
                                                                         'action': a, 'reason': 'paper runner bought'}) + '\n'
                                                            for i, a in enumerate(['BUY', 'BUY', 'HOLD'])))
            with mock.patch('engine_roles.ledger_root', side_effect=lambda r, e: Path(r) / e), \
                    mock.patch.object(overview.ec, 'cached_journal', return_value=[]):
                rows, waits = overview.timeline(root, '2026-11-13', {'005930': '삼성전자'})
        self.assertEqual(waits, {'거래량 증가 없음': 1})
        self.assertEqual([(r['출처'], r['종류']) for r in rows],
                         [('실계좌 1', '실계좌 계획'), ('Jev', '판단'), ('Jev 실행기', '매수 주문'), ('실계좌 1', '실계좌 계획')])
        self.assertEqual(rows[2]['내용'], '4주 · 지정가 213,500원 · 관심종목 강도')  # the repeated 09:51 BUY plan is folded


    def test_accounts_list_the_paper_account_and_no_frozen_vibe_book(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch('engine_roles.ledger_root', side_effect=lambda r, e: Path(r) / e), \
                    mock.patch('engine_roles.main_engine', return_value='jev'), \
                    mock.patch.object(overview.ec, 'cached_journal', return_value=[]):
                items = overview.accounts(root, {}, __import__('datetime').datetime.now().astimezone())
        self.assertEqual([i['key'] for i in items], ['jev'])


class HoldingSpanTests(unittest.TestCase):
    def test_round_trips_split_and_open_position_runs_to_now(self):
        import overview
        row = lambda t, side, qty, pnl=None, name='A': {'time': t, 'side': side, 'qty': qty, 'pnl': pnl, '종목': name}
        spans = overview.holding_spans([row('09:10', 'BUY', 2), row('09:20', 'SELL', 1, 50), row('09:30', 'SELL', 1, -80),
                                        row('10:00', 'BUY', 1), row('10:05', 'SELL', 1, 0), row('11:00', 'BUY', 3, name='B')], '12:00')
        self.assertEqual([(s['종목'], s['start'], s['end'], s['결과']) for s in spans],
                         [('A', '09:10', '09:30', '손실'), ('A', '10:00', '10:05', '본전'), ('B', '11:00', '12:00', '보유 중')])
        self.assertEqual(spans[0]['pnl'], -30)

class ErrorTextTests(unittest.TestCase):
    def test_errors_read_in_korean_and_say_when_nothing_was_ordered(self):
        import overview
        self.assertEqual(overview.error_text({'event': 'v2_error', 'reason': 'transient_read_failure', 'detail': 'quote unavailable'}),
                         '실행기 주기 실패 · 시세 조회 실패 · 주문 없음, 다음 주기 재시도')
        self.assertIn('상세 미기록', overview.error_text({'event': 'v2_error', 'reason': 'transient_read_failure'}))
        self.assertEqual(overview.error_text({'event': 'v2_holding_decision_error', 'error_type': 'IntegrityError'}),
                         '보유 판단 실패(이번 주기 보유 유지) · 기록 DB 동시 쓰기 충돌')
        self.assertEqual(overview.error_text({'event': 'odd_error'}), 'odd_error')

if __name__ == '__main__':
    unittest.main()


def test_alerts_include_failing_apis_of_today_only(tmp_path, monkeypatch):
    from datetime import datetime
    import api_status_view
    import overview
    monkeypatch.setattr(api_status_view, 'current_alerts', lambda now=None: ['API 키움 REST 실패: 최근 30분 4건 중 4건 실패'])
    today = datetime.now().astimezone().strftime('%Y-%m-%d')
    assert 'API 키움 REST 실패: 최근 30분 4건 중 4건 실패' in overview.alerts(tmp_path, {}, {}, today)
    assert not any(line.startswith('API ') for line in overview.alerts(tmp_path, {}, {}, '2026-01-02'))


def test_long_term_virtual_account_row_and_its_alerts(tmp_path):
    import vibe_longterm as LT
    root = tmp_path / 'vibe-longterm'
    assert overview.longterm_book(root) is None and overview.longterm_alerts(root, '2026-11-18') == []
    db = LT.connect(root)
    LT.put_state(db, last_plan={'day': '2026-11-18', 'why': 'initial_deployment', 'orders': [{'key': 'NDX'}]},
                 story={'stories': {'QUANT': {'status': 'broken', 'tilt': 0, 'reason': 'x'}, 'SEMI': {'status': 'intact', 'tilt': 0, 'reason': 'y'}}},
                 plan={'day': '2026-11-18', 'macro': {'errors': ['fomc: OSError']}})
    LT.log(db, 'skip', '2026-11-18', reason='시황 자료 없음: ValueError')
    LT.log(db, 'skip', '2026-11-17', reason='어제 건너뜀')
    row = overview.longterm_book(root)
    assert row['label'] == '장기투자 가상 계좌' and row['mode'] == '관찰' and row['rows'] == [] and '초기 투입 · 주문 1건(계획만)' in row['last']
    found = overview.longterm_alerts(root, '2026-11-18')
    assert found == ['장기투자 판단 건너뜀: 시황 자료 없음: ValueError', '장기투자 스토리 훼손: SOL 미국양자컴퓨팅TOP10 (위성 비중 0, 장기투자 → 테마 스토리)',
                     '장기투자 거시 자료 일부 없음: fomc: OSError (운영 → API 연동 → FRED)']
    assert overview.longterm_alerts(root, '2026-11-19')[0].startswith('장기투자 스토리 훼손')  # only today's skip and macro


def test_timeline_merges_long_term_decisions_and_its_live_account(tmp_path, monkeypatch):
    import vibe_longterm as LT
    from datetime import datetime
    db = LT.connect(tmp_path / 'vibe-longterm')
    at = lambda hm: datetime.fromisoformat(f'2026-11-18T{hm}:00+09:00')  # noqa: E731
    LT.log(db, 'regime', '2026-11-18', regime='correction', reason='조정 국면', now=at('08:45'))
    LT.log(db, 'macro', '2026-11-18', reason='하이일드 스프레드 3.23%p', now=at('08:45'))
    LT.log(db, 'plan', '2026-11-18', orders=[{'key': 'NDX', 'side': 'BUY', 'qty': 3}], reason='초기 투입', now=at('09:40'))
    LT.log(db, 'regime', '2026-11-17', regime='bull', reason='어제', now=at('08:45'))
    (tmp_path / 'longterm-live.jsonl').write_text(json.dumps({'time': '2026-11-18T09:41:00+09:00', 'account': 'live-2', 'engine': 'longterm',
                                                              'action': 'PLAN', 'orders': [{}, {}]}) + '\n')
    monkeypatch.setattr('engine_roles.ledger_root', lambda r, e: Path(r) / e)
    monkeypatch.setattr(overview.ec, 'cached_journal', lambda *a: [])
    rows, _ = overview.timeline(tmp_path, '2026-11-18', {})
    lt = [(r['출처'], r['종류'], r['내용']) for r in rows]
    assert lt == [('장기투자', '국면', '조정 · 조정 국면'), ('장기투자', '거시 배경', '하이일드 스프레드 3.23%p'),  # vibe_longterm.DECISION_KINDS
                  ('장기투자', '계획(주문 없음)', '초기 투입 · 1건 (KODEX 미국나스닥100 매수 3)'), ('실계좌 2 장기투자', '실계좌 계획', '그날 주문 계획 · 2건')]


def test_orders_tab_lists_todays_long_term_plans(tmp_path):
    import longterm_live as LL
    import vibe_longterm as LT
    LT.put_state(LT.connect(tmp_path / 'vibe-longterm'), last_plan={'day': '2026-11-18', 'why': 'initial_deployment', 'execute': False,
                                                                   'orders': [{'key': 'SPX', 'side': 'BUY', 'qty': 2, 'price': 21000}]})
    LL.save_state(tmp_path, 'live-2', {'day': '2026-11-18', 'orders': [{'key': 'NDX', 'code': '379810', 'side': 'SELL', 'qty': 1, 'price': 20000,
                                                                        'status': 'dry_run'}]})
    records = {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}, 'live-1': {'kind': 'live', 'engine': 'jev'}}
    rows = overview.longterm_plan_rows(tmp_path, records, '2026-11-18')
    assert [(r['계좌'], r['종목'], r['매수/매도'], r['상태']) for r in rows] == [
        ('장기투자 가상 계좌', 'KODEX 미국S&P500', '매수', '계획만(dry-run)'),
        ('실계좌 2', 'KODEX 미국나스닥100 (379810)', '매도', '계획만(실주문 꺼짐)')]
    assert overview.longterm_plan_rows(tmp_path, records, '2026-11-19') == []


def test_timeline_shows_long_term_dca_decisions(tmp_path, monkeypatch):
    import vibe_longterm as LT
    from datetime import datetime
    db = LT.connect(tmp_path / 'vibe-longterm')
    LT.log(db, 'dca', '2026-11-18', orders=[{'key': 'SPX', 'side': 'BUY', 'qty': 5}], reason='정기 적립 1,000,000원',
           now=datetime.fromisoformat('2026-11-18T10:00:00+09:00'))
    monkeypatch.setattr('engine_roles.ledger_root', lambda r, e: Path(r) / e)
    monkeypatch.setattr(overview.ec, 'cached_journal', lambda *a: [])
    rows, _ = overview.timeline(tmp_path, '2026-11-18', {})
    assert [(r['출처'], r['종류'], r['내용']) for r in rows] == [('장기투자', '적립식', '정기 적립 1,000,000원 · 1건 (KODEX 미국S&P500 매수 5)')]


def test_long_term_lists_skip_invalid_accounts_and_read_the_live_log_once(tmp_path, monkeypatch):
    import longterm_live as LL
    LL.save_state(tmp_path, 'live-3', {'day': '2026-11-18', 'orders': [{'key': 'NDX', 'code': '379810', 'side': 'BUY', 'qty': 1, 'price': 20000,
                                                                        'status': 'dry_run'}]})
    records = {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'},
               'live-3': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 3', 'invalid': True}}
    assert overview.longterm_plan_rows(tmp_path, records, '2026-11-18') == []
    reads = []
    monkeypatch.setattr(overview, 'latest_longterm', lambda runtime: reads.append(runtime) or {})
    monkeypatch.setattr('trading_journal._live_rows', lambda key, label: [])
    many = {f'live-{i}': {'kind': 'live', 'engine': 'longterm', 'label': f'실계좌 {i}'} for i in range(2, 5)}
    rows = overview.accounts(tmp_path, many, __import__('datetime').datetime.now().astimezone())
    assert [r['label'] for r in rows if r['engine'] == '장기투자'] == ['실계좌 2', '실계좌 3', '실계좌 4'] and len(reads) == 1


def test_long_term_card_shows_contributions_beside_nav(tmp_path):
    import vibe_longterm as LT
    LT.put_state(LT.connect(tmp_path / 'vibe-longterm'), plan={'regime': 'bull'},
                 nav_latest={'day': '2026-11-18', 'actual': 101_000_000, LT.CONTRIBUTED: 1_000_000})
    assert overview.longterm_card(tmp_path)['sub'] == '순자산 101,000,000원 · 적립 누계 1,000,000원'


def test_realized_overview_shows_only_todays_sells_of_each_account_and_their_total():
    # user 2026-11-18: this table is today's realized P&L only (longer spans of fill-price sells looked like a profit
    # while the account was below its deposit; the whole gain is 총자산 − 원금 in the balance section)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import overview as O
    now = datetime(2026, 11, 18, 13, 0, tzinfo=ZoneInfo('Asia/Seoul'))
    sell = lambda day, pnl: {'time': f'{day}T10:00:00+09:00', 'side': 'SELL', 'pnl': pnl, 'symbol': '114800'}
    buy = {'time': '2026-11-18T09:30:00+09:00', 'side': 'BUY', 'pnl': None, 'symbol': '114800'}
    items = [{'key': 'jev', 'label': '모의계좌', 'kind': '모의(공유)', 'rows': [sell('2026-11-17', 5_000), buy, sell('2026-11-18', 1_000),
                                                                           sell('2026-11-18', -400)]},
             {'key': 'live-1', 'label': '실계좌 1', 'kind': '실계좌', 'rows': [sell('2026-10-30', 300), sell('2026-11-18', -100)]}]
    rows = O.realized_overview(items, now)
    assert rows[0] == {'계좌': '모의계좌', '구분': '모의(공유)', '오늘 실현': 600, '매도': 2, '승률': '50%'}
    assert rows[-1] == {'계좌': '합계', '구분': '', '오늘 실현': 500, '매도': 3, '승률': '33%'}


def test_balance_overview_measures_the_whole_gain_against_the_money_put_in(tmp_path):
    # user 2026-11-18: each account's balance with its whole +/− (won, %) and the totals, from local records only.
    # The whole gain is 총자산 − 원금 (what was deposited), so fees, taxes and trades outside the ledger are counted;
    # adding up fill-price sells showed a profit while the account was below its deposit.
    import vibe_longterm as LT
    snap = lambda t, total, invested, evaluation: json.dumps({'time': f'2026-11-18T{t}:00+09:00', 'total_assets': total,
                                                              'invested': invested, 'evaluation': evaluation})
    (tmp_path / 'shared-etf-paper').mkdir()
    (tmp_path / 'shared-etf-paper' / 'account-snapshots.jsonl').write_text(
        json.dumps({'time': '2026-11-17T15:00:00+09:00', 'total_assets': 1}) + '\n'
        + snap('09:05', 10_000_000, 1_000_000, 1_000_000) + '\nnot json\n' + snap('12:30', 9_950_000, 1_000_000, 990_000) + '\n')
    (tmp_path / 'live-balance-live-1.jsonl').write_text(snap('09:10', 1_000_000, 500_000, 500_000) + '\n'
                                                         + snap('12:35', 990_000, 500_000, 490_000) + '\n')
    LT.put_state(LT.connect(tmp_path / 'vibe-longterm'),
                 nav_latest={'day': '2026-11-17', 'actual': 102_000_000, LT.CONTRIBUTED: 1_000_000})
    sold = {'time': '2026-11-10T10:00:00+09:00', 'side': 'SELL', 'pnl': 99_999}  # fill-price sells play no part
    items = [{'key': 'jev', 'label': '모의계좌', 'kind': '모의(공유)', 'rows': [sold]},
             {'key': 'longterm-virtual', 'label': '장기투자 가상 계좌', 'kind': '가상 체결', 'rows': []},
             {'key': 'live-1', 'label': '실계좌 1', 'kind': '실계좌', 'rows': [sold, {'time': '2026-11-18T11:00:00+09:00', 'pnl': -700}]},
             {'key': 'live-2', 'label': '실계좌 2', 'kind': '실계좌', 'rows': []},
             {'key': 'live-3', 'label': '실계좌 3', 'kind': '실계좌', 'rows': []}]
    (tmp_path / 'live-balance-live-3.jsonl').write_text(snap('12:40', 300_000, 0, 0) + '\n')
    principals = {'jev': 10_000_000, 'live-1': 1_000_000, 'live-2': 500_000}  # live-3 has none set
    rows = {r['계좌']: r for r in overview.balance_overview(items, tmp_path, '2026-11-18', principals)}
    paper = rows['모의계좌']
    assert (paper['총자산'], paper['원금'], paper['전체 손익'], paper['전체 수익률']) == (9_950_000, 10_000_000, -50_000, -0.5)
    assert (paper['평가손익'], paper['보유 수익률'], paper['오늘 변동'], paper['기준']) == (-10_000, -1.0, -50_000, '12:30')
    long = rows['장기투자 가상 계좌']  # 원금 = the 1억 start and the 적립 paid in; no intraday change
    assert (long['총자산'], long['원금'], long['전체 손익'], long['오늘 변동'], long['기준']) == (102_000_000, 101_000_000, 1_000_000, None, '11-17 마감')
    assert long['전체 수익률'] == round(1_000_000 / 101_000_000 * 100, 4)
    assert (rows['실계좌 1']['전체 손익'], rows['실계좌 1']['전체 수익률']) == (-10_000, -1.0)
    assert (rows['실계좌 1']['오늘 실현'], paper['오늘 실현'], rows['실계좌 합계']['오늘 실현'], rows['전체 합계']['오늘 실현']) == (-700, 0, -700, -700)
    assert rows['실계좌 2']['총자산'] is None and rows['실계좌 2']['기준'] == '기록 없음'
    assert rows['실계좌 3']['총자산'] == 300_000 and rows['실계좌 3']['전체 손익'] is None  # no principal: no whole gain shown
    live = rows['실계좌 합계']  # only the accounts with both a balance and a principal, and it says so
    assert (live['총자산'], live['원금'], live['전체 손익'], live['전체 수익률'], live['구분']) == (1_290_000, 1_000_000, -10_000, -1.0, '1/3 계좌 기준')
    total = rows['전체 합계']
    assert (total['원금'], total['전체 손익']) == (112_000_000, 940_000)
    assert total['전체 수익률'] == round(940_000 / 112_000_000 * 100, 4)


def test_a_signed_card_colours_its_profit_line_red_for_gain_and_blue_for_loss():
    # user 2026-11-18: the balance cards' 수익률 reads red when in profit and blue when at a loss
    import status_cards as sc
    gain = sc.html('실계좌 1', '1,000원', 'up', sub='▲ +10원 (+1.00%)', signed_sub=True)
    loss = sc.html('실계좌 2', '1,000원', 'down', sub='▼ -10원 (-1.00%)', signed_sub=True)
    assert '--a:#ef4444' in gain and 'class="sc-sub sc-signed"' in gain
    assert '--a:#3b82f6' in loss and 'class="sc-sub sc-signed"' in loss
    assert 'sc-signed' not in sc.html('x', 'y', 'up', sub='plain')
    assert '.sc-sub.sc-signed{color:var(--a)' in sc.CSS


def _kst(day, hhmm):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(f'{day}T{hhmm}:00').replace(tzinfo=ZoneInfo('Asia/Seoul'))


def test_a_runner_that_stops_during_the_session_is_an_alert(tmp_path, monkeypatch):
    # the docstring says "stopped executors": in session a dead shared runner left 주의 0건 (green)
    import nh_paper_isolated as runner
    monkeypatch.setattr(runner, '_HOLIDAYS', {2026: (frozenset(), 0.0)})
    states = {'공유 모의 ETF 실행기': '종료', '실계좌 1 미러 실행기': '종료', 'NXT 가상 실행기': '종료'}
    found = overview.alerts(tmp_path, states, {}, '2026-11-18', now=_kst('2026-11-18', '10:30'))
    assert {'공유 모의 ETF 실행기: 종료 (장중)', '실계좌 1 미러 실행기: 종료 (장중)'} <= set(found)
    assert not any(line.startswith('NXT') for line in found)  # runs only at 07:55 and 15:35
    assert overview.alerts(tmp_path, states, {}, '2026-11-18', now=_kst('2026-11-18', '16:00')) == []  # after hours: normal
    assert overview.alerts(tmp_path, states, {}, '2026-11-21', now=_kst('2026-11-21', '10:30')) == []  # Saturday


def test_a_malformed_exit_request_is_ignored_like_the_runner_does(tmp_path):
    (tmp_path / 'shared-etf-paper').mkdir()
    (tmp_path / 'shared-etf-paper' / 'exit-requests.json').write_text('[114800]')  # a number, not a code string
    (tmp_path / 'live-exit-requests-live-1.json').write_text('[1, "005930"]')
    assert overview.alerts(tmp_path, {}, {}, '2026-01-02') == []
    (tmp_path / 'shared-etf-paper' / 'exit-requests.json').write_text('{"114800": 3}')  # {code: shares}
    assert overview.alerts(tmp_path, {}, {}, '2026-01-02') == ['정리 요청 대기 · 모의계좌: 114800']


def test_record_dates_come_from_the_journals_the_timeline_reads(tmp_path):
    # the picker listed only days of the retired vibe_advice history (11-12…11-17): 11-11 and 11-18 could not be picked
    (tmp_path / 'shared-etf-paper').mkdir()
    (tmp_path / 'shared-etf-paper' / 'events.jsonl').write_text(''.join(
        json.dumps({'time': f'{day}T10:00:00+09:00', 'event': 'jev_shadow'}) + '\n' for day in ('2026-11-11', '2026-11-18')))
    (tmp_path / 'live-follow.jsonl').write_text(json.dumps({'time': '2026-11-10T10:00:00+09:00', 'account': 'live-1'}) + '\n')
    assert overview.record_days(tmp_path, '2026-11-19') == ['2026-11-19', '2026-11-18', '2026-11-11', '2026-11-10']


def test_the_paper_card_counts_the_shared_paper_account_alone():
    # the long-term virtual book (kind 가상 체결) has its own tab; its first sale must not move "오늘 실현 · 모의계좌"
    sell = lambda pnl: {'time': '2026-11-18T10:00:00+09:00', 'side': 'SELL', 'pnl': pnl, 'cost': 1000, 'return_pct': 0.0, 'symbol': 'A'}
    items = [{'key': 'jev', 'kind': '모의(공유)', 'rows': [sell(500)]},
             {'key': 'longterm-virtual', 'kind': '가상 체결', 'rows': [sell(90_000)]},
             {'key': 'live-1', 'kind': '실계좌', 'rows': [sell(-200)]}]
    assert overview.today_realized(items, '2026-11-18') == (500, -200)


def test_the_long_term_row_shows_its_holdings_cost_and_return(tmp_path):
    # 매입 was the 1억 + 적립 principal, so 보유 수익률 was the whole account's (+0.08%), not the holdings' own
    import sqlite3
    import vibe_longterm as LT
    root = tmp_path / 'vibe-longterm'
    LT.put_state(LT.connect(root), nav_latest={'day': '2026-11-18', 'actual': 101_000_000, LT.CONTRIBUTED: 1_000_000})
    with sqlite3.connect(root / 'virtual-account.sqlite3') as db:
        db.execute('CREATE TABLE account (name TEXT PRIMARY KEY, value REAL NOT NULL)')
        db.execute('CREATE TABLE orders (number INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, symbol TEXT NOT NULL, '
                   'side TEXT NOT NULL, qty INTEGER NOT NULL, price INTEGER NOT NULL, filled INTEGER NOT NULL, '
                   'cancelled INTEGER NOT NULL, fill_price REAL, time TEXT NOT NULL)')
        db.execute("INSERT INTO account VALUES ('cash', 75000000)")
        db.execute("INSERT INTO orders VALUES (1, '2026-11-18', '379810', 'BUY', 1000, 25000, 1000, 0, 25000, '2026-11-18T12:02:00+09:00')")
    items = [{'key': 'longterm-virtual', 'label': '장기투자 가상 계좌', 'kind': '가상 체결', 'rows': LT.journal_rows(root)}]
    row = overview.balance_overview(items, tmp_path, '2026-11-18')[0]
    assert (row['매입'], row['평가손익'], row['보유 수익률']) == (25_000_000, 1_000_000, 4.0)  # 101,000,000 − 75,000,000 cash
    assert (row['원금'], row['전체 손익']) == (101_000_000, 0)  # the whole gain stays against the money put in
    LT.put_state(LT.connect(root), **{'dca:actual': {'contributed_krw': 1_500_000}})  # 500,000 paid in since the close
    with sqlite3.connect(root / 'virtual-account.sqlite3') as db:
        db.execute("UPDATE account SET value = 75500000 WHERE name = 'cash'")
    row = overview.balance_overview(items, tmp_path, '2026-11-18')[0]
    assert row['평가손익'] == 1_000_000  # the new money is cash, not a holdings loss


def test_a_corrupt_line_in_the_mirror_log_does_not_hide_its_block_alerts(tmp_path, monkeypatch):
    # review4 D-3: one torn line in live-follow.jsonl emptied the whole log for the alerts
    import api_status_view
    monkeypatch.setattr(api_status_view, 'current_alerts', lambda now=None: [])
    (tmp_path / 'shared-etf-paper').mkdir()
    block = json.dumps({'time': '2026-11-18T10:00:00+09:00', 'account': 'live-1', 'action': 'BLOCK', 'reason': 'guard'})
    (tmp_path / 'live-follow.jsonl').write_text('\n'.join([block, block[:30], block]) + '\n')
    assert 'live-1 미러 차단·오류 2회 (운영 → AI·실행 기록에서 확인)' in overview.alerts(tmp_path, {}, {}, '2026-11-18')


def test_an_earlier_days_activity_carries_its_date(tmp_path, monkeypatch):
    # review4 D-5: yesterday's '09:41 계획' read as today's last activity
    from datetime import datetime
    monkeypatch.setattr('trading_journal._live_rows', lambda key, label: [])
    event = {'time': '2026-11-18T09:41:00+09:00', 'account': 'live-2', 'engine': 'longterm', 'action': 'PLAN', 'orders': []}
    (tmp_path / 'longterm-live.jsonl').write_text(json.dumps(event) + '\n')
    records = {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}}
    last = lambda day: next(r['last'] for r in overview.accounts(tmp_path, records, datetime.fromisoformat(f'{day}T10:00:00+09:00'))  # noqa: E731
                            if r['key'] == 'live-2')
    assert last('2026-11-19').startswith('11-18 09:41 ') and last('2026-11-18').startswith('09:41 ')
    assert L.clock('2026-11-18T09:41:00+09:00', '2026-11-18') == '09:41'
    assert L.clock('2026-11-18T09:41:00+09:00', '2026-11-19') == '11-18 09:41' and L.clock('', '2026-11-19') == ''


def _overview_page(runtime, records):
    import overview
    overview.render(runtime, records, {}, {})


def test_a_long_term_account_has_no_mirror_card(tmp_path, monkeypatch):
    # review4 D-5: live-2 runs the long-term engine since 11-17, yet its retired mirror's last record still had a card
    import api_status_view
    monkeypatch.setattr(api_status_view, 'current_alerts', lambda now=None: [])
    monkeypatch.setattr('trading_journal._live_rows', lambda key, label: [])
    mirror = lambda key: json.dumps({'time': '2026-11-17T10:00:00+09:00', 'account': key, 'action': 'HOLD', 'reason': 'matches the paper runner'})  # noqa: E731
    (tmp_path / 'live-follow.jsonl').write_text(mirror('live-1') + '\n' + mirror('live-2') + '\n')
    records = {'live-1': {'kind': 'live', 'engine': 'jev', 'label': '실계좌 1'}, 'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}}
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_overview_page, args=(tmp_path, records), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    html = ' '.join(m.value for m in at.markdown)
    assert '실계좌 1 미러' in html and '실계좌 2 미러' not in html
    assert '11-17 10:00 · 엔진 원장과 같음' in html  # not today: the date shows


def test_a_day_whose_only_fill_sells_a_carried_holding_still_draws(tmp_path, monkeypatch):
    # 2026-11-24 morning: 삼성전자 carried overnight was the day's only fill, no bar started that day, and the tab stopped
    import api_status_view
    from datetime import datetime
    monkeypatch.setattr(api_status_view, 'current_alerts', lambda now=None: [])
    sell = {'time': datetime.now().astimezone().replace(hour=9, minute=34).isoformat(), 'symbol': '005930', 'side': 'SELL', 'qty': 1, 'price': 189050,
            'amount': 189050, 'avg': 180000, 'pnl': 9050, 'cost': 180000, 'return_pct': 5.0, 'after': 0, 'path': '장기투자', 'basis': ''}
    monkeypatch.setattr('trading_journal._live_rows', lambda key, label: [sell])
    from streamlit.testing.v1 import AppTest
    records = {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}}
    at = AppTest.from_function(_overview_page, args=(tmp_path, records), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any('종목마다 한 줄' in c.value for c in at.caption)  # the timeline itself was drawn


def test_totals_stay_unknown_when_no_account_knows_the_value(tmp_path):
    # review4 D-8: '오늘 변동' / '평가손익' / '매입' totals read +0원 while every account row showed '—'
    (tmp_path / 'live-balance-live-1.jsonl').write_text(json.dumps({'time': '2026-11-17T15:00:00+09:00', 'total_assets': 990_000}) + '\n')
    items = [{'key': 'live-1', 'label': '실계좌 1', 'kind': '실계좌', 'rows': []},
             {'key': 'live-2', 'label': '실계좌 2', 'kind': '실계좌', 'rows': []}]
    rows = {r['계좌']: r for r in overview.balance_overview(items, tmp_path, '2026-11-18')}
    assert rows['실계좌 1']['오늘 변동'] is None and rows['실계좌 1']['평가손익'] is None
    for name in ('실계좌 합계', '전체 합계'):
        total = rows[name]
        assert (total['총자산'], total['오늘 변동'], total['평가손익'], total['매입']) == (990_000, None, None, None), name
