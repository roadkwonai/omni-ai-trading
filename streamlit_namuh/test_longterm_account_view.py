import json
from unittest.mock import patch

import longterm_account_view as V
import vibe_longterm as L

RECORD = {'kind': 'live', 'label': '실계좌 2', 'engine': 'longterm', 'auto': True, 'live_orders': False,
          'budget': {'capital_krw': 500_000}}


def test_status_sizes_targets_on_the_account_capital_and_reads_the_day_plan(tmp_path):
    weights = {k: 0 for k in L.ASSETS} | {'NDX': 0.5, 'SPX': 0.5}
    plan = {'day': '2026-11-18', 'regime': 'correction', 'mode': 'manual', 'weights': weights}
    state = {'day': '2026-11-18', 'orders': [{'key': 'NDX', 'code': '379810', 'side': 'BUY', 'qty': 3, 'price': 20_000,
                                              'status': 'dry_run'}], 'info': {'why': '초기 투입'}}
    (tmp_path / 'longterm-live').mkdir()
    (tmp_path / 'longterm-live' / 'state-live-2.json').write_text(json.dumps(state))
    with patch.object(L, 'read_state', return_value={'plan': plan}):
        data = V.status('live-2', RECORD, tmp_path)
    ndx = next(t for t in V.affordability(500_000, data['weights'], {}, 300_000) if '379810' in t['ETF'])
    assert (ndx['목표 비중'], ndx['목표 금액']) == ('50%', '250,000원')
    # the account's own target of the day (after the story check) wins over the engine's saved weights
    (tmp_path / 'longterm-live' / 'state-live-2.json').write_text(json.dumps({**state, 'info': {'want': {**weights, 'NDX': 0.4}}}))
    with patch.object(L, 'read_state', return_value={'plan': plan}):
        again = V.status('live-2', RECORD, tmp_path)
    assert (again['weights']['NDX'], again['basis']) == (0.4, '이 계좌 오늘 계획의 목표(스토리 반영)')
    assert (data['service'], data['mode']) == ('longterm-live-2', '계획만 기록')
    assert V.order_rows(data['today'])[0]['상태'] == '계획만(실주문 꺼짐)'
    assert V.status('live-1', {**RECORD, 'live_orders': True}, tmp_path)['mode'] == '실주문'


def test_linked_accounts_lists_only_valid_long_term_live_accounts():
    records = {'paper-shared': {'kind': 'paper', 'engine': 'jev'}, 'live-1': {'kind': 'live', 'engine': 'jev', 'label': '실계좌 1'},
               'live-2': RECORD, 'live-3': {**RECORD, 'invalid': True}}
    assert V.linked_accounts(records) == [('live-2', '실계좌 2', '계획만')]


def test_fill_history_is_newest_first_with_realized_pnl(tmp_path):
    import sqlite3
    import live_fills
    import longterm_account_view as V
    assert V.fill_rows('live-2', tmp_path) == []
    with sqlite3.connect(tmp_path / live_fills.NAME) as db:
        db.execute(live_fills.SCHEMA)
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', [
            ('live-2', '20261118', 1, '379810', 'BUY', 10, 20000.0, '2026-11-18T09:41:00+09:00', 'x'),
            ('live-2', '20261119', 2, '379810', 'SELL', 4, 21000.0, '2026-11-19T09:42:00+09:00', 'x'),
            ('live-1', '20261119', 3, '114800', 'BUY', 1, 1000.0, '2026-11-19T09:43:00+09:00', 'x')])
    rows = V.fill_rows('live-2', tmp_path)
    assert [r['매수/매도'] for r in rows] == ['매도', '매수'] and rows[0]['실현손익'] == '+4,000원' and rows[0]['보유 후'] == 6
    assert rows[0]['종목'] == 'KODEX 미국나스닥100 (379810)'


def _longterm_desk_page(nhplug):
    import longterm_account_view as view

    view.desk('live-2', {'kind': 'live', 'engine': 'longterm', 'budget': {'capital_krw': 500_000}},
              nhplug, [], '종료')


def test_first_step_estimate_uses_saved_free_cash_and_alert_ask_without_counting_alert_as_order():
    import yeo_method as Y

    plan = {'day': '2026-11-23', 'info': {'nav': 482_820, 'invested': 64_910, 'weights': {'RES': 0}}}
    events = [{'action': 'DCA_ALERT', 'time': '2026-11-23T09:45:14+09:00',
               'orders': [{'code': '379810', 'price': 22_825}, {'code': '0261H0', 'price': 9_660}]}]
    base, rows = V.first_step_estimate(Y.pairs(Y.clean({}))[0], .7, plan, None, events)
    assert base == 417_910
    assert [(r['예상 배정액'], r['예상 수량']) for r in rows] == [('117,015원', '5주'), ('50,149원', '5주')]
    assert all('매수호가 기록' in r['가격 근거'] for r in rows)


def _longterm_assets_page(nhplug, snapshot):
    import streamlit as st
    import longterm_account_view as view

    def load_assets(refresh):
        if st.session_state.get('loaded_once'):
            raise TimeoutError
        st.session_state['loaded_once'] = True
        return snapshot

    view.desk('live-2', {'kind': 'live', 'engine': 'longterm', 'principal_krw': 500_000,
                         'budget': {'capital_krw': 500_000}}, nhplug, [], '실행 중', load_assets=load_assets)


def test_assets_tab_displays_broker_valuation_and_preserves_last_success(tmp_path, monkeypatch):
    from datetime import datetime
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(L, 'ROOT', tmp_path)
    snapshot = {'time': datetime.fromisoformat('2026-11-23T10:20:30+09:00'),
                'account': {'총자산': 499_200, '주문가능금액': 453_200},
                'aggregate': {'평가손익': -800, '수익률(%)': -1.71},
                'holdings': [{'종목코드': '379810', '종목': 'KODEX 미국나스닥100', '보유수량': 2, '평균매입가': 23_400,
                              '현재가': 23_000, '평가금액': 46_000, '평가손익': -800, '수익률(%)': -1.71}]}
    ui = AppTest.from_function(_longterm_assets_page, args=(str(tmp_path), snapshot)).run()
    assert not ui.exception
    assert any('불러오는 중' in caption.value for caption in ui.caption)
    ui.button(key='lt_asset_refresh_live-2').click().run()
    for attempt in range(2):
        assert not ui.exception
        row = next(table.value for table in ui.dataframe if '현재가' in table.value).iloc[0]
        assert list(row[['현재가', '평가금액', '평가손익', '수익률']]) == ['23,000원', '46,000원', '-800원', '-1.71%']
        assert any('2026-11-23 10:20:30 KST' in caption.value for caption in ui.caption)
        if attempt == 0:
            ui.button(key='lt_asset_refresh_live-2').click().run()
    assert any('마지막 성공 조회 결과' in warning.value for warning in ui.warning)


def test_desk_shows_remaining_holding_from_raw_broker_fills(tmp_path, monkeypatch):
    import sqlite3
    import live_fills
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(L, 'ROOT', tmp_path)
    (tmp_path / 'longterm-live').mkdir()
    (tmp_path / 'longterm-live' / 'state-live-2.json').write_text(json.dumps({
        'orders': [{'key': 'NDX', 'code': '379810', 'side': 'BUY', 'qty': 10, 'price': 20_000,
                    'status': 'dry_run'}]}))
    with sqlite3.connect(tmp_path / live_fills.NAME) as db:
        db.execute(live_fills.SCHEMA)
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?,?,?,?)', [
            ('live-2', '20261118', 1, '379810', 'BUY', 10, 20_000, '2026-11-18T09:41:00+09:00', 'x'),
            ('live-2', '20261119', 2, '379810', 'SELL', 4, 21_000, '2026-11-19T09:42:00+09:00', 'x'),
        ])
    live_fills.balance_file(tmp_path, 'live-2').write_text('\n'.join([
        json.dumps({'time': '2026-11-18T14:00:00+09:00', 'total_assets': 500_000, 'cash': 300_000}),
        'broken',
        json.dumps({'time': '2026-11-19T06:00:00+00:00', 'total_assets': 505_000, 'cash': 384_000}),
        json.dumps({'time': '2026-11-19T09:40:00+09:00', 'total_assets': 499_000, 'cash': 300_000}),
    ]))

    ui = AppTest.from_function(_longterm_desk_page, args=(str(tmp_path),)).run()
    assert not ui.exception
    holdings = next(table.value for table in ui.dataframe if '평균 매입가' in table.value)
    ndx = holdings.loc[holdings['종목'].str.endswith('(379810)')].iloc[0]
    assert (ndx['보유 수량'], ndx['매입 금액']) == (6, '120,000원')
    assert [tab.label for tab in ui.tabs] == ['자산·보유', '주문·체결', '가상 비교', '증권사 조회·보호']
    assert len(ui.get('vega_lite_chart')) == 2
    allocation = next(table.value for table in ui.dataframe if '실계좌 비중' in table.value)
    assert allocation['실계좌 비중'].isna().all()
    assert [(day, row['total_assets']) for day, _, row in V.daily_balance_history(
        live_fills.balance_file(tmp_path, 'live-2'))] == [('2026-11-18', 500_000), ('2026-11-19', 505_000)]


def test_live_diagram_shows_the_long_term_path():
    import guides
    dot = guides.live_dot(guides.values())
    assert '장기투자 실행기' in dot and 'lt -> ltlive' in dot and '종목 손절 없음' in dot


def test_skipped_and_retried_orders_read_in_korean(tmp_path, monkeypatch):
    today = {'orders': [
        {'key': 'SEMI', 'code': '381180', 'side': 'BUY', 'qty': 1, 'price': 30_000, 'status': 'skipped', 'reason': 'share price above the account price cap'},
        {'key': 'NDX', 'code': '379810', 'side': 'SELL', 'qty': 2, 'price': 20_000, 'status': 'skipped',
         'reason': 'held shares are not in the guard ledger (adopting them needs operator approval)'},
        {'key': 'SPX', 'code': '379800', 'side': 'BUY', 'qty': 3, 'price': 20_000, 'status': 'planned', 'reason': 'broker has outstanding orders'}]}
    assert [r['상태'] for r in V.order_rows(today)] == ['건너뜀 · 1주 가격이 계좌 1주 가격 상한보다 높음',
                                                       '건너뜀 · 보유분이 보호 원장에 없음(편입은 운영자 승인 필요)',
                                                       '보낼 차례 · 다른 미체결 주문이 있음']
    import overview
    from pathlib import Path
    events = [{'time': '2026-11-18T09:41:00+09:00', 'account': 'live-2', 'action': 'WAIT', 'execution': 'RETRY', 'symbol': '379800',
               'reason': 'broker has outstanding orders'},
              {'time': '2026-11-18T09:42:00+09:00', 'account': 'live-2', 'action': 'SKIP', 'symbol': '381180',
               'reason': 'share price above the account price cap'}]
    (tmp_path / 'longterm-live.jsonl').write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    monkeypatch.setattr('engine_roles.ledger_root', lambda r, e: Path(r) / e)
    monkeypatch.setattr(overview.ec, 'cached_journal', lambda *a: [])
    rows, _ = overview.timeline(tmp_path, '2026-11-18', {})
    texts = [r['내용'] for r in rows]
    assert texts == ['대기 · 다른 미체결 주문이 있음 · 다시 시도', '건너뜀 · 1주 가격이 계좌 1주 가격 상한보다 높음']
    assert not any(word in ' '.join(texts) for word in ('RETRY', 'SKIP', 'share price'))


# ── 장기투자 계좌 설정 개편 (docs/longterm-account-settings-plan.md 1절, 2026-11-18) ──
WEIGHTS = {'NDX': 0.3, 'SPX': 0.3, 'SEMI': 0.1, 'QUANT': 0.04, 'SPACE': 0.07, 'RES': 0.19}
PRICES = {'NDX': 24_060, 'SPX': 22_095, 'SEMI': 34_435, 'QUANT': 15_840, 'SPACE': 6_035, 'RES': 112_475}


def _plist(folder, args):
    import plistlib
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'com.vibetrading.longterm-live-2.plist').write_bytes(plistlib.dumps({'ProgramArguments': args}))


def test_checklist_reads_execute_from_the_registered_plist_and_the_switch_from_the_account(tmp_path, monkeypatch):
    monkeypatch.setattr(V, 'LAUNCH_AGENTS', tmp_path / 'agents')
    with patch.object(L, 'read_state', return_value={}):
        assert V.status('live-2', RECORD, tmp_path)['checklist'] == [('실행기 실주문 등록(--execute)', False), ('실주문 스위치', False)]
        _plist(tmp_path / 'agents', ['python', 'scripts/longterm_live.py', '--account', 'live-2'])
        data = V.status('live-2', {**RECORD, 'live_orders': True}, tmp_path)
        assert data['checklist'] == [('실행기 실주문 등록(--execute)', False), ('실주문 스위치', True)]
        assert V.checklist_text(data['checklist']) == '실주문까지: ① 실행기 실주문 등록(--execute) ✗ · ② 실주문 스위치 ✓'
        _plist(tmp_path / 'agents', ['python', 'scripts/longterm_live.py', '--account', 'live-2', '--execute'])
        assert all(ok for _, ok in V.status('live-2', {**RECORD, 'live_orders': True}, tmp_path)['checklist'])


def test_affordability_counts_whole_shares_of_the_target_money_after_the_fee():
    import longterm_dca as D
    dca = D.clean({'on': True, 'pick_on': True, 'picks': ['NDX', 'SPX', 'SPACE']})
    prices = {k: (p, '이 계좌 11-18 계획') for k, p in PRICES.items()}
    rows = {r['ETF'].split(' (')[1][:-1]: r for r in V.affordability(500_000, WEIGHTS, prices, 300_000, dca)}
    assert (rows['379810']['목표 금액'], rows['379810']['1주 가격'], rows['379810']['살 수 있는 주식 수']) == ('150,000원', '24,060원', 6)
    assert rows['379810']['상태'] == '살 수 있음' and rows['379810']['가격 기준'] == '이 계좌 11-18 계획'
    assert (rows['381180']['살 수 있는 주식 수'], rows['381180']['상태']) == (1, '살 수 있음 · 적립식 제외')
    assert (rows['449170']['살 수 있는 주식 수'], rows['449170']['상태']) == (0, '1주 미만이라 못 삼 · 적립식 제외')
    off = {r['ETF']: r['상태'] for r in V.affordability(500_000, WEIGHTS, prices, 300_000, D.clean({}))}
    assert not any('적립식' in s for s in off.values())  # 적립식 꺼짐: no ETF is "excluded"
    # a share above the account's 1-share cap, a 0% target and an ETF with no recorded price
    odd = {r['ETF'].split(' (')[1][:-1]: r for r in V.affordability(
        500_000, {**WEIGHTS, 'SPACE': 0.0}, {**{k: v for k, v in prices.items() if k != 'QUANT'}, 'SEMI': (350_000, '엔진')}, 300_000, None)}
    assert odd['381180']['상태'] == '1주 가격 상한보다 비쌈' and odd['381180']['살 수 있는 주식 수'] == 0
    assert odd['0183J0']['상태'] == '목표 0%' and odd['0023A0']['상태'] == '가격 기록 없음' and odd['0023A0']['1주 가격'] == '—'


def test_min_capital_is_the_smallest_capital_that_buys_one_share_of_every_weighted_etf():
    prices = {k: (p, '') for k, p in PRICES.items()}
    # RES: 112,475 × 1.00015 / 0.19 ≈ 592,062 → rounded up to the next 10,000 won
    assert V.min_capital(WEIGHTS, prices) == (600_000, 6, [])
    assert V.min_capital({**WEIGHTS, 'RES': 0.0}, prices) == (400_000, 5, [])  # QUANT 15,842 / 0.04 ≈ 396,059
    krw, count, missing = V.min_capital(WEIGHTS, {k: v for k, v in prices.items() if k != 'RES'})
    assert (krw, count, missing) == (400_000, 5, ['TIGER KOFR금리액티브(합성)'])


def test_latest_prices_take_the_newest_recorded_order_price_and_name_its_source():
    today = {'day': '2026-11-18', 'orders': [{'key': 'NDX', 'price': 24_060}, {'key': 'BAD'}, {'key': 'SPX', 'price': 0}]}
    decisions = [{'day': '2026-11-18', 'orders': json.dumps([{'key': 'SEMI', 'price': 34_435}, {'key': 'NDX', 'price': 24_050}])},
                 {'day': '2026-11-17', 'orders': json.dumps([{'key': 'RES', 'price': 112_000}, {'key': 'SEMI', 'price': 30_000}])},
                 {'day': '2026-11-17', 'orders': None}]
    prices = V.latest_prices(today, decisions)
    assert prices == {'NDX': (24_060, '이 계좌 11-18 계획'), 'SEMI': (34_435, '엔진 11-18 계획'), 'RES': (112_000, '엔진 11-17 계획')}
    old = V.latest_prices({'day': '2026-11-10', 'orders': [{'key': 'NDX', 'price': 20_000}]}, decisions)
    assert old['NDX'] == (24_050, '엔진 11-18 계획')  # a newer engine record wins over an older account day


def test_protection_rows_explain_the_long_term_limits_and_the_engine_monthly_sell_cap():
    record = {**RECORD, 'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000},
              'limits': {'order_max_krw': 500_000, 'daily_new_symbols': 5}}
    settings = L.load_settings('/nonexistent')
    rows = {r['항목']: r for r in V.protection_rows(record, 500_000, settings)}
    assert rows['월 매도 한도']['지금 값'] == '50,000원/월 (운용금의 10%)' and rows['월 매도 한도']['확인'] == '엔진(가드 아님)'
    assert rows['하루 새 종목 수']['지금 값'] == '5개 (첫 투입일 6개)' and rows['하루 새 종목 수']['확인'] == '실계좌 가드'
    assert rows['하루 실현 손실 한도']['지금 값'] == '20,000원' and '평가손' in rows['하루 실현 손실 한도']['장기 계좌에서']
    assert {'회당 매수 한도', '1주 가격 상한'} <= set(rows) and not {'종목 손절', '가격 추격 한도', '매수 크기 · 1회 최대 수량'} & set(rows)
    tighter = {r['항목']: r for r in V.protection_rows({**record, 'limits': {'order_max_krw': 500_000, 'daily_new_symbols': 3}}, 500_000,
                                                      {**settings, 'modules': {**settings['modules'], 'buffett_lowturn': False}})}
    assert tighter['하루 새 종목 수']['지금 값'] == '3개'  # the first-day exception applies only at the full 5 (the guard)
    assert tighter['월 매도 한도']['지금 값'].startswith('꺼짐')


def test_long_term_account_cards_show_no_jev_mirror_fields():
    import settings_view as sv
    labels = [k for k, _ in sv.account_rows(RECORD, '계획만 기록')]
    assert '운용 방식' in labels and not {'매수 크기(분할 1회)', '계좌 손절 설정', '가격 추격', '미러 실행'} & set(labels)
    dot = sv.limits_dot(RECORD)
    assert '장기투자 엔진' in dot and '모의 분할' not in dot
    jev = {**RECORD, 'engine': 'jev'}
    assert '가격 추격' in [k for k, _ in sv.account_rows(jev)]  # the Jev card is unchanged
    assert 'style="--c:#a855f7">Jev 트레이딩' in sv.account_card('live-1', jev)
    assert 'style="--c:#22c55e">장기투자' in sv.account_card('live-2', RECORD)


def _settings_page():
    import app
    record = {'label': '실계좌 2', 'kind': 'live', 'masked': '000-**-***00', 'hash': 'test', 'engine': 'longterm', 'auto': True,
              'budget': {'capital_krw': 500_000, 'daily_loss_krw': 20_000, 'order_price_max_krw': 300_000},
              'limits': {'order_max_krw': 500_000, 'daily_new_symbols': 5}, 'live_orders': False}
    app.account_form('live-2', record, {'live-2': record}, prefix='lt_test_')


def test_long_term_settings_screen_renders_the_four_sections_and_saves_the_same_fields(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    nhplug, engine = tmp_path / 'nhplug', tmp_path / 'nhplug' / 'vibe-longterm'
    (nhplug / 'longterm-live').mkdir(parents=True)
    engine.mkdir()
    (engine / 'settings.json').write_text(json.dumps({'dca': {'live-2': {'on': True, 'amount_krw': 300_000, 'pick_on': True,
                                                                         'picks': ['NDX', 'SPX']}}}))
    (nhplug / 'longterm-live' / 'state-live-2.json').write_text(json.dumps(
        {'day': '2026-11-18', 'orders': [{'key': k, 'code': L.ASSETS[k][0], 'side': 'BUY', 'qty': 1, 'price': p, 'status': 'dry_run'}
                                         for k, p in PRICES.items()], 'info': {'want': WEIGHTS}}))
    monkeypatch.setattr(L, 'ROOT', engine)
    monkeypatch.setattr(V, 'LAUNCH_AGENTS', tmp_path / 'agents')
    monkeypatch.setattr('src.config.paths.get_runtime_root', lambda: tmp_path)
    plan = {'day': '2026-11-18', 'regime': 'correction', 'mode': 'manual', 'weights': {k: a[3] for k, a in L.ASSETS.items()}}
    with patch.object(L, 'read_state', return_value={'plan': plan}), patch('accounts.save_one') as save:
        ui = AppTest.from_function(_settings_page, default_timeout=30).run()
        assert not ui.exception, ui.exception
        text = ' '.join([m.value for m in ui.markdown] + [c.value for c in ui.caption] + [i.value for i in ui.info]).replace('**', '')
        for words in ('포트폴리오', '운용금이 600,000원 이상이면 6종 모두 1주 이상', '적립식', '켜짐', '2종목만', '장기투자 → 전략 → 적립식',
                      '보호 한도 (장기 전용)', '월 매도 한도', '첫 투입일', '실주문까지: ① 실행기 실주문 등록(--execute) ✗ · ② 실주문 스위치 ✗'):
            assert words in text, words
        assert not any(w.key and w.key.startswith('lt_test_live-2_' + name) for w in ui.radio
                       for name in ('sizing',)) and not any('가격 추격' in s.label for s in ui.slider)
        labels = [s.label for s in ui.slider]
        assert any(l.startswith('하루 실현 손실 한도') for l in labels) and any(l.startswith('1주 가격 상한') for l in labels)
        ui.button(key='lt_test_save_live-2').click().run()
        assert not ui.exception
        key, changes = save.call_args.args
        assert key == 'live-2' and 'rules' not in changes and changes['engine'] == 'longterm'
        assert set(changes['budget']) == {'capital_krw', 'order_price_max_krw', 'daily_loss_krw'}
        assert changes['budget'] == {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}
        assert changes['limits'] == {'order_max_krw': 500_000, 'daily_new_symbols': 5}


def test_shadow_summary_gives_the_accounts_virtual_result_and_both_return_curves():
    # user 2026-11-18: 실계좌 2 shows its own strategy's virtual result, beside the engine's virtual book
    rows = [('2026-11-18', 500_000, 500_000), ('2026-11-19', 510_000, 500_000), ('2026-11-20', 495_000, 500_000)]
    nav = [('2026-11-17', 'actual', 100_000_000), ('2026-11-18', 'actual', 101_000_000), ('2026-11-19', 'base', 1),
           ('2026-11-20', 'actual', 99_000_000)]
    s = V.shadow_summary(rows, nav, {'2026-11-20': 1_000_000})
    assert (s['value'], s['gain'], s['pct'], s['days'], s['start']) == (495_000, -5_000, -1.0, 3, '2026-11-18')
    assert round(s['mdd'], 4) == round(495_000 / 510_000 - 1, 4)
    # both curves from the account's first day; the engine's 적립 paid in since then is not a gain
    assert s['series'] == [('2026-11-18', 0.0, 0.0), ('2026-11-19', 2.0, None),
                           ('2026-11-20', -1.0, round(((99_000_000 - 1_000_000) / 101_000_000 - 1) * 100, 2))]
    assert V.shadow_summary([], nav, {}) is None


def test_allocation_shows_actual_reservoir_gap_and_unknown_valuation():
    rows = V.allocation_comparison({'NDX': 0.5, 'SPX': 0, 'RES': 0.5},
                                  {'379810': {'평가금액': 200_000}, '0261H0': {'평가금액': None}},
                                  500_000, {}, '357870')
    assert [r['종목'] for r in rows] == ['379810', '357870', '0261H0']
    assert (rows[0]['목표 비중'], rows[0]['실계좌 비중'], rows[0]['비중 차이']) == (50, 40, -10)
    assert (rows[1]['목표 금액'], rows[1]['실계좌 비중']) == (250_000, 0)
    assert rows[2]['실계좌 비중'] is None and rows[2]['비중 차이'] is None
    chart = V.allocation_chart(rows).to_dict()
    assert chart['height'] == {'step': 42}
    assert [layer['mark']['type'] for layer in chart['layer']] == ['bar', 'tick']


def test_flow_dot_marks_where_the_account_stops_today():
    off = V.flow_dot({'mode': '계획만 기록', 'checklist': []}, '여운봉 저수지만(보글 없음)', '조정', '실행 중')
    assert 'digraph' in off and '여운봉 저수지만' in off and '조정' in off and '계획만 기록' in off
    assert V.flow_dot({'mode': '실주문', 'checklist': []}, 'x', 'y', '실행 중').count('penwidth=2.4') >= 1
