"""Integration coverage for the account filters and menu shortcut."""
import pytest
from streamlit.testing.v1 import AppTest
from datetime import datetime


def _fills():
    import trade_history_view as history

    today = datetime.now(history.KST).date()
    return tuple(
        dict(account=key, time=f'{today}T{clock}:00+09:00', time_known=True, time_basis='원장 기록',
             symbol='005930', side='BUY', order_no=str(index), qty=1, price=100,
             amount=100, cost=None, pnl=None, return_pct=None)
        for index, (key, clock) in enumerate((('live-1', '13:31'), ('live-1', '13:30'),
                                              ('paper-shared', '10:00'), ('live-2', '09:00')))
    )


def _view():
    from datetime import datetime
    import os
    from pathlib import Path
    import streamlit as st
    import trade_history_view as history

    today = datetime.now(history.KST).date()
    if 'started' not in st.session_state:
        st.session_state['trade_history_open'] = ('live-1', today)
        st.session_state.started = True
    def load_names():
        st.session_state['name_reads'] = st.session_state.get('name_reads', 0) + 1
        return {'005930': '삼성전자'}

    history.render(Path(os.environ['TRADE_HISTORY_TEST_ROOT']), load_names, lambda _keys: ({}, {}))


@pytest.fixture(autouse=True)
def _isolated_query_root(monkeypatch, tmp_path):
    monkeypatch.setenv('TRADE_HISTORY_TEST_ROOT', str(tmp_path))


def _buy_card(ui):
    return next(element.value for element in ui.markdown if 'aria-label="매수 체결금액:' in element.value)


def test_account_shortcut_and_minute_filter(monkeypatch):
    import trade_history_data
    rows = _fills()
    monkeypatch.setattr(trade_history_data, 'load', lambda _: rows)
    ui = AppTest.from_function(_view).run()
    assert not ui.exception
    assert ui.segmented_control(key='trade_history_scope').value == '실계좌 1'
    assert ui.segmented_control(key='trade_history_view').value == '체결·손익'
    assert ui.radio(key='trade_history_period').value == '오늘'
    assert ' · 2건"' in _buy_card(ui)
    ui.checkbox(key='trade_history_intraday').check().run()
    assert not ui.exception
    assert ' · 1건"' in _buy_card(ui)
    ui.segmented_control(key='trade_history_scope').set_value('전체').run()
    assert not ui.exception
    assert ' · 2건"' in _buy_card(ui)


def test_new_ledger_keeps_current_results_until_refresh(monkeypatch):
    import trade_history_data

    rows = list(_fills())
    monkeypatch.setattr(trade_history_data, 'load', lambda _: tuple(rows))
    monkeypatch.setattr(trade_history_data, 'freshness_revision', lambda _: (len(rows),))
    monkeypatch.setattr(trade_history_data, 'revision', lambda _: (len(rows),))
    ui = AppTest.from_function(_view).run()
    assert ' · 2건"' in _buy_card(ui)
    assert ui.session_state['name_reads'] == 1
    rows.append({**rows[0], 'order_no': 'new'})
    ui.run()
    assert ' · 2건"' in _buy_card(ui)
    assert ui.session_state['name_reads'] == 1
    assert any('체결 또는 주문 연결 기록이 변경' in message.value for message in ui.info)
    ui.button(key='trade_history_refresh').click().run()
    assert ' · 3건"' in _buy_card(ui)
    assert ui.session_state['name_reads'] == 2
    assert not any('체결 또는 주문 연결 기록이 변경' in message.value for message in ui.info)


def _holdings_with_broker_error():
    import os
    from pathlib import Path
    import streamlit as st
    import trade_history_view as history

    path = Path(os.environ['TEST_HOLDINGS_CALLS'])

    def loader(keys):
        with path.open('a') as log:
            log.write(','.join(keys) + '\n')
        return {}, {'live-1': '실계좌 1 잔고를 확인하지 못했습니다.'}

    st.session_state.setdefault('trade_history_view', '보유주식')
    st.session_state.setdefault('trade_history_scope', '실계좌 1')
    def unnecessary_names():
        raise AssertionError('보유주식 조회에서 체결 원장 종목명을 읽지 않아야 합니다.')

    history.render(Path('/unused'), unnecessary_names, loader)


def test_broker_error_is_retried_only_on_refresh_within_cache_window(tmp_path, monkeypatch):
    path = tmp_path / 'calls.txt'
    monkeypatch.setenv('TEST_HOLDINGS_CALLS', str(path))
    ui = AppTest.from_function(_holdings_with_broker_error).run()
    assert not ui.exception
    assert path.read_text().splitlines() == ['live-1']
    ui.run()
    assert path.read_text().splitlines() == ['live-1']
    assert any('실계좌 1 잔고를 확인하지 못했습니다' in message.value for message in ui.warning)
    ui.button(key='trade_history_holdings_refresh').click().run()
    assert path.read_text().splitlines() == ['live-1', 'live-1']


def test_profit_chart_labels_amounts_and_distribution_without_hover(monkeypatch):
    import json
    import trade_history_data

    rows = _fills()
    sell = {**rows[0], 'side': 'SELL', 'pnl': -1200, 'cost': 2200, 'return_pct': -54.55}
    monkeypatch.setattr(trade_history_data, 'load', lambda _: (sell, *rows[1:]))
    ui = AppTest.from_function(_view).run()
    assert not ui.exception
    chart = json.loads(ui.get('vega_lite_chart')[0].proto.spec)
    labels = [layer for layer in chart['layer'] if layer['mark']['type'] == 'text']
    assert len(labels) == 2
    assert all(layer['encoding']['text']['field'] == '금액 표시' for layer in labels)
    assert any('손실' in line.value and '1건' in line.value and '100.0%' in line.value for line in ui.markdown)
    assert next(i for i, element in enumerate(ui) if element.type == 'markdown' and 'aria-label="매수 체결금액:' in element.value) < next(
        i for i, element in enumerate(ui) if element.type == 'markdown' and '매수·매도 상세 내역' in element.value
    )

    loss_card = next(line.value for line in ui.markdown if 'aria-label="실현손익:' in line.value)
    assert '--a:#3b82f6' in loss_card and '-1,200원' in loss_card


def test_holdings_resolves_live_accounts_once_before_reading_balances(monkeypatch):
    import app

    calls = []
    adapter = object()

    def linked(read, tool, args, cache_s=0):
        calls.append((tool, args))
        return {'Output_0': [{'acct_no': '1000', 'acct_type': '01'},
                             {'acct_no': '2000', 'acct_type': '02'},
                             {'acct_no': '3000', 'acct_type': '03'}]}

    def balance(read, number):
        assert read is adapter
        calls.append(('balance', number))
        return {'Output_0': {'tot_aet_amt': 1000}, 'Output_1': []}

    from accounts import digest
    records = {f'live-{index}': {'kind': 'live', 'hash': digest(number)}
               for index, number in enumerate(('1000', '2000', '3000'), 1)}
    monkeypatch.setattr(app, 'call', linked)
    monkeypatch.setattr(app, 'read_balance', balance)
    snapshots, errors = app.trade_history_holdings(adapter, object(), None, records, records)
    assert calls == [('list_accounts', {}), ('balance', '1000'), ('balance', '2000')]
    assert set(snapshots) == {'live-1', 'live-2'}
    assert set(errors) == {'live-3'}


def test_holdings_switch_keeps_custom_trade_dates(monkeypatch):
    from datetime import date, timedelta
    import trade_history_data
    from trade_history_view import KST

    monkeypatch.setattr(trade_history_data, 'load', lambda _: _fills())
    ui = AppTest.from_function(_view).run()
    ui.radio(key='trade_history_period').set_value('직접 지정').run()
    today = datetime.now(KST).date()
    start = today - timedelta(days=9)
    assert isinstance(start, date)
    ui.date_input(key='trade_history_start').set_value(start).run()
    ui.segmented_control(key='trade_history_view').set_value('보유주식').run()
    assert not ui.exception
    ui.segmented_control(key='trade_history_view').set_value('체결·손익').run()
    assert ui.radio(key='trade_history_period').value == '직접 지정'
    assert ui.date_input(key='trade_history_start').value == start


def test_trade_page_requests_survive_same_filter_rerun(monkeypatch):
    import trade_history_data
    import trade_history_view as history

    rows = tuple({**_fills()[0], 'order_no': f'{i:03d}'} for i in range(history.PAGE + 1))
    monkeypatch.setattr(trade_history_data, 'load', lambda _: rows)
    ui = AppTest.from_function(_view).run()
    snapshot = ui.session_state[history.SNAPSHOT]
    assert not hasattr(snapshot, 'rows')
    assert len(trade_history_data.get_query(snapshot.lease.token)) == history.PAGE + 1
    ui.run()
    assert ui.session_state[history.SNAPSHOT].query_id == snapshot.query_id
    ui.button(key='trade_history_refresh').click().run()
    assert ui.session_state[history.SNAPSHOT].query_id != snapshot.query_id
    assert ui.session_state[history.SNAPSHOT].lease.token == snapshot.lease.token


def test_leaving_trade_view_releases_snapshot_and_keeps_other_session(monkeypatch, tmp_path):
    import pytest
    import trade_history_data as data
    import trade_history_view as history

    monkeypatch.setattr(data, 'load', lambda _: _fills())
    ui = AppTest.from_function(_view).run()
    assert not ui.exception
    original = ui.session_state[history.SNAPSHOT]
    second, query = data.acquire_query(tmp_path, original.filters)
    assert len(query) == 2
    ui.segmented_control(key='trade_history_view').set_value('자산·성과').run()
    assert not ui.exception
    assert history.SNAPSHOT not in ui.session_state
    assert len(data.get_query(second.token)) == 2
    second.release()
    with pytest.raises(data.QueryExpired):
        data.get_query(original.lease.token)


def _combined_realized():
    from datetime import datetime
    from pathlib import Path
    import trade_history_view as history

    today = datetime.now(history.KST).date()
    history._realized_overview(Path('/unused'), ('live-1', 'live-2'), today, today, True)


def test_combined_realized_uses_each_sales_cost_even_when_gain_is_zero(monkeypatch):
    import trade_history_data as data

    rows = _fills()
    sells = (
        {**rows[0], 'account': 'live-1', 'side': 'SELL', 'amount': 120, 'pnl': 20,
         'cost': 100, 'return_pct': 20},
        {**rows[1], 'account': 'live-2', 'side': 'SELL', 'amount': 900, 'pnl': 0,
         'cost': 900, 'return_pct': 0},
    )
    monkeypatch.setattr(data, 'load', lambda _: sells)
    ui = AppTest.from_function(_combined_realized).run()
    assert not ui.exception
    combined = ui.dataframe[0].value.set_index('계좌').loc['실계좌 합산']
    assert combined['실현손익'] == '+20원'
    assert combined['실현수익률'] == '+2.00%'
    assert combined['원가 확인 매도'] == '2 / 2건'
