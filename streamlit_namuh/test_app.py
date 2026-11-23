import pytest
import unittest
import sqlite3
import json
import tempfile
import time
from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import app
from streamlit.testing.v1 import AppTest


class SettingsSummaryTests(unittest.TestCase):
    def test_split_policy_summary_uses_recorded_runner_version(self):
        old = {'event': 'strategy_settings', 'settings': {'add_step_pct': 0.7}}
        new = {'event': 'strategy_settings', 'settings': {'add_step_pct': 0.8, 'scale_policy_version': 2}}
        with patch('engine_compare.recent_events', return_value=[old]):
            self.assertIn('최대 3회', app.split_policy_summary())
        with patch('engine_compare.recent_events', return_value=[old, new]):
            text = app.split_policy_summary()
            self.assertIn('±0.8%', text)
            self.assertIn('익절 후', text)
        with patch('engine_compare.recent_events', return_value=[]):
            self.assertIn('확인하지 못했습니다', app.split_policy_summary())

    def test_scale_settings_status_distinguishes_saved_from_recorded(self):
        saved = {'scale_signal_add_on': 1, 'scale_step_add_on': 0,
                 'scale_profit_rebuy_on': 1, 'scale_interval_min': 7}
        event = {'time': '2026-11-16T10:30:00+09:00', 'settings': {
            'scale_signal_add_on': 0, 'scale_step_add_on': 1,
            'scale_profit_rebuy_on': 1, 'scale_interval_min': 5}}
        stored, applied = app.scale_settings_status(saved, [event])
        self.assertIn('새 매수 판단으로 추가 매수: 켜짐', stored)
        self.assertIn('단계 추가 간격: 7분', stored)
        self.assertIn('새 매수 판단으로 추가 매수: 꺼짐', applied)
        self.assertIn('단계 추가 간격: 5분', applied)
        self.assertIn('미확인', app.scale_settings_status(saved, [{'settings': {}}])[1])


class HoldingStopDisplayTests(unittest.TestCase):
    def test_real_market_view_applies_reused_regime_and_engine_specific_plan(self):
        settings = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}
        with tempfile.TemporaryDirectory() as d:
            nhplug = Path(d) / 'nhplug'
            shared, vibe = nhplug / 'shared-etf-paper', nhplug / 'vibe-paper'
            shared.mkdir(parents=True)
            vibe.mkdir()
            view = {'time': datetime.now(app.KST).isoformat(), 'indices': {'001': {
                'name': '코스피', 'regime': {'label': '하락장', 'reasons': [], 'drawdown_pct': 0},
                'change_pct': -1, 'flow': '약세', 'investors_100m': {}, 'program_100m': {},
                'breadth': {'상승': 1, '하락': 1}, 'reused_from': '2026-11-13T15:00:00+09:00'}}}
            (shared / 'market-context.json').write_text(json.dumps(view, ensure_ascii=False))
            (vibe / 'oversold-plans.json').write_text(json.dumps({'114800': {'stop_price': 1190, 'target_price': 1300}}))
            choose = lambda root, engine: shared if engine == 'jev' else vibe
            with patch.object(app, 'get_runtime_root', return_value=Path(d)), \
                 patch('engine_roles.ledger_root', side_effect=choose), \
                 patch('nh_live_follow.ordinary_positions', return_value={'114800': True}):
                price, label = app.holding_stop_display('114800', 1272, settings, 'jev')
                self.assertAlmostEqual(price, 1246.56)  # an inverse is not tightened in a 하락장 (11/17, direction-aware)
                self.assertIn('보유 계획 미확인', label)
                self.assertIn('시황 재사용/오래됨', label)
                live_price, live_label = app.holding_stop_display('114800', 1272, settings, 'jev', 2.0)
                self.assertAlmostEqual(live_price, 1246.56)
                self.assertIn('시황 재사용/오래됨', live_label)
                special, special_label = app.holding_stop_display('114800', 1272, settings, 'vibe')
                self.assertEqual(special, 1190)
                self.assertIn('낙폭과대', special_label)

    def test_vibe_as_main_engine_reads_the_shared_runner_plans(self):
        # 11/17 review: with vibe as main the shared account is still traded by nh_paper_isolated (ENGINE='vibe'), which
        # keeps exit-plans.json; the vibe_runner plan store (oversold-plans.json) belongs to vibe-paper only
        settings = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}
        with tempfile.TemporaryDirectory() as d:
            shared = Path(d) / 'nhplug' / 'shared-etf-paper'
            shared.mkdir(parents=True)
            (shared / 'exit-plans.json').write_text(json.dumps({
                '114800': {'profile': 'oversold', 'stop_pct': 0.5, 'half_pct': 1.5, 'all_pct': 1.5}}))
            (shared / 'oversold-plans.json').write_text(json.dumps({'114800': {'stop_price': 1190, 'target_price': 1300}}))
            with patch.object(app, 'get_runtime_root', return_value=Path(d)), \
                 patch('engine_roles.ledger_root', return_value=shared), \
                 patch('nh_paper_isolated.stop_symbol_index', return_value='코스피'), \
                 patch('nh_paper_isolated.market_view', return_value={'코스피': {'regime': '상승장'}}):
                price, label = app.holding_stop_display('114800', 1272, settings, 'vibe')
                self.assertAlmostEqual(price, 1265.64)  # the shared runner's 0.5% plan, not a stray 1190
                self.assertIn('전용', label)

    def test_regime_tightens_ordinary_but_not_special_and_stale_is_labelled(self):
        settings = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}
        base = {'profile': 'defensive', 'stop_pct': 2.0, 'half_pct': 1.0, 'all_pct': 2.0}
        with patch('engine_roles.ledger_root', return_value=Path('/test/shared-etf-paper')), \
             patch('nh_paper_isolated.exit_plans', return_value={'114800': base}) as plans, \
             patch('nh_paper_isolated.stop_symbol_index', return_value='코스피'), \
             patch('nh_paper_isolated.market_view', return_value={'코스피': {'regime': '하락장'}}) as market:
            price, label = app.holding_stop_display('114800', 1272, settings, 'jev')
            self.assertAlmostEqual(price, 1246.56)
            self.assertIn('코스피 하락장', label)
            market.return_value = None
            price, label = app.holding_stop_display('114800', 1272, settings, 'jev')
            self.assertAlmostEqual(price, 1246.56)
            self.assertIn('시황 미확인', label)
            market.return_value = {'코스피': {'regime': '하락장', 'stale_reused_from': '2026-11-13T15:00:00+09:00'}}
            price, label = app.holding_stop_display('114800', 1272, settings, 'jev')
            self.assertAlmostEqual(price, 1246.56)
            self.assertIn('시황 재사용/오래됨', label)
            plans.return_value = {}
            market.return_value = {'코스피': {'regime': '하락장'}}
            price, label = app.holding_stop_display('114800', 1272, settings, 'jev')
            self.assertAlmostEqual(price, 1246.56)
            self.assertIn('보유 계획 미확인', label)
            plans.return_value = {'069500': base}  # a long ETF in the same 하락장 is still tightened to 1%
            price, _ = app.holding_stop_display('069500', 1272, settings, 'jev')
            self.assertAlmostEqual(price, 1259.28)
            plans.return_value = {'114800': {**base, 'profile': 'ipo', 'stop_pct': 3.0,
                                               'trend_half_pct': 3.0, 'trend_trail_pct': 2.0}}
            market.return_value = {'코스피': {'regime': '하락장'}}
            price, label = app.holding_stop_display('114800', 1272, settings, 'jev')
            self.assertAlmostEqual(price, 1233.84)
            self.assertIn('전용', label)

    def test_live_account_stop_distinguishes_regime_and_unconfirmed_profile(self):
        settings = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}
        with patch('engine_roles.ledger_root', return_value=Path('/test/vibe-paper')), \
             patch('nh_paper_isolated.market_view', return_value={'코스피': {'regime': '횡보장'}}) as market, \
             patch('nh_paper_isolated.stop_symbol_index', return_value='코스피'), \
             patch('nh_live_follow.stop_percent_for_positions', return_value={
                 '114800': {'pct': 1.0, 'profile': 'ordinary', 'regime': '횡보장'}}) as stop:
            price, label = app.holding_stop_display('114800', 1272, settings, 'vibe', 2.0)
            self.assertAlmostEqual(price, 1259.28)
            self.assertIn('계좌·시황 기준', label)
            market.return_value = {'코스피': {'regime': '횡보장', 'stale_reused_from': '2026-11-13T15:00:00+09:00'}}
            price, label = app.holding_stop_display('114800', 1272, settings, 'vibe', 2.0)
            self.assertAlmostEqual(price, 1259.28)
            self.assertIn('시황 재사용/오래됨', label)
            market.return_value = {'코스피': {'regime': '횡보장'}}
            stop.return_value = {'114800': {'pct': 2.0, 'profile': 'unconfirmed_or_special', 'regime': 'unavailable'}}
            price, label = app.holding_stop_display('114800', 1272, settings, 'vibe', 2.0)
            self.assertAlmostEqual(price, 1246.56)
            self.assertIn('확인 필요', label)

    def test_vibe_oversold_uses_its_absolute_stop(self):
        settings = {'stop_loss_pct': 2.0, 'take_profit_half_pct': 1.0, 'take_profit_all_pct': 2.0}
        with patch('engine_roles.ledger_root', return_value=Path('/test/vibe-paper')), \
             patch('vibe_runner.oversold_plans', return_value={'114800': {'stop_price': 1190}}):
            price, label = app.holding_stop_display('114800', 1272, settings, 'vibe')
        self.assertEqual(price, 1190)
        self.assertIn('낙폭과대', label)




def app_test(tab, **subtabs):
    """The dashboard with `tab` open: only the open main tab runs (lazy tabs, 2026-11-13); keyed sub-tabs the same way."""
    ui = AppTest.from_file(app.__file__, default_timeout=30)
    ui.session_state['main_tab'] = tab
    for key, label in subtabs.items():
        ui.session_state[key] = label
    return ui


def _strategy_settings_page():
    import app
    app.strategy_form([], prefix='test_')


def _display_preferences_page():
    import app
    app.prefs_view()
    app.apply_font_scale(app.st.session_state.prefs['font_scale'])
    app.prefs_form()


def test_font_size_setting_saves_and_applies_immediately():
    with tempfile.TemporaryDirectory() as d, patch.object(app, 'prefs_path', return_value=Path(d) / 'prefs.json'), \
         patch('config_store.record'):
        ui = AppTest.from_function(_display_preferences_page, default_timeout=30).run()
        assert not ui.exception
        assert ui.selectbox[0].label == '화면 글씨 크기'
        assert ui.selectbox[0].value == 100
        ui.selectbox[0].select(70)
        ui.button(key='FormSubmitter:prefs_form-저장').click().run()
        assert not ui.exception
        assert app.PREFS.load(Path(d) / 'prefs.json')['font_scale'] == 70
        assert 'font-size: 70%' in ui.html[0].value
        assert '.stGraphVizChart svg { max-width: 70%' in ui.html[0].value
        ui.selectbox[0].select(100)
        ui.button(key='FormSubmitter:prefs_form-저장').click().run()
        assert not ui.exception
        assert app.PREFS.load(Path(d) / 'prefs.json')['font_scale'] == 100
        assert 'font-size:' not in ui.html[0].value


def test_existing_session_preferences_gain_font_default():
    old = {key: value for key, value in app.PREFS.DEFAULTS.items() if key != 'font_scale'}
    ui = AppTest.from_function(_display_preferences_page, default_timeout=30)
    ui.session_state['prefs'] = old
    ui.run()
    assert not ui.exception
    assert ui.selectbox[0].value == 100


def _paper_order_quantity_page():
    import app
    from accounts import DEFAULTS
    record = DEFAULTS['accounts']['paper-shared']
    app.account_form('paper-shared', record, {'paper-shared': record}, prefix='qty_test_')


def test_paper_order_quantity_control_explains_buy_sell_and_protective_exits():
    with patch('tunables.setting', return_value=0):
        ui = AppTest.from_function(_paper_order_quantity_page, default_timeout=30).run()
        assert not ui.exception
        control = ui.toggle(key='qty_test_paper-shared_order_qty_on')
        assert control.label == '1회 주문 수량 제한'
        assert control.value is False
        assert all(word in control.help for word in ('일반 매수', '일반 매도', '보호 손절', '실계좌'))
        control.set_value(True).run()
        assert not ui.exception
        assert any(s.label == '일반 주문 1회 최대 수량 (주)' for s in ui.slider)


def _live_sizing_page():
    import app
    from accounts import DEFAULT_LIVE_RULES
    record = {'label': '실계좌 T', 'kind': 'live', 'masked': '000-**-***00', 'hash': 'test', 'engine': 'jev', 'auto': False,
              'budget': {'capital_krw': 1_000_000, 'daily_loss_krw': 20_000, 'order_price_max_krw': 300_000},
              'rules': {**DEFAULT_LIVE_RULES, 'sizing': 'amount', 'tranche_krw': 300_000, 'max_order_qty': 100},
              'limits': {}, 'live_orders': False}
    app.account_form('live-t', record, {'live-t': record}, prefix='sizing_test_')


def test_live_share_cap_is_disabled_under_amount_sizing():
    ui = AppTest.from_function(_live_sizing_page, default_timeout=30).run()
    assert not ui.exception
    box = ui.number_input(key='sizing_test_live-t_maxqty_off')
    assert box.disabled and box.value == 100  # amount sizing has no share cap (2026-11-17)
    assert not any(s.label == '1회 매수 최대 수량 (주)' for s in ui.slider)
    ui.radio(key='sizing_test_live-t_sizing').set_value('share').run()
    assert not ui.exception
    assert any(s.label == '1회 매수 최대 수량 (주)' for s in ui.slider)


def test_the_tranche_amount_is_locked_under_share_sizing_and_an_example_shows_both_bases():
    """2026-11-19: 체결 1건당 최대 금액 is unused by 1주 기준, so it is locked there; an example table shows what each basis buys."""
    ui = AppTest.from_function(_live_sizing_page, default_timeout=30).run()
    assert not ui.exception
    assert not any(n.key == 'sizing_test_live-t_tranche_off' for n in ui.number_input)  # amount: editable
    text = ' '.join(m.value for m in ui.markdown)
    assert '이 계좌는 이렇게 따라 삽니다' in text and '금액 비율이면 ◀ 지금 설정' in text  # plain words (user 2026-11-20)
    assert all(word in ui.radio(key='sizing_test_live-t_sizing').help for word in ('1주씩', '금액 비율', '100% = 모의 수량 그대로'))
    ui.radio(key='sizing_test_live-t_sizing').set_value('share').run()
    assert not ui.exception
    box = ui.number_input(key='sizing_test_live-t_tranche_off')
    assert box.disabled and box.value == 300_000  # the saved amount is kept for 금액 기준
    assert '1주씩이면 ◀ 지금 설정' in ' '.join(m.value for m in ui.markdown)


def _orders_page():
    import app
    snapshot = {'orders': [{'주문 시각': '095857', '구분': '현금매수', '종목': 'KODEX 인버스', '종목코드': '114800', '주문수량': 74,
                            '체결수량': 74, '미체결수량': 0, '평균체결가': 1341, '취소수량': 0, '거부 사유': None},
                           {'주문 시각': '103857', '구분': '현금매도', '종목': 'KODEX 인버스', '종목코드': '114800', '주문수량': 197,
                            '체결수량': 197, '미체결수량': 0, '평균체결가': 1345, '취소수량': 0, '거부 사유': None}],
                'aggregate': {'평가금액': 265665, '평가손익': -860, '수익률(%)': -0.32},
                'holdings': [{'종목': 'KODEX 인버스', '종목코드': '114800', '보유수량': 199}]}
    books = {'114800': {'today': 788.0, 'sales': [('2026-11-17T10:38:57', 788.0, 264177.0)]}}
    app.orders_view(snapshot, books, '2026-11-17')
    app.shadow_view({'trades': [{'time': '2026-11-17T09:42:00', 'side': 'BUY', 'symbol': '114800', 'qty': 743, 'price': 1345, 'reason': 'jev_fast', 'pnl': None},
                                {'time': '2026-11-17T10:20:00', 'side': 'SELL', 'symbol': '114800', 'qty': 743, 'price': 1330, 'reason': '손절', 'pnl': -11145}],
                     'realized': -11145, 'open': {}, 'skipped_entries': {}, 'basis': 'engine ledger'})


def test_orders_and_virtual_trades_show_amounts_rates_and_side_colors():
    ui = AppTest.from_function(_orders_page, default_timeout=30).run()
    assert not ui.exception
    text = ' '.join(str(m.value) for m in ui.markdown)
    assert '99,234원' in text  # 74 x 1,341 bought
    assert '+0.30%' in text  # 788 / 264,177 realized today
    assert '−1.12%' in text or '-1.12%' in text  # virtual sell -11,145 on 999,335
    orders, virtual = ui.dataframe[0].value, ui.dataframe[1].value
    assert list(orders['시각']) == ['10:38:57', '09:58:57']  # the latest order first, HH:MM:SS
    assert list(orders['실현손익']) == ['+788원', ''] and list(orders['수익률']) == ['+0.30%', '']  # sells only
    assert list(virtual['시각']) == ['10:20', '09:42']  # the latest virtual trade first


def test_sell_results_without_books_use_the_days_running_average():
    filled = [{'종목코드': 'A', '구분': '현금매수', '체결수량': 10, '평균체결가': 1000, '주문번호': 1},
              {'종목코드': 'A', '구분': '현금매수', '체결수량': 10, '평균체결가': 1100, '주문번호': 2},
              {'종목코드': 'A', '구분': '현금매도', '체결수량': 20, '평균체결가': 1060, '주문번호': 3},
              {'종목코드': 'B', '구분': '현금매도', '체결수량': 5, '평균체결가': 500, '주문번호': 4}]  # carried: unknown
    out = app.sell_results(filled)
    assert round(out[3][0]) == 200 and round(out[3][1], 2) == 0.95 and 4 not in out


def _quick_sell_page():
    import streamlit as st
    import app
    sent = st.session_state.setdefault('sent', [])
    app.quick_sell_panel([('114800', 'KODEX 인버스', 496), ('005930', '삼성전자 (005930)', 3),
                          ('0261H0', '0261H0', 2), ('379810', '379810', 2)], st.session_state.get('pending', {}),
                         lambda code, qty: sent.append(('SELL', code, qty)), lambda code, qty: sent.append(('BUY', code, qty)), 'qs', '테스트')


def test_quick_trade_buttons_file_quantity_sells_buys_and_sell_alls():
    ui = AppTest.from_function(_quick_sell_page, default_timeout=30).run()
    assert not ui.exception
    labels = {item.value for item in ui.markdown}
    assert '**삼성전자 (005930)** · 보유 3주' in labels
    assert '**KODEX 미국나스닥100미국채혼합50 (0261H0)** · 보유 2주' in labels
    assert '**KODEX 미국나스닥100 (379810)** · 보유 2주' in labels
    ui.number_input(key='qs_qty_114800').set_value(100).run()
    ui.button(key='qs_sell_114800').click().run()
    assert ui.session_state['sent'] == [('SELL', '114800', 100)]  # a chosen quantity
    ui.session_state['sent'] = []
    ui.button(key='qs_buy_114800').click().run()
    assert ui.session_state['sent'] == [('BUY', '114800', 100)]  # a hand add
    ui.session_state['sent'] = []
    ui.number_input(key='qs_qty_005930').set_value(9).run()
    ui.button(key='qs_sell_005930').click().run()
    assert ui.session_state['sent'] == [('SELL', '005930', 3)]  # never more than held
    ui.session_state['sent'] = []
    ui.button(key='qs_sell_all').click().run()
    assert sorted(ui.session_state['sent']) == [('SELL', code, None) for code in ('005930', '0261H0', '114800', '379810')]
    ui.session_state['pending'] = {'114800': '100주 매도'}
    ui.run()
    assert ui.button(key='qs_sell_114800').disabled  # one request at a time per holding
    assert ui.number_input(key='qs_qty_005930')  # the quantity box is on the row, not hidden in a menu


def _auto_page():
    import streamlit as st
    import app
    st.write('AUTO', app.auto_on())


def test_one_header_switch_drives_every_page_refresh():
    ui = AppTest.from_function(_auto_page, default_timeout=30)
    ui.session_state['prefs'] = {'quotes_auto': True}
    ui.run()
    assert 'AUTO `True`' in [m.value for m in ui.markdown]  # the saved default until the header switch is touched
    ui.session_state['auto_all'] = False
    ui.run()
    assert 'AUTO `False`' in [m.value for m in ui.markdown]  # the header switch wins everywhere


def test_scale_settings_widgets_save_through_strategy_store():
    from contextlib import ExitStack
    with tempfile.TemporaryDirectory() as d, ExitStack() as stack:
        stack.enter_context(patch.object(app, 'get_runtime_root', return_value=Path(d)))
        stack.enter_context(patch.object(app, 'strategy_settings', return_value=(dict(app.STRATEGY_DEFAULTS), 'default')))
        for name in ('strength_presets', 'oversold_presets', 'oversold_universe_editor', 'ipo_presets', 'volume_presets'):
            stack.enter_context(patch.object(app, name))
        save = stack.enter_context(patch.object(app, 'write_settings'))
        ui = AppTest.from_function(_strategy_settings_page, default_timeout=30).run()
        assert not ui.exception
        ui.slider(key='test_strategy_scale_interval_min_0_s').set_value(7).run()
        ui.toggle(key='test_strategy_scale_signal_add_on_0').set_value(False)
        ui.toggle(key='test_strategy_scale_profit_rebuy_on_0').set_value(False)
        ui.button(key='test_strategy_save').click().run()
        assert not ui.exception
        values = save.call_args.args[1]
        assert values['scale_signal_add_on'] == 0
        assert values['scale_step_add_on'] == 1
        assert values['scale_profit_rebuy_on'] == 0
        assert values['scale_interval_min'] == 7

class Adapter:
    def __init__(self, *, paper=False, pending=0, account_type='03', sellable=2):
        self.paper, self.pending, self.account_type, self.sellable = paper, pending, account_type, sellable
        self.sent = []

    def call_tool(self, tool, args):
        if tool == 'list_accounts':
            data = {'Output_0': [{'acct_no': 'paper', 'acct_type': self.account_type}]}
        elif tool == 'get_stock_price':
            data = {'rsp_cd': '00000', 'Output_0': {'iem_cd': args['stock_code']}}
        else:
            op = args['operationId']
            if op == 'krstockInquiryBalance':
                data = {'rsp_cd': '00000', 'Output_0': {}, 'Output_1': []}
            elif op == 'krstockInquiryDailyOrderExecution':
                data = {'rsp_cd': '00000', 'Output_0': [{'ny_cns_qty': self.pending}], '_nh_pagination': {'complete': True}}
            elif op == 'krstockInquirySellableQuantity':
                data = {'rsp_cd': 'XA102', 'Output_0': {'iem_cd': args['input']['iem_cd'], 'sll_pbl_qty': self.sellable}}
            else:
                self.sent.append(op)
                data = {'Output_0': {'mkt_orr_no': 123}}
        return {'status': 'ok', 'data': data}


class SubmitTest(unittest.TestCase):
    def test_chart_merge_prefers_new_quote_without_changing_history(self):
        recorded = [{'time': 1, 'close': 100}, {'time': 2, 'close': 101}]
        fresh = [{'time': 2, 'close': 102}, {'time': 3, 'close': 103}]
        self.assertEqual(app.merge_chart_bars(recorded, fresh),
                         [{'time': 1, 'close': 100}, {'time': 2, 'close': 102}, {'time': 3, 'close': 103}])

    def test_shared_etf_broker_snapshot_shows_verified_holdings_and_order_status(self):
        balance = {'Output_0': {'dca': 100000, 'tot_eal_amt': 1280, 'tot_eal_pls': 3, 'pft_rt': 0.23}, 'Output_1': [
            {'iem_cd': '114800', 'iem_nm': 'KODEX 인버스', 'rsdl_qty': 1,
             'phs_pr': 1277, 'now_pr': 1280, 'eal_amt': 1280, 'eal_pls_amt': 3, 'pft_rt': 0.23},
            {'iem_cd': '069500', 'rsdl_qty': 0},
        ]}
        history = {'Output_0': [{'iem_cd': '114800', 'sby_dit_cd_nm': '현금매수',
                    'orr_qty': 2, 'tot_cns_qty': 1, 'ny_cns_qty': 1, 'can_qty': 0,
                    'cns_avg_uit_pr': 1277, 'orr_rjt_rsn_cd_nm': ''}]}
        now = datetime(2026, 11, 11, 11, 0, tzinfo=app.KST)
        with patch.object(app, 'verify_account'), patch.object(app, 'paper_pages', side_effect=[balance, history]):
            result = app.shared_etf_broker_snapshot(Adapter(), Adapter(), 'paper', now)
        self.assertEqual(result['time'], now)
        self.assertEqual(len(result['holdings']), 1)
        self.assertEqual(result['holdings'][0]['종목코드'], '114800')
        self.assertEqual(result['holdings'][0]['평가손익'], 3)
        self.assertEqual(result['aggregate']['평가금액'], 1280)
        self.assertEqual(result['orders'][0]['체결수량'], 1)
        self.assertEqual(result['orders'][0]['미체결수량'], 1)
        self.assertNotIn('계좌번호', str(result))
        portfolio = app.etf_portfolio_rows(result['holdings'])
        self.assertEqual(len(portfolio), 7)
        self.assertEqual(next(row for row in portfolio if row['종목코드'] == '114800')['보유수량'], 1)
        self.assertEqual(next(row for row in portfolio if row['종목코드'] == '069500')['보유수량'], 0)
        self.assertIsNone(next(row for row in portfolio if row['종목코드'] == '069500')['수익률(%)'])
        with self.assertRaises(ValueError):
            app.etf_portfolio_rows(result['holdings'] * 2)
        balance['Output_1'][0]['now_pr'] = None
        with patch.object(app, 'verify_account'), patch.object(app, 'paper_pages', side_effect=[balance, history]):
            self.assertIsNone(app.shared_etf_broker_snapshot(Adapter(), Adapter(), 'paper', now)['aggregate'])

    def test_shared_etf_broker_snapshot_refuses_incomplete_holdings_or_failed_query(self):
        now = datetime(2026, 11, 11, 11, 0, tzinfo=app.KST)
        with patch.object(app, 'verify_account'), patch.object(app, 'paper_pages', side_effect=[{'Output_0': {}, 'Output_1': [{'iem_cd': '114800'}]}, {'Output_0': []}]):
            with self.assertRaises(ValueError):
                app.shared_etf_broker_snapshot(Adapter(), Adapter(), 'paper', now)
        with patch.object(app, 'verify_account'), patch.object(app, 'paper_pages', side_effect=ValueError('incomplete')):
            with self.assertRaises(ValueError):
                app.shared_etf_broker_snapshot(Adapter(), Adapter(), 'paper', now)

    def test_shared_etf_ui_clears_previous_balance_after_query_failure(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        response = [{'Output_0': {'dca': 1000}, 'Output_1': [{'iem_cd': '114800', 'rsdl_qty': 1}]},
                    {'Output_0': []}]
        with patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10), \
             patch('nh_paper_isolated.paper_pages', side_effect=response) as pages, \
             patch('automation_status.peek_processes', return_value={'공유 모의 ETF 실행기': '종료', '공유 모의 ETF 데이터 관찰기': '실행 중'}):
            ui = app_test('트레이딩 현황').run()
            self.assertTrue(any('잔고·주문을 불러오는 중' in str(c.value) for c in ui.caption))
            next(button for button in ui.button if button.label == 'ETF 현황 새로고침').click().run()
            self.assertEqual(ui.session_state['etf_broker_snapshot']['holdings'][0]['보유수량'], 1)
            from datetime import timedelta
            ui.session_state['etf_broker_snapshot']['time'] -= timedelta(minutes=3)
            ui.session_state['etf_broker_attempt'] = 0
            pages.reset_mock()
            ui.run()
            self.assertTrue(any('잔고·주문을 불러오는 중' in str(c.value) for c in ui.caption))
            self.assertEqual(pages.call_count, 0)
            self.assertTrue(any('최근 2분 안의 증권사 잔고가 없어' in str(c.value) for c in ui.caption))
            pages.side_effect = ValueError('조회 실패')
            next(button for button in ui.button if button.label == 'ETF 현황 새로고침').click().run()
            self.assertTrue(ui.session_state['etf_broker_error'])
            with self.assertRaises(KeyError):
                ui.session_state['etf_broker_snapshot']
            self.assertTrue(any('빈 계좌로 간주하지' in str(error.value) for error in ui.error))
            self.assertTrue(any('실행기가 작동하지 않아' in str(warning.value) for warning in ui.warning))

    def test_shared_etf_ui_shows_the_ledger_while_a_broker_read_is_pending(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        with patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10), \
             patch('nh_paper_isolated.paper_pages') as pages, \
             patch('automation_status.peek_processes', return_value={'공유 모의 ETF 실행기': '실행 중'}):
            ui = app_test('트레이딩 현황').run()
            pending = Future()
            ui.session_state['etf_broker_future'] = pending
            ui.run()
            self.assertFalse(ui.exception)
            self.assertTrue(any('Jev 보유·오늘 매도' in str(markdown.value) for markdown in ui.markdown))
            self.assertTrue(any('잔고·주문을 불러오는 중' in str(caption.value) for caption in ui.caption))
            pages.assert_not_called()

            snapshot = {'holdings': [], 'time': datetime.now(app.KST),
                        'orders': [], 'cash': None, 'aggregate': None}
            pending.set_result((snapshot, time.monotonic()))
            ui.run()
            self.assertFalse(ui.exception)
            self.assertEqual(ui.session_state['etf_broker_snapshot'], snapshot)
            self.assertFalse(any('잔고·주문을 불러오는 중' in str(caption.value) for caption in ui.caption))
            pages.assert_not_called()
    def test_regime_observation_reports_fresh_stale_and_missing(self):
        now = datetime(2026, 11, 11, 10, 10, tzinfo=app.KST)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'regime-observation.json'
            self.assertIsNone(app.read_regime_observation(path, now))
            record = {'time': now.isoformat(), 'status': 'partial',
                      'minute_event_time': (now - timedelta(minutes=1)).isoformat(),
                      'source': 'NH KRX completed minute events + daily quote',
                      'symbols': {'INV': {'symbol': '114800', 'mode': 'intraday',
                                          'reason': 'test', 'data_quality': 'valid',
                                          'purpose': 'research_only', 'daily_quote_time': now.isoformat()}}}
            path.write_text(json.dumps(record), encoding='utf-8')
            observed = app.read_regime_observation(path, now)
            self.assertFalse(observed['stale'])
            self.assertEqual(observed['rows'][0]['종목'], 'KODEX 인버스')
            self.assertEqual(observed['rows'][0]['국면'], '장중 매매 후보')
            record['symbols']['INV']['minute_source'] = 'market_context'
            path.write_text(json.dumps(record), encoding='utf-8')
            self.assertEqual(app.read_regime_observation(path, now)['rows'][0]['분봉 출처'], '시장자료 기록')
            self.assertTrue(app.read_regime_observation(path, now + timedelta(seconds=121))['stale'])
            self.assertTrue(app.read_regime_observation(path, now - timedelta(seconds=1))['stale'])
            record['symbols']['INV']['symbol'] = '069500'
            path.write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaises(ValueError):
                app.read_regime_observation(path, now)

    def test_all_seven_etfs_are_selectable_for_quotes_and_orders(self):
        self.assertEqual(len(app.ETF_CHARTS), 7)
        self.assertEqual(app.ETF_CHARTS['KODEX 코스닥150선물인버스'], ('KQ150INV', '251340'))
        self.assertEqual(app.ETF_CHARTS['KODEX 반도체레버리지'], ('SEMILEV', '494310'))
        self.assertEqual(set(app.SYMBOLS.values()), {'069500', '122630', '114800', '229200', '233740', '251340', '494310'})

    def test_regime_is_research_only_and_does_not_invent_missing_history(self):
        self.assertIsNone(app.latest_regime([]))
        result = app.latest_regime([
            {'event': 'v2_regime', 'time': '2026-11-11T10:00:00+09:00', 'mode': 'intraday', 'reason': 'high_volatility'},
            {'event': 'v2_regime', 'time': '2026-11-11T10:01:00+09:00', 'mode': 'swing_candidate', 'reason': 'low_volatility'},
        ])
        self.assertEqual(result['국면'], '단기 스윙 후보')
        self.assertEqual(result['기록 시각'], '2026-11-11 10:01:00')
        self.assertEqual(result['사유'], 'low_volatility')
        self.assertIn('검증 중', result['상태'])

    def test_only_shared_paper_records_are_shown(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for folder, choice in [('shared-etf-paper', 'INV'), ('isolated-paper', 'K200')]:
                path = root / 'nhplug' / folder
                path.mkdir(parents=True)
                records = [{'event': 'decision', 'time': '2026-11-11T09:10:00+09:00', 'choice': choice}]
                if folder == 'shared-etf-paper':
                    records.extend([{'event': 'v2_news_error', 'time': '2026-11-11T09:10:01+09:00', 'error_type': 'TimeoutError'},
                                    {'event': 'v2_risk_exit_error', 'time': '2026-11-11T09:10:02+09:00', 'symbol': '114800', 'reason': 'signal_or_session_invalid'}])
                (path / 'events.jsonl').write_text(''.join(json.dumps(record) + '\n' for record in records))
            with patch('src.config.loader.load_agent_config', return_value=configs), patch('src.config.paths.get_runtime_root', return_value=root), patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10):
                ui = app_test('운영', ops_view='AI·실행 기록')
                ui.session_state['record_source'] = 'jev'  # the record tab opens on its overview; this checks the engine view
                ui.run()
                self.assertEqual(ui.session_state['ai_raw_events'][0]['choice'], 'INV')
                self.assertFalse(any(s.label == '모의 실험 기록' for s in ui.selectbox))
                self.assertFalse(any('이전 별도' in str(button.label) for button in ui.button))
                self.assertEqual(ui.session_state['ai_events'][0]['이벤트'], 'v2_risk_exit_error')
                self.assertTrue(any('보호매도 미전송' in str(warning.value) for warning in ui.warning))
                chart = next(s for s in ui.selectbox if s.label == '차트 종목')
                self.assertEqual(len(chart.options), 7)
                chart.select('KODEX 반도체레버리지').run()
                self.assertTrue(any('수집하지 않은 종목' in str(message.value) for message in ui.info))

    def test_holding_basis_requires_recent_matching_broker_position_and_fill(self):
        now = datetime(2026, 11, 11, 11, 10, tzinfo=app.KST)
        snapshot = {'time': now, 'holdings': [{'종목코드': '114800', '보유수량': 1, '평균매입가': 1277, '현재가': 1280}]}
        fills = [{'시각': '2026-11-11T11:05:31+09:00', '종목': '114800', '구분': 'BUY', '수량': 1, '체결가': 1277}]
        basis = app.holding_chart_basis(snapshot, fills, '114800', now)
        self.assertEqual(basis['price'], 1277)
        self.assertEqual(datetime.fromtimestamp(basis['start'], app.KST).minute, 5)
        self.assertIsNone(app.holding_chart_basis(snapshot, fills, '069500', now))
        self.assertIsNone(app.holding_chart_basis(snapshot, fills, '114800', now + timedelta(seconds=121)))
        self.assertIsNone(app.holding_chart_basis(snapshot, [{**fills[0], '체결가': 1278}], '114800', now))
        self.assertIsNone(app.holding_chart_basis(snapshot, fills + [{**fills[0], '시각':'2026-11-11T11:06:00+09:00', '구분':'SELL'}], '114800', now))
        self.assertEqual(app.etf_portfolio_rows(snapshot['holdings'])[2]['가격 구간'], '수익 +0.23%')

    def test_jev_only_context_links_real_opinion_and_order_without_fake_decision(self):
        rows = app.comparison_data([
            {'event':'market_context', 'context':'entry', 'time':'2026-11-11T11:00:00+09:00', 'cycle_id':'a'},
            {'event':'jev_shadow', 'time':'2026-11-11T11:00:01+09:00', 'cycle_id':'a', 'jev':{'candidate':{'choice':'INV','confidence':.8}}},
            {'event':'v2_entry_gate', 'time':'2026-11-11T11:00:02+09:00', 'cycle_id':'a', 'choice':'INV', 'permitted':True},
            {'event':'market_context', 'context':'holding', 'time':'2026-11-11T11:01:00+09:00', 'cycle_id':'b', 'symbol':'114800'},
            {'event':'jev_holding_decision', 'time':'2026-11-11T11:01:01+09:00', 'cycle_id':'b', 'symbol':'114800', 'choice':'SELL','confidence':.75},
        ])
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]['decision'])
        self.assertEqual(rows[0]['jev'], 'INV')
        self.assertTrue(rows[0]['permitted'])
        self.assertEqual(rows[1]['jev_holding'], 'SELL')
        self.assertEqual(rows[1]['cycle_id'], 'b')

    def test_paper_mode_settings_are_explicit_and_show_pending(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        active = {'version':1, 'jev':True, 'decision':True}
        with tempfile.TemporaryDirectory() as d, \
             patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.config.paths.get_runtime_root', return_value=Path(d)), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10), \
             patch('paper_ai_modes.read_modes', return_value=active), \
             patch('paper_ai_modes.read_pending_modes', return_value={'version':1,'jev':True,'decision':False}), \
             patch('paper_ai_modes.write_modes') as save:
            ui = app_test('트레이딩 현황', etf_desk_tab='전략·AI 설정').run()
            self.assertFalse(save.called)
            self.assertTrue(any('다음 안전 주기 적용 대기' in str(w.value) for w in ui.warning))
            self.assertFalse(next(c for c in ui.checkbox if c.label == 'Decision 사용').value)
            next(button for button in ui.button if button.label == '모의 AI 설정 요청').click().run()
            self.assertTrue(save.called)
            self.assertEqual(save.call_args.kwargs, {'jev':True, 'decision':False})

    def test_unknown_paper_modes_do_not_display_assumed_active_models(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        with tempfile.TemporaryDirectory() as d, \
             patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.config.paths.get_runtime_root', return_value=Path(d)), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10), \
             patch('paper_ai_modes.read_modes', side_effect=ValueError('missing')):
            ui = app_test('트레이딩 현황', etf_desk_tab='전략·AI 설정').run()
            self.assertTrue(any('모의 AI 설정 미확인' in str(item.value) for item in ui.error))
            self.assertFalse(any(c.label in ('Jev 사용', 'Decision 사용') for c in ui.checkbox))

    def test_experiment_row_displays_jev_and_zero_quantity(self):
        row = app.experiment_row({'event': 'jev_shadow', 'jev': {'candidate': {'choice': 'INV'}}, 'remaining': 0})
        self.assertEqual(row['판단'], 'INV')
        self.assertEqual(row['잔량'], '0')
        self.assertEqual(app.experiment_row({'event': 'decision', 'choice': 'WAIT'})['판단'], 'WAIT')

    def test_comparison_requires_unique_matching_cycle(self):
        events = [
            {'event':'decision', 'time':'2026-11-06T09:10:00+09:00', 'choice':'WAIT', 'cycle_id':'a'},
            {'event':'jev_shadow', 'time':'2026-11-06T09:10:01+09:00', 'cycle_id':'a', 'openai_choice':'WAIT', 'jev':{'candidate':{'choice':'INV','confidence':0.85}}},
            {'event':'v2_entry_gate', 'time':'2026-11-06T09:10:02+09:00', 'cycle_id':'a', 'permitted':False, 'reason':'trend_not_rising'},
            {'event':'v2_execution_gate', 'time':'2026-11-06T09:10:03+09:00', 'cycle_id':'a', 'permitted':False, 'reason':'learning_filter'},
            {'event':'v2_execution_gate', 'time':'2026-11-06T09:10:04+09:00', 'cycle_id':'wrong', 'permitted':False, 'reason':'order_limit'},
            {'event':'decision', 'time':'2026-11-06T09:11:00+09:00', 'choice':'WAIT'},
            {'event':'jev_shadow', 'time':'2026-11-06T09:11:01+09:00', 'openai_choice':'INV', 'jev':{'candidate':{'choice':'INV'}}},
        ]
        rows = app.comparison_data(events)
        self.assertEqual(rows[0]['jev'], 'INV')
        self.assertFalse(rows[0]['permitted'])
        self.assertEqual(rows[0]['execution_reason'], 'learning_filter')
        self.assertIsNone(rows[1]['jev'])
        self.assertEqual(rows[2]['kind'], 'jev_only')
        self.assertEqual(app.order_outcome(rows[1], {}), '연결 기록 없음')

    def test_jev_holding_opinion_is_separate_from_candidate(self):
        events = [
            {'event': 'v2_holding_decision', 'time': '2026-11-11T11:00:00+09:00', 'choice': 'SELL',
             'symbol': '114800', 'cycle_id': 'hold'},
            {'event': 'jev_holding_decision', 'time': '2026-11-11T11:00:01+09:00', 'choice': 'HOLD',
             'confidence': 0.8, 'symbol': '114800', 'cycle_id': 'hold'},
            {'event': 'jev_shadow', 'time': '2026-11-11T11:00:02+09:00', 'openai_choice': 'SELL',
             'jev': {'candidate': {'choice': 'K200'}}, 'cycle_id': 'hold'},
        ]
        row = app.comparison_data(events)[0]
        self.assertEqual(row['jev_holding'], 'HOLD')
        self.assertEqual(row['jev_holding_confidence'], 0.8)
        self.assertEqual(row['jev'], 'K200')

    def test_actionable_streak_is_labelled_once(self):
        def row(minute, conf):
            return [{'event': 'market_context', 'time': f'2026-11-11T11:{minute:02d}:00+09:00', 'context': 'entry',
                     'cycle_id': str(minute), 'market_bars': {}},
                    {'event': 'jev_shadow', 'time': f'2026-11-11T11:{minute:02d}:01+09:00', 'cycle_id': str(minute),
                     'jev': {'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': conf}}}]
        events = row(1, 0.4) + row(2, 0.65) + row(3, 0.66) + row(4, 0.3) + row(6, 0.7)
        bars = [{'time': int(datetime(2026, 11, 11, 11, m, tzinfo=app.KST).timestamp())} for m in range(8)]
        texts = [(datetime.fromtimestamp(m['time'], app.KST).minute, m['text'])
                 for m in app.chart_markers(app.comparison_data(events), [], bars, 'INV', '114800')]
        self.assertEqual(texts, [(1, 'Jev매수 0.40'), (2, 'Jev매수 0.65 신뢰도 충족'), (3, ''), (4, ''),
                                 (6, 'Jev매수 0.70 신뢰도 충족')])

    def test_chart_distinguishes_confidence_entry_and_order_block(self):
        stamp = datetime(2026, 11, 11, 11, 0, tzinfo=app.KST)
        base = {'time': stamp, 'jev': 'INV', 'jev_confidence': 0.8, 'source': 'jev'}
        bars = [{'time': int(stamp.timestamp())}]
        for row, expected in ((base, '신뢰도 충족'),
                              ({**base, 'permitted': False}, '진입 차단'),
                              ({**base, 'permitted': True}, '진입 통과'),
                              ({**base, 'permitted': True, 'execution_reason': 'order_guard_refused'}, '주문 차단')):
            text = app.chart_markers([row], [], bars, 'INV', '114800')[0]['text']
            self.assertIn(expected, text)
            if 'permitted' not in row:
                self.assertNotIn('진입 통과', text)

    def test_chart_shows_linked_entry_and_order_block_reasons(self):
        stamp = datetime(2026, 11, 11, 11, 0, tzinfo=app.KST)
        bars = [{'time': int(stamp.timestamp())}]
        base = {'time': stamp, 'jev': 'INV', 'jev_confidence': 0.8, 'source': 'jev'}
        blocked = {**base, 'permitted': False, 'reason': 'volume_not_rising'}
        marker = app.chart_markers([blocked], [], bars, 'INV', '114800')[0]
        self.assertIn('진입 차단: 거래량 증가 조건 미충족', marker['text'])
        rejected = {**base, 'permitted': True, 'execution_reason': 'order_guard_refused'}
        marker = app.chart_markers([rejected], [], bars, 'INV', '114800')[0]
        self.assertIn('주문 차단: 공유 계좌 가드 거절', marker['text'])

    def test_holding_basis_follows_multiple_buys_since_position_opened(self):
        now = datetime(2026, 11, 11, 13, 10, tzinfo=app.KST)
        snapshot = {'time': now, 'holdings': [{'종목코드': '229200', '보유수량': 15, '평균매입가': 1010, '현재가': 1020}]}
        fills = [{'시각': '2026-11-11T10:00:00+09:00', '종목': '229200', '구분': 'BUY', '수량': 10, '체결가': 900},
                 {'시각': '2026-11-11T10:30:00+09:00', '종목': '229200', '구분': 'SELL', '수량': 10, '체결가': 950},
                 {'시각': '2026-11-11T11:00:00+09:00', '종목': '229200', '구분': 'BUY', '수량': 10, '체결가': 1000},
                 {'시각': '2026-11-11T12:00:00+09:00', '종목': '229200', '구분': 'SELL', '수량': 5, '체결가': 1005},
                 {'시각': '2026-11-11T12:30:00+09:00', '종목': '229200', '구분': 'BUY', '수량': 10, '체결가': 1020}]
        basis = app.holding_chart_basis(snapshot, fills, '229200', now)
        self.assertEqual(basis['price'], 1010)
        self.assertEqual(datetime.fromtimestamp(basis['start'], app.KST).hour, 11)
        self.assertIsNone(app.holding_chart_basis(snapshot, fills[2:3], '229200', now))
        self.assertIsNone(app.holding_chart_basis(
            {**snapshot, 'holdings': [{**snapshot['holdings'][0], '평균매입가': 1015}]}, fills, '229200', now))

    def test_symbol_pnl_combines_broker_valuation_and_local_realized(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE fills (order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
        db.executemany('INSERT INTO fills VALUES (?,?,?,?,?,?)', [
            ('a', '114800', 'BUY', 1, 1277.0, '2026-11-11T11:03:21+09:00'),
            ('b', '114800', 'SELL', 1, 1252.0, '2026-11-11T13:29:43+09:00'),
            ('c', '233740', 'BUY', 10, 4300.0, '2026-11-10T10:00:00+09:00'),
            ('d', '233740', 'BUY', 10, 4400.0, '2026-11-11T10:00:00+09:00'),
            ('e', '233740', 'SELL', 5, 4450.0, '2026-11-11T11:00:00+09:00')])
        books = app.realized_by_symbol(db, '2026-11-11')
        self.assertEqual(round(books['114800']['realized']), -25)
        self.assertEqual(round(books['233740']['realized']), 500)
        self.assertEqual(books['233740']['qty'], 15)
        holdings = [{'종목코드': '233740', '보유수량': 15, '평균매입가': 4350, '현재가': 4400, '평가손익': 750}]
        rows = {r['종목']: r for r in app.symbol_pnl_rows(holdings, books)}
        self.assertEqual(set(rows), {'KODEX 인버스', 'KODEX 코스닥150레버리지'})
        self.assertEqual((rows['KODEX 인버스']['보유수량'], rows['KODEX 인버스']['합계']), (0, -25))
        self.assertEqual((rows['KODEX 코스닥150레버리지']['실현손익(오늘)'], rows['KODEX 코스닥150레버리지']['합계']), (500, 1250))
        self.assertEqual(app.symbol_pnl_rows([], {}), [])
        lev = rows['KODEX 코스닥150레버리지']
        self.assertEqual((lev['평가수익률'], lev['누적수익률']), (1.15, 2.3))  # 750/65,250 · 500/(4,350x5)
        month = {r['종목']: r for r in app.symbol_pnl_rows(holdings, books, period=('2026-11', '2026-11'))}
        self.assertEqual((month['KODEX 인버스']['실현손익(2026-11)'], month['KODEX 인버스']['실현수익률(2026-11)']), (-25, -1.96))
        self.assertIsNone(app.symbol_pnl_rows(holdings, books, period=('2026-10', '2026-10'))[0]['실현수익률(2026-10)'])
        rows_list = app.symbol_pnl_rows(holdings, books)
        self.assertEqual([r['종목'] for r in rows_list if app.HOLDING_STATES['보유'](r)], ['KODEX 코스닥150레버리지'])
        self.assertEqual([r['종목'] for r in rows_list if app.HOLDING_STATES['미보유'](r)], ['KODEX 인버스'])
        self.assertEqual(len([r for r in rows_list if app.HOLDING_STATES['전체'](r)]), 2)
        days = app.realized_by_period(books, '일')
        self.assertEqual([(r['기간'], r['매도 횟수'], r['실현손익']) for r in days], [('2026-11-11', 2, 475)])
        self.assertEqual(app.realized_by_period(books, '연')[0]['수익률'], round(475 / (1277 + 4350 * 5) * 100, 2))

    def test_jev_evidence_pairs_the_answer_with_inputs_from_the_same_cycle(self):
        bars = {'INV': [{'bsop_time': '134800', 'stck_prpr': '1260'}, {'bsop_time': '134700', 'stck_prpr': '1250'}],
                'K200': [{'bsop_time': '134800', 'stck_prpr': '84000'}, {'bsop_time': '134700', 'stck_prpr': '84000'}]}
        events = [
            {'event': 'market_context', 'time': '2026-11-11T13:49:00+09:00', 'cycle_id': 'old', 'market_bars': {}, 'news': []},
            {'event': 'market_context', 'time': '2026-11-11T13:50:00+09:00', 'cycle_id': 'c1', 'market_bars': bars,
             'news': [{'title': 't', 'published': '2026-11-10T08:58:07+00:00', 'publisher': 'p'}]},
            {'event': 'jev_shadow', 'time': '2026-11-11T13:50:01+09:00', 'cycle_id': 'c1',
             'jev': {'candidate': {'choice': 'INV', 'confidence': 0.42, 'probabilities': {'WAIT': 0.2, 'INV': 0.5}}}},
            {'event': 'jev_holding_decision', 'time': '2026-11-11T13:50:02+09:00', 'symbol': '114800', 'choice': 'HOLD', 'confidence': 0.1}]
        evidence = app.jev_evidence(events)
        self.assertEqual((evidence['choice'], evidence['confidence'], len(evidence['news'])), ('INV', 0.42, 1))
        self.assertEqual(evidence['holding']['choice'], 'HOLD')
        frame = app.jev_chart_frame(evidence['bars'])
        self.assertEqual(list(frame.index), ['13:47', '13:48'])
        self.assertAlmostEqual(frame['KODEX 인버스']['13:48'], 0.8)
        self.assertIsNone(app.jev_evidence(events[:1]))

    def test_pnl_rows_include_scanner_stocks_by_name(self):
        books = {'005930': {'qty': 0, 'avg': 0.0, 'realized': 1200.0, 'today': 1200.0, 'buys': 1, 'sells': 1}}
        holdings = [{'종목코드': '000660', '종목': 'SK하이닉스', '보유수량': 1, '평균매입가': 1000, '현재가': 1010, '평가손익': 10}]
        rows = {r['종목']: r for r in app.symbol_pnl_rows(holdings, books, {'005930': '삼성전자'})}
        self.assertEqual(set(rows), {'삼성전자', 'SK하이닉스'})
        self.assertEqual(rows['삼성전자']['합계'], 1200)
        self.assertEqual(rows['SK하이닉스']['합계'], 10)

    def test_zone_places_the_price_between_stop_and_target(self):
        self.assertAlmostEqual(app.position_zone(1000, 1005, 3.0, 4.0)[2], 3.5 / 7)
        self.assertEqual((app.targets_text(1010, 1020), app.targets_text(1020, None), app.targets_text(1050, 1050)),
                         ('절반 1,010 · 전량 1,020', '절반 1,020 · 나머지 추적 청산', '익절 1,050'))

    def test_activity_frames_bucket_kinds_and_wait_reasons(self):
        day = datetime(2026, 11, 11, tzinfo=app.KST).date()
        events = [{'time': '2026-11-11T10:03:00+09:00', 'event': 'jev_shadow'},
                  {'time': '2026-11-11T10:07:00+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'jev_low_confidence'},
                  {'time': '2026-11-11T10:12:00+09:00', 'event': 'submitted'},
                  {'time': '2026-11-10T10:12:00+09:00', 'event': 'submitted'}]
        counts, waits = app.activity_frames(events, day)
        self.assertEqual(sorted(zip(counts['종류'], counts['건수'])), [('Jev 판단', 1), ('대기', 1), ('주문', 1)])
        self.assertEqual(waits.iloc[0]['사유'], 'Jev 신뢰도가 진입 기준 미만')

    def test_position_zone_labels_direction_with_text_and_gauge(self):
        self.assertEqual(app.position_zone(1000, 1010)[1], '▲ 수익 구간 +1.00%')
        self.assertAlmostEqual(app.position_zone(1000, 1010)[2], 0.75)
        self.assertEqual(app.position_zone(1000, 990)[1], '▼ 손실 구간 -1.00%')
        self.assertAlmostEqual(app.position_zone(1000, 990)[2], 0.25)
        self.assertEqual(app.position_zone(1000, 1000)[1:], ('■ 보합 0.00%', 0.5))
        self.assertEqual(app.position_zone(1000, 900)[2], 0.0)

    def test_repeated_opinions_are_marked_only_when_they_change(self):
        def row(minute, jev):
            return [{'event': 'market_context', 'time': f'2026-11-11T11:{minute:02d}:00+09:00', 'context': 'holding',
                     'symbol': '114800', 'cycle_id': str(minute)},
                    {'event': 'jev_holding_decision', 'time': f'2026-11-11T11:{minute:02d}:01+09:00',
                     'choice': jev, 'confidence': 0.4, 'symbol': '114800', 'cycle_id': str(minute)}]
        events = row(0, 'SELL') + row(1, 'SELL') + row(2, 'HOLD') + row(3, 'SELL') + row(4, 'SELL') + row(5, 'SELL')
        bars = [{'time': int(datetime(2026, 11, 11, 11, m, tzinfo=app.KST).timestamp())} for m in range(6)]
        markers = app.chart_markers(app.comparison_data(events), [], bars, 'INV', '114800')
        self.assertEqual([(datetime.fromtimestamp(m['time'], app.KST).minute, m['text']) for m in markers],
                         [(0, 'Jev매도 0.40'), (1, ''), (3, 'Jev매도 0.40'), (4, ''), (5, '0.40')])

    def test_chart_marks_jev_sell_as_holding_opinion_not_fill(self):
        events = [
            {'event': 'v2_holding_decision', 'time': '2026-11-11T11:00:00+09:00', 'choice': 'SELL',
             'symbol': '114800', 'cycle_id': 'hold'},
            {'event': 'jev_holding_decision', 'time': '2026-11-11T11:00:01+09:00', 'choice': 'SELL',
             'confidence': 0.8, 'symbol': '114800', 'cycle_id': 'hold'},
        ]
        stamp = int(datetime(2026, 11, 11, 11, 0, tzinfo=app.KST).timestamp())
        markers = app.chart_markers(app.comparison_data(events), [], [{'time': stamp}], 'INV', '114800')
        self.assertEqual([marker['text'] for marker in markers], ['Decision: SELL', 'Jev매도 0.80 신뢰도 충족'])

    def test_disabling_jev_opinions_keeps_confirmed_fills(self):
        stamp = datetime(2026, 11, 11, 11, 0, tzinfo=app.KST)
        rows = [{'time': stamp, 'jev': 'INV', 'jev_confidence': .8,
                 'jev_holding': 'SELL', 'jev_holding_confidence': .8, 'symbol': '114800'}]
        fills = [{'시각': stamp.isoformat(), '종목': '114800', '구분': 'BUY', '수량': 3}]
        bars = [{'time': int(stamp.timestamp())}]
        visible = app.chart_markers(rows, fills, bars, 'INV', '114800')
        hidden = app.chart_markers(rows, fills, bars, 'INV', '114800', show_jev=False)
        self.assertEqual(len(visible), 3)
        self.assertEqual([row['text'] for row in hidden], ['체결 확인: 매수 3주'])

    def test_insufficient_buying_power_is_displayed_in_korean(self):
        events = [
            {'event': 'decision', 'time': '2026-11-11T09:10:00+09:00', 'choice': 'INV', 'cycle_id': 'a'},
            {'event': 'v2_execution_gate', 'time': '2026-11-11T09:10:01+09:00', 'cycle_id': 'a',
             'permitted': False, 'reason': 'insufficient_buying_power'},
        ]
        row = app.comparison_data(events)[0]
        self.assertEqual(app.execution_reason_label(row['execution_reason']), '모의 주문 가능 금액 부족')
        self.assertEqual(app.entry_reason_label('signal_or_session_invalid'), '시그널 만료 또는 거래 중단')

    def test_order_statuses_only_use_confirmed_link_and_fills(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE fills(order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT)')
            self.assertEqual(app.paper_order_records(db), ([], None))
        with sqlite3.connect(':memory:') as db:
            db.executescript('''CREATE TABLE fills(order_id TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL, time TEXT);
                CREATE TABLE intents(id TEXT, status TEXT, symbol TEXT, side TEXT, qty INTEGER);
                CREATE TABLE decision_order_links(order_id TEXT, cycle_id TEXT);
                INSERT INTO intents VALUES ('a','acknowledged','114800','BUY',1), ('b','terminal','114800','BUY',2), ('c','uncertain','114800','SELL',1), ('d','terminal','114800','SELL',1);
                INSERT INTO decision_order_links VALUES ('a','1'), ('b','2'), ('c','3'), ('d','4');
                INSERT INTO fills VALUES ('b','114800','BUY',1,100,'2026-11-06T09:10:00+09:00');''')
            fills, records = app.paper_order_records(db)
            self.assertEqual(len(fills), 1)
            self.assertIn('주문 접수·체결 대조 중', app.order_outcome({'cycle_id':'1'}, records))
            self.assertIn('부분 체결 확인·주문 종료 · 확인 1주 / 100.0원', app.order_outcome({'cycle_id':'2'}, records))
            self.assertIn('매도 1주 · 주문 결과 미확인', app.order_outcome({'cycle_id':'3'}, records))
            self.assertIn('주문 종료·체결 기록 없음', app.order_outcome({'cycle_id':'4'}, records))
            self.assertEqual(app.order_outcome({'cycle_id':'5'}, records), '연결된 주문 없음(원장 기준)')
            self.assertEqual(app.order_outcome({'cycle_id':'5'}, None), '주문 원장 확인 불가')

    def test_risk_exit_is_separate_from_model_opinion_and_not_a_fill(self):
        events = [{'event':'v2_risk_exit', 'time':'2026-11-06T09:11:00+09:00',
                   'symbol':'114800', 'cycle_id':'risk'}]
        row = app.comparison_data(events)[0]
        self.assertEqual(row['kind'], 'v2_risk_exit')
        self.assertIsNone(row['decision'])
        self.assertEqual(app.order_outcome(row, {}), '연결된 주문 없음(원장 기준)')

    def test_risk_exit_error_is_shown_as_unsent_and_never_a_fill(self):
        event = {'event': 'v2_risk_exit_error', 'time': '2026-11-11T11:10:00+09:00',
                 'symbol': '114800', 'reason': 'signal_or_session_invalid', 'cycle_id': 'risk'}
        self.assertEqual(app.experiment_row(event)['사유'], '시그널 만료 또는 거래 중단')
        warning = app.risk_exit_warning([event])
        self.assertIn('보호매도 미전송', warning)
        self.assertIn('시그널 만료 또는 거래 중단', warning)
        self.assertEqual(app.comparison_data([event]), [])
        self.assertIsNone(app.risk_exit_warning([]))

    def test_chart_uses_only_same_minute_for_markers(self):
        events = [{'event':'decision', 'time':'2026-11-06T09:12:00+09:00', 'choice':'INV',
            'market_bars':{'INV':[{'bsop_time':'091000','stck_oprc':'100','stck_hgpr':'102','stck_lwpr':'99','stck_prpr':'101'},
                                  {'bsop_time':'091100','stck_oprc':'101','stck_hgpr':'103','stck_lwpr':'100','stck_prpr':'102'}]}}]
        day = datetime(2026, 11, 6).date()
        bars = app.chart_bars(events, 'INV', day)
        self.assertEqual(len(bars), 2)
        fills = [{'시각':'2026-11-06T09:11:30+09:00','종목':'114800','구분':'BUY'},
                 {'시각':'2026-11-06T09:12:30+09:00','종목':'114800','구분':'SELL'}]
        markers = app.chart_markers(app.comparison_data(events), fills, bars, 'INV', '114800')
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]['text'], '체결 확인: 매수')
        self.assertEqual(app.chart_bars([], 'INV', day), [])
        gap = app.chart_markers([], [{'시각':'2026-11-06T09:09:30+09:00','종목':'114800','구분':'BUY','수량':10}], bars, 'INV', '114800')
        self.assertEqual((gap[0]['time'], gap[0]['text']), (bars[0]['time'], '체결 확인: 매수 10주 (09:09)'))

    def test_live_balance_requires_complete_paginated_result(self):
        reader = Adapter(account_type='01')
        with patch('nh_live_broker._pages', return_value={'Output_0': {}, 'Output_1': [], '_pagination_complete': True}) as pages:
            self.assertEqual(app.live_balance(reader, 'paper')['Output_1'], [])
            self.assertEqual(pages.call_args.args[1], 'krstockInquiryBalance')
        with patch('nh_live_broker._pages', return_value={'Output_0': {}, 'Output_1': [], '_pagination_complete': False}):
            with self.assertRaises(ValueError):
                app.live_balance(reader, 'paper')

    def test_live_orders_require_complete_pages_and_do_not_expose_account(self):
        now = datetime(2026, 11, 11, 11, 0, tzinfo=app.KST)
        row = {'act_no':'secret-account', 'iem_cd':'114800', 'orr_qty':2, 'tot_cns_qty':1,
               'ny_cns_qty':0, 'can_qty':1, 'cns_avg_uit_pr':1277, 'orr_rjt_rsn_cd_nm':''}
        with patch.object(app, 'verify_account'), patch.object(app, 'read_live_pages', return_value={'Output_0':[row], '_pagination_complete':True}):
            rows = app.live_order_rows(Adapter(), 'paper', now)
        self.assertEqual(rows[0]['체결수량'], 1)
        self.assertEqual(rows[0]['취소수량'], 1)
        self.assertNotIn('secret-account', str(rows))
        for result in ({'Output_0': [], '_pagination_complete':False}, {'Output_0':[None], '_pagination_complete':True}):
            with patch.object(app, 'verify_account'), patch.object(app, 'read_live_pages', return_value=result):
                with self.assertRaises(ValueError):
                    app.live_order_rows(Adapter(), 'paper', now)

    def test_live_start_disabled_but_stop_remains_available(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        with patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10):
            ui = app_test('실계좌').run()
            start = next(b for b in ui.button if b.label == '실계좌 자동매매 시작')
            self.assertTrue(start.disabled)
            self.assertIn('시작할 수 없습니다', start.help)
            self.assertFalse(next(b for b in ui.button if b.label == '실계좌 신규 매수 중단').disabled)

    def test_live_controls_remain_without_live_accounts(self):
        from accounts import DEFAULTS
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        with patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10), \
             patch('accounts.load', return_value=dict(DEFAULTS['accounts'])):
            ui = app_test('실계좌').run()
            self.assertFalse(ui.exception)
            self.assertFalse(next(b for b in ui.button if b.label == '실계좌 신규 매수 중단').disabled)

    def test_live_order_query_distinguishes_empty_from_failed(self):
        from accounts import DEFAULTS
        paper_config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        read_config = SimpleNamespace(args=['--mode', 'readonly', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': paper_config, 'nhplug_readonly': read_config})
        records = {**DEFAULTS['accounts'], 'live-1': {'label': '실계좌 1', 'kind': 'live', 'engine': 'jev', 'auto': True, 'hash': 'h',
                   'masked': '***', 'budget': {'capital_krw': 500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}}
        read, paper = Adapter(account_type='01'), Adapter()

        def pages(adapter, operation, fields, block):
            if block == 'Output_1':  # balance: cash and no holdings
                return {'Output_0': {'dca': 0}, 'Output_1': [], '_pagination_complete': True}
            return {'Output_0': [], '_pagination_complete': True}
        with patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[read, paper] * 10), \
             patch('accounts.load', return_value=records), patch('accounts.resolve', return_value={'live-1': 'paper'}), \
             patch('nh_live_broker._pages', side_effect=pages) as paging:
            ui = app_test('실계좌').run()
            next(b for b in ui.button if b.label == '실계좌 1 잔고·주문 조회').click().run()
            data = ui.session_state['live_balance_all_data']
            self.assertEqual((data['orders'], data['errors']), ([], []))  # an empty day, not a failure
            paging.side_effect = ValueError('incomplete')
            next(b for b in ui.button if b.label == '실계좌 1 잔고·주문 조회').click().run()
            self.assertEqual(len(ui.session_state['live_balance_all_data']['errors']), 1)
            self.assertTrue(any('비었다고 간주하지 마세요' in str(e.value) for e in ui.error))

    def test_shared_runner_caption_says_listing_is_not_an_order_target(self):
        config = SimpleNamespace(args=['--mode', 'paper', '--account', 'paper'])
        configs = SimpleNamespace(mcp_servers={'nhplug_paper': config, 'nhplug_readonly': config})
        with patch('src.config.loader.load_agent_config', return_value=configs), \
             patch('src.tools.mcp.MCPServerAdapter', side_effect=[Adapter(), Adapter()] * 10):
            ui = app_test('트레이딩 현황', etf_desk_tab='전략·AI 설정').run()
            self.assertTrue(any('목록에 표시되는 것만으로 자동주문 대상' in str(item.value) for item in ui.caption))

class LiveMarkerTest(unittest.TestCase):
    def test_filled_orders_become_fills_and_virtual_trades_land_on_next_bar(self):
        from app import order_fills, virtual_markers, KST
        from datetime import date, datetime
        day = date(2026, 11, 12)
        t = lambda h, m: int(datetime(2026, 11, 12, h, m, tzinfo=KST).timestamp())
        orders = [{'종목코드': '069500', '체결수량': 1, '평균체결가': 9000, '구분': '현금매수', '주문 시각': '100130'},
                  {'종목코드': '069500', '체결수량': 0, '구분': '매도', '주문 시각': '100000'},
                  {'종목코드': '069500', '체결수량': 1, '구분': '매도', '주문 시각': None}]
        self.assertEqual(order_fills(orders, day), [{'시각': datetime(2026, 11, 12, 10, 1, 30, tzinfo=KST).isoformat(),
                                                     '종목': '069500', '구분': 'BUY', '수량': 1, '체결가': 9000}])
        bars = [{'time': t(10, 0)}, {'time': t(10, 2)}]
        trades = [{'time': '2026-11-12T10:01:20+09:00', 'symbol': '069500', 'side': 'SELL'},
                  {'time': '2026-11-12T10:00:00+09:00', 'symbol': '114800', 'side': 'BUY'}]
        self.assertEqual([(m['time'], m['text']) for m in virtual_markers(trades, bars, '069500')],
                         [(t(10, 2), '가상 재현 매도 (10:01)')])

    def test_shadow_symbols_and_markers_distinguish_replay_from_real_fills(self):
        from app import shadow_chart_symbols, virtual_markers, KST
        from datetime import datetime
        t = int(datetime(2026, 11, 16, 10, 1, tzinfo=KST).timestamp())
        trades = [
            {'time': '2026-11-16T10:01:00+09:00', 'symbol': '100790', 'side': 'BUY'},
            {'time': '2026-11-16T10:02:00+09:00', 'symbol': '100790', 'side': 'SELL', 'pnl': 300},
            {'time': '2026-11-16T10:03:00+09:00', 'symbol': '280360', 'side': 'SELL', 'pnl': -50, 'reason': '모의 원장 매도'},
            {'time': '2026-11-16T10:04:00+09:00', 'symbol': '005930', 'side': 'SELL', 'pnl': -20, 'reason': '실계좌 손절 −1%'},
        ]
        charts = {'미래에셋벤처투자 (100790)': ('100790', '100790')}
        codes, status = shadow_chart_symbols(charts, {'open': {'100790': {'qty': 5}}, 'trades': trades}, {})
        self.assertEqual(len(codes), 3)
        self.assertEqual(status['100790']['qty'], 5)
        self.assertEqual(status['100790']['outcome'], '익절')
        self.assertEqual(status['280360']['outcome'], '손실 매도')
        self.assertEqual(status['005930']['outcome'], '손절')
        markers = virtual_markers(trades, [{'time': t}, {'time': t + 60}], '100790')
        self.assertEqual([m['text'] for m in markers], ['가상 재현 매수', '가상 재현 매도'])
        self.assertNotEqual(markers[0]['color'], markers[1]['color'])
        self.assertTrue(all(m['shape'] == 'circle' for m in markers))


class ChartDataTest(unittest.TestCase):
    def test_period_rows_become_ascending_bars_and_ticks_merge_per_second(self):
        from app import bars_from_period
        row = lambda d, t, o, h, l, c, v: {'bsop_date': d, 'bsop_time': t, 'stck_oprc': str(o), 'stck_hgpr': str(h),
                                           'stck_lwpr': str(l), 'stck_prpr': str(c), 'vol': str(v)}
        ticks = [row('20261112', '100001', 103, 103, 103, 103, 5), row('20261112', '100000', 101, 101, 101, 101, 2),
                 row('20261112', '100000', 100, 100, 100, 100, 1)]  # newest first, two trades in one second
        bars = bars_from_period(ticks, '틱')
        self.assertEqual([(b['open'], b['high'], b['low'], b['close'], b['volume']) for b in bars], [(100, 101, 100, 101, 3), (103, 103, 103, 103, 5)])
        months = bars_from_period([row('202611', '', 10, 12, 9, 11, 7), row('202610', '', 9, 10, 8, 10, 6), row('202609', '', 0, 1, 0, 1, 1)], '월')
        self.assertEqual(len(months), 2)  # the zero-priced row is dropped
        self.assertLess(months[0]['time'], months[1]['time'])
        years = bars_from_period([row('202612', '', 10, 12, 9, 11, 7)], '년')
        self.assertEqual(datetime.fromtimestamp(years[0]['time'], app.KST).strftime('%Y-%m-%d'), '2026-01-01')

    def test_moving_averages_start_once_enough_bars_exist(self):
        from app import moving_averages
        bars = [{'time': i, 'close': c} for i, c in enumerate([1, 2, 3, 4, 5])]
        lines = moving_averages(bars, [3, 20])
        self.assertEqual(lines[3], [{'time': 2, 'value': 2.0}, {'time': 3, 'value': 3.0}, {'time': 4, 'value': 4.0}])
        self.assertNotIn(20, lines)

    def test_fills_land_on_the_bar_that_contains_them(self):
        from app import containing_markers
        day = lambda d: int(datetime(2026, 11, d, tzinfo=app.KST).timestamp())
        bars = [{'time': day(11)}, {'time': day(12)}]
        fills = [{'시각': datetime(2026, 11, 12, 10, 5, tzinfo=app.KST).isoformat(), '종목': '069500', '구분': 'BUY', '수량': 3},
                 {'시각': datetime(2026, 11, 10, 10, 5, tzinfo=app.KST).isoformat(), '종목': '069500', '구분': 'SELL', '수량': 1},
                 {'시각': datetime(2026, 11, 12, 11, 0, tzinfo=app.KST).isoformat(), '종목': '114800', '구분': 'SELL', '수량': 1}]
        self.assertEqual([(m['time'], m['text']) for m in containing_markers(fills, bars, '069500')], [(day(12), '매수 3주')])

    def test_investor_rows_keep_the_four_groups_oldest_first(self):
        from app import investor_rows
        class Reader:
            def call_tool(self, tool, params):
                return {'status': 'ok', 'data': {'Output_0': [
                    {'bsop_date1': '20261111', 'frgn_ntby_qty': 0, 'gigwan': -5, 'person': 3, 'program': 7},
                    {'bsop_date1': '20261110', 'frgn_ntby_qty': -2, 'gigwan': 1, 'person': 1, 'program': -1},
                    {'bsop_date1': 'bad'}]}}
        rows = investor_rows(Reader(), '005930')
        self.assertEqual([(r['날짜'].day, r['외국인'], r['프로그램']) for r in rows], [(10, -2, -1), (11, 0, 7)])


if __name__ == '__main__':
    unittest.main()


class DashboardHeaderTest(unittest.TestCase):
    def test_day_summary_counts_one_day_only(self):
        from datetime import date
        day = date(2026, 11, 12)
        events = [{'time': '2026-11-12T10:00:00+09:00', 'event': 'jev_shadow', 'jev': {'candidate': {'confidence': 0.4}}},
                  {'time': '2026-11-12T10:01:00+09:00', 'event': 'jev_shadow', 'jev': {'candidate': {'confidence': 0.6}}},
                  {'time': '2026-11-12T10:01:00+09:00', 'event': 'v2_entry_gate', 'permitted': False, 'reason': 'jev_low_confidence'},
                  {'time': '2026-11-12T10:02:00+09:00', 'event': 'v2_entry_gate', 'permitted': True, 'reason': 'entry_conditions_met'},
                  {'time': '2026-11-12T10:02:01+09:00', 'event': 'submitted'},
                  {'time': '2026-11-11T10:00:00+09:00', 'event': 'jev_shadow', 'jev': {'candidate': {'confidence': 0.9}}}]
        fills = [{'시각': '2026-11-12T10:03:00+09:00', '구분': 'BUY'}, {'시각': '2026-11-11T10:03:00+09:00', '구분': 'SELL'}]
        summary = app.day_summary(events, day, fills)
        self.assertEqual((summary['judgments'], summary['max_conf'], summary['permitted'], summary['orders']), (2, 0.6, 1, 1))
        self.assertEqual((summary['buys'], summary['sells'], summary['waits']), (1, 0, 1))
        self.assertEqual(summary['top_wait'][1], 1)

class NewsLinkTest(unittest.TestCase):
    def test_headline_becomes_a_safe_search_link(self):
        link = app.news_link({'title': '美 반도체 [속보] $5 급등 - 엠에스투데이'})
        self.assertTrue(link.startswith('[美 반도체 \\[속보\\] \\$5 급등 - 엠에스투데이](https://news.google.com/search?q='))
        self.assertNotIn(' ', link.split('](', 1)[1])  # the URL itself is fully encoded
        self.assertIn('%5B%EC%86%8D%EB%B3%B4%5D', link)


class MoneyFormatTests(unittest.TestCase):
    def test_korean_money_reading(self):
        self.assertEqual(app.krw_words(21_000_000), '2,100만 원')
        self.assertEqual(app.krw_words(150_000_000), '1억 5,000만 원')
        self.assertEqual(app.krw_words(300_000), '30만 원')
        self.assertEqual(app.krw_words(12_345), '1만 2,345원')
        self.assertEqual(app.krw_words(0), '0 원')
        self.assertEqual(app.krw_words(None), '—')


def _sidebar_chart_flow():
    import app
    import sidebar
    import streamlit as st

    st.button('vibe 종목 열기', on_click=sidebar.open_trading_chart, args=('vibe', '005930'))
    app.paper_trade_chart(None, None, False, False)


def _live_shadow_chart_flow():
    import app
    shadow = {'open': {'100790': {'qty': 65}}, 'trades': [
        {'time': '2026-11-16T11:59:00+09:00', 'symbol': '100790', 'side': 'SELL', 'pnl': -14520,
         'reason': '실계좌 손절 −1%'}]}
    app.paper_trade_chart(None, None, False, False, fills=[], key='live_test', shadow=shadow)


def _visibility_chart_flow():
    import app
    from datetime import datetime
    now = datetime.now(app.KST).replace(second=0, microsecond=0)
    fills = [{'시각': now.isoformat(), '종목': '114800', '구분': 'BUY', '수량': 1}]
    shadow = {'open': {'114800': {'qty': 1}}, 'trades': [
        {'time': now.isoformat(), 'symbol': '114800', 'side': 'SELL', 'pnl': 100}]}
    app.paper_trade_chart(None, None, False, False, fills=fills, key='visibility', shadow=shadow, engine='vibe')


def test_chart_opinion_toggles_preserve_real_and_virtual_trades(tmp_path):
    from unittest.mock import patch
    import engine_roles
    import research_store
    now = datetime.now(app.KST).replace(second=0, microsecond=0)
    bar = {'time': int(now.timestamp()), 'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 10}
    rows = [{'time': now, 'jev': 'INV', 'jev_confidence': .8}]
    with patch.object(app, 'get_runtime_root', return_value=tmp_path), \
         patch.object(engine_roles, 'ledger_root', return_value=tmp_path), \
         patch.object(engine_roles, 'main_engine', return_value='jev'), \
         patch.object(app, 'read_events', return_value=[]), \
         patch.object(app, 'movers_snapshot', return_value={}), \
         patch.object(app, 'watchlist_snapshot', return_value={}), \
         patch.object(app, 'stock_names', return_value={}), \
         patch.object(app, 'period_bars', return_value=[bar]), \
         patch.object(app, 'comparison_data', return_value=rows), \
         patch.object(research_store, 'history', return_value=[]), \
         patch.object(app, 'tradingview_chart') as chart, patch.object(app, 'investor_panel'):
        ui = AppTest.from_function(_visibility_chart_flow).run()
        assert not ui.exception
        def texts(): return [row['text'] for row in chart.call_args.args[1]]
        assert any('Jev매수' in text for text in texts())
        ui.toggle(key='visibility_show_jev').set_value(False).run()
        assert not any('Jev매수' in text for text in texts())
        assert not ui.exception  # the vibe marker toggle is gone: no account follows vibe (2026-11-17)
        assert any('체결 확인: 매수' in text for text in texts())
        assert '가상 재현 매도' in texts()


def test_live_shadow_chart_selects_replayed_holdings_and_sell_without_duplicates(tmp_path):
    from unittest.mock import patch
    with patch.object(app, 'get_runtime_root', return_value=tmp_path), \
         patch.object(app, 'read_events', return_value=[]), \
         patch.object(app, 'movers_snapshot', return_value={}), \
         patch.object(app, 'watchlist_snapshot', return_value={'selected': ['100790']}), \
         patch.object(app, 'stock_names', return_value={'100790': '미래에셋벤처투자'}), \
         patch.object(app, 'period_bars', return_value=[]), \
         patch.object(app, 'investor_panel'):
        ui = AppTest.from_function(_live_shadow_chart_flow).run()
        assert not ui.exception
        options = ui.selectbox(key='live_test_chart_symbol').options
        matches = [option for option in options if '100790' in option]
        assert len(matches) == 1
        assert '가상 재현 보유 65주' in matches[0]
        assert '가상 재현 손절 11/16 11:59' in matches[0]
        assert ui.selectbox(key='live_test_chart_symbol').value == '100790'


def test_sidebar_click_changes_existing_chart_and_reads_selected_engine(tmp_path):
    from unittest.mock import patch
    import engine_roles

    nhplug = tmp_path / 'nhplug'
    for folder in ('shared-etf-paper', 'vibe-paper'):
        root = nhplug / folder
        root.mkdir(parents=True)
        (root / 'events.jsonl').write_text('')
        with sqlite3.connect(root / 'orders.sqlite3') as db:
            db.execute('CREATE TABLE fills (time TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL)')
            db.execute('INSERT INTO fills VALUES (?,?,?,?,?)', ('2026-11-16T09:00:00+09:00', '005930', 'BUY', 1, 100))
    def open_root(_root, engine):
        return nhplug / ('shared-etf-paper' if engine == 'jev' else 'vibe-paper')
    reads = []
    def events(path, count):
        reads.append(path)
        return []
    actual_fills = []
    original_records = app.paper_order_records
    def records(db):
        result = original_records(db)
        actual_fills.append(result[0])
        return result
    with patch.object(app, 'paper_order_records', side_effect=records), \
         patch.object(app, 'get_runtime_root', return_value=tmp_path), \
         patch.object(engine_roles, 'ledger_root', side_effect=open_root), \
         patch.object(engine_roles, 'main_engine', return_value='jev'), \
         patch.object(app, 'read_events', side_effect=events), \
         patch.object(app, 'movers_snapshot', return_value={}), \
         patch.object(app, 'watchlist_snapshot', return_value={}), \
         patch.object(app, 'stock_names', return_value={'005930': '삼성전자'}), \
         patch.object(app, 'period_bars', return_value=[]), \
         patch.object(app, 'investor_panel'):
        ui = AppTest.from_function(_sidebar_chart_flow).run()
        assert not ui.exception
        before = ui.selectbox(key='paper_desk_chart_symbol').value
        ui.button[0].click().run()
        assert not ui.exception
        assert ui.session_state['main_tab'] == '트레이딩 현황'
        assert ui.selectbox(key='paper_desk_chart_symbol').value != before
        assert '005930' in ui.selectbox(key='paper_desk_chart_symbol').value
        assert reads[-1] == nhplug / 'vibe-paper' / 'events.jsonl'
        assert actual_fills[-1][0]['종목'] == '005930'
        assert actual_fills[-1][0]['구분'] == 'BUY'
        assert any('가상 체결 원장' in row.value for row in ui.caption)


def test_chart_options_identify_engine_holdings_and_recent_stops(tmp_path):
    from unittest.mock import patch
    import engine_roles
    import trading_positions

    nhplug = tmp_path / 'nhplug'
    for folder in ('shared-etf-paper', 'vibe-paper'):
        root = nhplug / folder
        root.mkdir(parents=True)
        (root / 'events.jsonl').write_text('')
        with sqlite3.connect(root / 'orders.sqlite3') as db:
            db.execute('CREATE TABLE fills (time TEXT, symbol TEXT, side TEXT, quantity INTEGER, price REAL)')
    data = {
        'jev': {'status': 'ok', 'holdings': [{'symbol': '005930'}, {'symbol': '114800'}],
                'recent_stops': [{'symbol': '005930', 'time': '2026-11-13T15:05:00+09:00'}, {'symbol': '005930', 'time': '2026-11-13T11:17:00+09:00'}]},
        'vibe': {'status': 'ok', 'holdings': [{'symbol': '005930'}], 'recent_stops': []},
    }
    with patch.object(app, 'get_runtime_root', return_value=tmp_path), \
         patch.object(trading_positions, 'snapshot', return_value=data), \
         patch.object(engine_roles, 'ledger_root', side_effect=lambda root, engine: nhplug / ('vibe-paper' if engine == 'vibe' else 'shared-etf-paper')), \
         patch.object(engine_roles, 'main_engine', return_value='jev'), \
         patch.object(app, 'movers_snapshot', return_value={}), \
         patch.object(app, 'watchlist_snapshot', return_value={'selected': ['114800', '005930']}), \
         patch.object(app, 'stock_names', return_value={'005930': '삼성전자'}), \
         patch.object(app, 'period_bars', return_value=[]), \
         patch.object(app, 'investor_panel'):
        ui = AppTest.from_function(_sidebar_chart_flow).run()
        options = ui.selectbox(key='paper_desk_chart_symbol').options
        matches = [option for option in options if '005930' in option]
        assert len(matches) == 1
        assert '보유' in matches[0] and 'Jev' in matches[0] and 'vibe-trading' in matches[0]
        assert '11/13 15:05' in matches[0] and '11/13 11:17' not in matches[0]
        assert len([option for option in options if 'KODEX 인버스' in option]) == 1
        ui.selectbox(key='paper_desk_chart_symbol').select(matches[0]).run()
        ui.radio(key='paper_desk_engine_005930').set_value('vibe').run()
        assert not ui.exception
        assert any('vibe 엔진 · 가상 체결 원장' in row.value for row in ui.caption)
        inverse = next(option for option in options if 'KODEX 인버스' in option)
        ui.selectbox(key='paper_desk_chart_symbol').select(inverse).run()
        assert ui.selectbox(key='paper_desk_chart_symbol').value == inverse


def test_with_broker_fills_counts_a_sale_the_mirror_has_not_recorded_yet():
    recorded = [{'time': '2026-11-17T00:00:00+09:00', 'symbol': '114800', 'side': 'BUY', 'qty': 10, 'price': 1000.0, 'order_no': 1},
                {'time': '2026-11-17T00:00:00+09:00', 'symbol': '114800', 'side': 'BUY', 'qty': 10, 'price': 1100.0, 'order_no': 2}]
    snapshot = {'orders': [{'종목코드': '114800', '구분': '현금매수', '체결수량': 20, '평균체결가': 1100, '주문번호': 2},  # later partial fill
                           {'종목코드': '114800', '구분': '현금매도', '체결수량': 30, '평균체결가': 1200, '주문번호': 3},
                           {'종목코드': '114800', '구분': '현금매도', '체결수량': 0, '평균체결가': 0, '주문번호': 4}]}
    rows = app.with_broker_fills(recorded, snapshot, '2026-11-17')
    assert [(r['order_no'], r['qty']) for r in rows] == [(1, 10), (2, 20), (3, 30)]
    books = app.books_from_rows([(r['time'], r['symbol'], r['side'], r['qty'], r['price']) for r in rows], '2026-11-17')
    assert round(books['114800']['today']) == 30 * 1200 - (10 * 1000 + 20 * 1100)
    assert app.with_broker_fills(recorded, None, '2026-11-17') == recorded


def test_sell_results_ignores_books_whose_sales_do_not_match_the_orders():
    filled = [{'종목코드': 'A', '구분': '현금매수', '체결수량': 10, '평균체결가': 1000, '주문번호': 1},
              {'종목코드': 'A', '구분': '현금매도', '체결수량': 10, '평균체결가': 1100, '주문번호': 2}]
    split = {'A': {'sales': [('2026-11-17T10:00', 400.0, 4000.0), ('2026-11-17T10:00', 600.0, 6000.0)]}}  # partial-fill rows
    assert app.sell_results(filled, split, '2026-11-17') == {2: (1000.0, 10.0)}
    one = {'A': {'sales': [('2026-11-17T10:00', 900.0, 9000.0)]}}
    assert app.sell_results(filled, one, '2026-11-17') == {2: (900.0, 10.0)}


def _longterm_live_chart_flow():
    import app
    app.paper_trade_chart(None, None, False, False, fills=[], key='live_lt', engine='longterm')


def test_long_term_live_account_chart_has_no_engine_ledger_and_still_draws(tmp_path):
    # 11-17: live-2 became a long-term account; its engine has no Jev ledger folder (ledger_root None)
    from unittest.mock import patch
    with patch.object(app, 'get_runtime_root', return_value=tmp_path), \
         patch.object(app, 'movers_snapshot', return_value={}), \
         patch.object(app, 'watchlist_snapshot', return_value={'selected': []}), \
         patch.object(app, 'period_bars', return_value=[]), \
         patch.object(app, 'investor_panel'):
        ui = AppTest.from_function(_longterm_live_chart_flow).run()
    assert not ui.exception
    assert any('엔진 원장 없음' in c.value for c in ui.caption)
    assert not ui.warning  # nothing to read is not a failure to read


def test_main_script_has_no_bare_expressions_streamlit_magic_would_print():
    # app.py is the Streamlit main script: a bare `st.dataframe(..) if x else st.caption(..)` is passed to st.write
    # and prints the DeltaGenerator help on the page (11-17, live account desk)
    import ast
    from pathlib import Path
    tree = ast.parse(Path(app.__file__).read_text(encoding='utf-8'))
    bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Expr)
            and not isinstance(n.value, (ast.Call, ast.Constant, ast.Yield, ast.YieldFrom, ast.Await))]
    assert bare == []


def _shared_root(tmp_path):
    shared = tmp_path / 'nhplug' / 'shared-etf-paper'
    shared.mkdir(parents=True)
    return shared


def test_take_profit_lines_follow_the_positions_own_plan(tmp_path):
    # D1 (11-17 review): the chart and position bars showed the settings' +1%/+2% for every holding, while the runner
    # sells by the plan saved at entry: a trend or new-listing plan has no fixed full target, 선별 v2 is +8%/+15%
    import json
    shared = _shared_root(tmp_path)
    (shared / 'exit-plans.json').write_text(json.dumps({
        '114800': {'profile': 'trend', 'stop_pct': 2.0, 'half_pct': 1.0, 'all_pct': 2.0, 'trend_half_pct': 2.0, 'trend_trail_pct': 1.5},
        '005930': {'profile': 'oversold', 'stop_pct': 2.0, 'half_pct': 8.0, 'all_pct': 15.0}}))
    with patch.object(app, 'get_runtime_root', return_value=tmp_path), \
         patch('engine_roles.ledger_root', return_value=shared):
        trend = app.with_levels({'price': 1000.0, 'start': 0}, '114800', 'jev')
        v2 = app.with_levels({'price': 1000.0, 'start': 0}, '005930', 'jev')
        plain = app.with_levels({'price': 1000.0, 'start': 0}, '069500', 'jev')  # no saved plan: the settings' defensive plan
    assert (round(trend['half']), trend['all']) == (1020, None)
    assert (round(v2['half']), round(v2['all'])) == (1080, 1150)
    defaults = app.STRATEGY_DEFAULTS
    assert (round(plain['half']), round(plain['all'])) == (round(1000 * (1 + defaults['take_profit_half_pct'] / 100)),
                                                           round(1000 * (1 + defaults['take_profit_all_pct'] / 100)))
    assert app.with_levels(None, '069500', 'jev') is None


def test_paper_requests_keep_letter_codes_across_clicks(tmp_path):
    # D2/D6: the runner accepts KRX codes with a letter (0183J0); the screen dropped them and the next click rewrote the
    # request files without them
    import json
    import nh_paper_isolated as runner
    shared = _shared_root(tmp_path)
    (shared / 'exit-requests.json').write_text(json.dumps({'0183J0': 3}))
    (shared / 'manual-buys.json').write_text(json.dumps({'0183J0': 2}))
    with patch.object(app, 'get_runtime_root', return_value=tmp_path):
        assert app.paper_requests(app.EXIT_REQUESTS) == {'0183J0': 3}
        assert app.paper_requests(app.MANUAL_BUYS) == {'0183J0': 2}
        app.request_paper(app.EXIT_REQUESTS, '069500', None)  # a click on another holding
        app.request_paper(app.MANUAL_BUYS, '069500', 1)
    assert runner.exit_requests(shared, {'0183J0': 5, '069500': 5}) == {'0183J0': 3, '069500': None}
    assert runner.manual_buys(shared) == {'0183J0': 2, '069500': 1}


def test_stock_names_cover_etfs_scanner_and_watchlist(tmp_path):
    # D8: the name map was rebuilt in ten places and missed the scanner (movers.json) names
    import json
    shared = _shared_root(tmp_path)
    (shared / 'movers.json').write_text(json.dumps({'rows': {'000660': {'name': 'SK하이닉스'}}}))
    (shared / 'watchlist.json').write_text(json.dumps({'stocks': {'005930': {'name': '삼성전자'}}}))
    with patch.object(app, 'get_runtime_root', return_value=tmp_path):
        names = app.stock_names()
    assert names['000660'] == 'SK하이닉스' and names['005930'] == '삼성전자'
    assert names[app.ETF_CODES['K200']] == app.ETF_NAMES['K200']


def test_display_quotes_run_together_but_start_at_most_five_a_second():
    # P1: the sidebar's quotes ran one after another (~0.27 s each, every rerun); in parallel they must still respect
    # NH's 5 calls/s per app key, which the runners share
    import time

    class Clocked:
        server_name = 'pace-test'

        def __init__(self):
            self.starts = []

        def call_tool(self, tool, args):
            self.starts.append(time.monotonic())
            return {'status': 'ok', 'data': {'Output_0': {'iem_cd': args['stock_code']}}}

    read, codes = Clocked(), [f'90000{i}' for i in range(7)]
    quotes = app.display_quotes(read, codes, 30)
    assert quotes == {code: {'iem_cd': code} for code in codes}
    starts = sorted(read.starts)
    assert all(later - first >= 0.99 for first, later in zip(starts, starts[5:]))
    assert app.display_quotes(read, codes, 30) == quotes and len(read.starts) == 7  # within the ttl: no new calls


def test_display_prices_ask_kiwoom_once_and_nh_only_for_what_it_missed(monkeypatch):
    # 2026-11-20 NH call plan P1: the sidebar quotes (every session, every refresh) were NH's second-largest display load
    import kiwoom_data
    asked, nh = [], []
    answer = [{'stk_cd': '069500_AL', 'cur_prc': '-50120', 'base_pric': '51000'}, {'stk_cd': '005930', 'cur_prc': '+70100', 'base_pric': '70000'}]
    monkeypatch.setattr(kiwoom_data, 'call', lambda api, body, key, max_pages=1: asked.append(body['stk_cd']) or answer)
    monkeypatch.setattr(app, 'display_quotes', lambda read, codes, ttl: nh.extend(codes) or {c: {'iem_cd': c, 'stck_prpr': 9, 'stck_prdy_clpr': 9} for c in codes})
    app._read_cache().clear()
    quotes = app.display_prices(None, ['069500', '005930', '114800'], 30)
    assert quotes['069500'] == ({'iem_cd': '069500', 'stck_prpr': 50120.0, 'stck_prdy_clpr': 51000.0, 'stck_oprc': 0,
                                  'stck_hgpr': 0, 'acml_vol': 0}, 'kiwoom')
    assert quotes['114800'][1] == 'nh' and nh == ['114800'] and asked == ['069500|005930|114800']
    app.display_prices(None, ['069500', '005930', '114800'], 30)
    assert len(asked) == 1  # within the ttl: Kiwoom is not asked again
    monkeypatch.setattr(kiwoom_data, 'call', lambda *a, **k: (_ for _ in ()).throw(OSError('down')))
    app._read_cache().clear()
    assert {s for _, s in app.display_prices(None, ['069500'], 30).values()} == {'nh'}  # Kiwoom down: NH as before
    app._read_cache().clear()


def test_a_long_term_account_charts_its_own_etfs_not_jev_trading_symbols():
    # user 2026-11-18: 실계좌 2(장기투자) showed the inverse and Jev's watchlist; it trades the long-term ETFs only
    import app, vibe_longterm as L
    from unittest.mock import patch
    known = {'114800': 'KODEX 인버스', '005930': '삼성전자'}
    with patch.object(app, 'watchlist_snapshot', return_value={'selected': ['005930']}):
        longterm = app.chart_universe('longterm', [{'종목코드': '379810', '종목': 'KODEX 미국나스닥100'}], known)
        jev = app.chart_universe('jev', [], known)
    codes = {code for _, code in longterm.values()}
    assert codes == {a[0] for a in L.ASSETS.values()} | {'0261H0', '438080'}  # + the 여운봉 매매법 mixed ETFs (2026-11-18)
    assert '114800' in {code for _, code in jev.values()} and '005930' in {code for _, code in jev.values()}
    assert all(name.startswith(L.asset_name(k)) for name, (k, _) in longterm.items())


def test_a_kept_broker_snapshot_is_fetched_again_before_the_chart_calls_it_stale():
    # 11-18: with auto refresh off the paper desk kept its first snapshot and the chart warned after 2 minutes
    import app
    from datetime import timedelta
    from unittest.mock import patch
    now = datetime(2026, 11, 18, 9, 30, tzinfo=app.KST)
    assert app.snapshot_due(None, now)
    for refresh in (30, 600):
        with patch.object(app, 'refresh_s', return_value=refresh):
            assert app.snapshot_due({'time': now - timedelta(seconds=min(refresh, app.SNAPSHOT_MAX_AGE) + 1)}, now)
            assert not app.snapshot_due({'time': now - timedelta(seconds=10)}, now)


def test_dashboard_reads_share_running_requests_without_blocking_other_reads():
    started, release = Event(), Event()

    def slow_broker_read():
        started.set()
        return release.wait(5)

    slow = app.start_dashboard_read(('test', 'broker'), slow_broker_read)
    try:
        assert started.wait(2)
        duplicate = app.start_dashboard_read(('test', 'broker'), lambda: 'duplicate')
        states = app.start_dashboard_read(('test', 'states'), lambda: '확인됨')
        assert duplicate is slow
        assert states.result(timeout=2) == '확인됨'
        assert not slow.done()
    finally:
        release.set()
        assert slow.result(timeout=2) is True


def test_account_settings_rerun_alone_so_the_slider_follows_the_box_at_once():
    # 11-18: +/- and typing reran the whole page (balances, quotes, sidebar), so the slider lagged and quick clicks bounced
    from streamlit.testing.v1 import AppTest

    def view():
        import streamlit as st
        import app
        st.session_state['outer_runs'] = st.session_state.get('outer_runs', 0) + 1
        rec = {'kind': 'live', 'engine': 'jev', 'auto': True, 'rules': {},
               'budget': {'capital_krw': 1_500_000, 'order_price_max_krw': 300_000, 'daily_loss_krw': 20_000}}
        app.account_form('live-f', rec, {'live-f': rec}, prefix='frag_')
    import app
    at = AppTest.from_function(view, default_timeout=60).run()
    box = next(n for n in at.number_input if 'order_price_max' in (n.key or ''))
    box.increment().run()
    slider = next(s for s in at.slider if 'order_price_max' in (s.key or ''))
    assert (box.value, slider.value) == (31, 31) and not at.exception
    # AppTest reruns the whole script even for a fragment; the browser reruns only the fragment (checked on 8599, 11-18)
    assert 'streamlit' in app.account_form.__code__.co_filename and app.account_form.__wrapped__.__name__ == 'account_form'


def test_the_box_callback_never_writes_back_a_value_it_kept():
    # 11-18 (8599, 4 quick + clicks): the box callback wrote its own key back every time, so the server value replaced
    # the clicks still on their way and the amount bounced back (43 → 44 → 43). Only a value brought into range is written.
    import app
    from unittest.mock import MagicMock, patch
    state = {'f_n': 44, 'f_s': 43}

    class Spy(dict):
        writes = []
        def __setitem__(self, k, v):
            Spy.writes.append(k)
            super().__setitem__(k, v)
    spy = Spy(state)
    callbacks = {}
    box = MagicMock(side_effect=lambda *a, **k: callbacks.setdefault('box', k['on_change']) and 44)
    container = MagicMock(number_input=box, slider=MagicMock(side_effect=lambda *a, **k: callbacks.setdefault('bar', k['on_change'])))
    with patch.object(app.st, 'session_state', spy):
        app.synced_number(container, '가격', 1, 200, 43, 'f', True)
        Spy.writes.clear()
        callbacks['box']()
        assert 'f_n' not in Spy.writes and spy['f_s'] == 44
        spy['f_n'] = 500
        callbacks['box']()
        assert spy['f_n'] == 200 == spy['f_s']  # out of range: brought into range and written


def test_the_desk_shows_a_realized_card_for_today_this_month_and_since_records_start():
    # user 2026-11-18: the account desk had 평가손익 but no realized P&L
    import app
    from datetime import date
    books = {'114800': {'sales': [('2026-11-18T10:00:00+09:00', 1_000, 100_000), ('2026-11-03T10:00:00+09:00', -400, 50_000)]},
             '069500': {'sales': [('2026-10-30T10:00:00+09:00', 250, 10_000)]}}
    card = app.realized_card(books, date(2026, 11, 18))
    assert card['label'] == '실현손익' and card['value'] == '+1,000원' and card['tone'] == 'up'
    assert card['sub'] == '이번 달 +600원 · 누적 +850원'
    assert app.realized_card(None, date(2026, 11, 18))['value'] == '확인 불가'


def _oversold_views():
    import os
    from pathlib import Path
    import app
    from strategy_settings import DEFAULTS
    root = Path(os.environ['OVERSOLD_TEST_ROOT'])
    app.oversold_algorithm_view(root, dict(DEFAULTS))
    app.oversold_watch_view(root)


def test_oversold_views_draw_when_a_recorded_number_is_unknown(tmp_path, monkeypatch):
    # 11-18 14:30:48: the 선별 v2 gate journaled kospi.low_pct null (quote unknown) and 전략 구조 stopped there (TypeError)
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d')
    at = lambda hhmm: f'{today}T{hhmm}:00+09:00'
    events = [{'time': at('14:30'), 'event': 'oversold_v2_gate', 'passed': False, 'kospi': {'low_pct': None}, 'kosdaq': {'low_pct': -1.3}},
              {'time': at('14:31'), 'event': 'oversold_v2_signal', 'symbol': '005930', 'drop_pct': None, 'market_low_pct': None,
               'engine': 'jev', 'choice': 'BUY', 'confidence': None},
              {'time': at('14:32'), 'event': 'oversold_scan', 'symbol': '005930', 'drop_pct': None, 'bounce_pct': None},
              {'time': at('14:33'), 'event': 'oversold_signal', 'symbol': '005930', 'engine': 'jev', 'choice': 'BUY', 'confidence': None}]
    (tmp_path / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))
    monkeypatch.setenv('OVERSOLD_TEST_ROOT', str(tmp_path))
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'home'))
    with patch('guide_ui.flow'):
        ui = AppTest.from_function(_oversold_views, default_timeout=60).run()
    assert not ui.exception, [e.value for e in ui.exception]
    gate = next(m for m in ui.metric if m.label == '오늘 시장 판정')
    assert gate.value == '통과 못 함' and gate.delta == '코스닥 -1.3%'  # the unknown market is left out, not printed as 0


def test_the_order_tab_names_a_long_term_account_by_its_engine():
    records = {'paper-shared': {'label': '모의계좌', 'engine': 'jev'}, 'live-2': {'label': '실계좌 2', 'engine': 'longterm'}}
    shown = {}
    fake = lambda label, keys, default=None, key=None, format_func=str: shown.update({k: format_func(k) for k in keys}) or default
    with patch.object(app.st, 'segmented_control', side_effect=fake):
        app.account_scope(records, 'scope')
    assert shown == {'paper-shared': '모의계좌 · Jev 트레이딩', 'live-2': '실계좌 2 · 장기투자'}


def test_oversold_views_draw_before_the_runner_writes_its_journal(tmp_path, monkeypatch):
    # review4 D-11: 전략 구조 raised FileNotFoundError when events.jsonl did not exist yet (a new or restored runtime)
    monkeypatch.setenv('OVERSOLD_TEST_ROOT', str(tmp_path))
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'home'))
    with patch('guide_ui.flow'):
        ui = AppTest.from_function(_oversold_views, default_timeout=60).run()
    assert not ui.exception, [e.value for e in ui.exception]
    assert next(m for m in ui.metric if m.label == '오늘 시장 판정').value == '판정 전'


def test_nh_quotes_and_balances_refresh_only_while_krx_trades(monkeypatch):
    # review4 D-1: with auto refresh on the dashboard polled NH quotes and balances every 30 s through evenings and
    # holidays and was NH's top caller beside the runners' 429s
    from datetime import date
    import nh_paper_isolated
    monkeypatch.setattr(nh_paper_isolated, '_HOLIDAYS', {2026: (frozenset({date(2026, 12, 31)}), 0.0)})
    monkeypatch.setattr(app, 'refresh_s', lambda: 30)
    monkeypatch.setattr(app, 'auto_on', lambda: True)
    at = lambda *a: datetime(*a, tzinfo=app.KST)  # noqa: E731
    assert app.live_every(at(2026, 11, 19, 9, 0)) == '30s' and app.live_every(at(2026, 11, 19, 15, 29)) == '30s'
    assert app.live_every(at(2026, 11, 19, 8, 59)) is None and app.live_every(at(2026, 11, 19, 15, 30)) is None
    assert app.live_every(at(2026, 11, 19, 21, 0)) is None  # evening
    assert app.live_every(at(2026, 11, 21, 10, 0)) is None  # Saturday
    assert app.live_every(at(2026, 12, 31, 10, 0)) is None  # KRX year-end holiday (a Thursday)
    monkeypatch.setattr(app, 'auto_on', lambda: False)
    assert app.live_every(at(2026, 11, 19, 10, 0)) is None  # the header switch is off


def _new_listing_page():
    from datetime import datetime
    import app
    app.new_listing_panel(datetime(2026, 11, 19, 10, 0, tzinfo=app.KST))


def test_the_new_listing_panel_reads_the_runners_list_and_never_fetches_it(tmp_path, monkeypatch):
    # review4 D-10: the 30-second fragment asked KRX KIND (then Toss) itself and wrote the runner's cache file
    from unittest.mock import MagicMock
    import requests
    root = tmp_path / 'nhplug' / 'shared-etf-paper'
    root.mkdir(parents=True)
    fetch = MagicMock(side_effect=OSError('network'))
    monkeypatch.setattr(requests, 'Session', fetch)
    monkeypatch.setattr(app, 'get_runtime_root', lambda: tmp_path)
    monkeypatch.setattr(app, 'display_quotes', lambda read, codes, ttl: {code: None for code in codes})
    with patch('guide_ui.flow'):
        ui = AppTest.from_function(_new_listing_page, default_timeout=60).run()
    assert not ui.exception and not fetch.called and not (root / 'new-listings.json').exists()
    (root / 'new-listings.json').write_text(json.dumps({'day': '20261119', 'checked': '2026-11-19T08:00:00+09:00', 'ok': True,
                                                        'rows': [{'name': '넥스아이', 'code': '493410', 'listed': '20261112'}]}))
    with patch('guide_ui.flow'):
        ui = AppTest.from_function(_new_listing_page, default_timeout=60).run()
    assert not ui.exception and not fetch.called
    assert list(ui.dataframe[0].value['종목']) == ['넥스아이']


def test_fill_rows_put_each_stock_on_its_own_line_by_trading_day_and_minute():
    """2026-11-19: prices of a 1천 원 ETF and a 20만 원 stock on one price axis hid the cheap one, and nights and weekends
    took most of the time axis; the chart is now stock × trading day with the minute in the session inside each day."""
    fills = [{'시각': '2026-11-13T09:31:00+09:00', '종목': '069500', '구분': 'BUY', '수량': 10, '체결가': 10_000},
             {'시각': '2026-11-13T15:10:00+09:00', '종목': '069500', '구분': 'SELL', '수량': 10, '체결가': 10_100},
             {'시각': '2026-11-16T10:00:00+09:00', '종목': '005930', '구분': 'BUY', '수량': 1, '체결가': 200_000},
             {'시각': 'bad', '종목': '005930', '구분': 'BUY', '수량': 1, '체결가': 1}]
    rows, days = app.fill_rows(fills, {'069500': 'KODEX 200'})
    assert [r['날짜'] for r in rows] == ['11/13(금)', '11/13(금)', '11/16(월)'] and days == ['11/13(금)', '11/16(월)']
    assert [r['장중 분'] for r in rows] == [31, 370, 60] and rows[0]['종목'] == 'KODEX 200' and rows[2]['종목'] == '005930'
    assert [r['금액'] for r in rows] == [100_000, 101_000, 200_000] and rows[1]['구분'] == '매도'


def test_shared_snapshot_serves_other_sessions_until_a_refresh():
    app._read_cache().pop(('broker-snapshot', 'live', 'acct-x'), None)
    calls = []
    fetch = lambda: calls.append(1) or {'n': len(calls)}
    assert app.shared_snapshot('live', 'acct-x', fetch) == {'n': 1}
    assert app.shared_snapshot('live', 'acct-x', fetch) == {'n': 1}  # a second tab within the window reuses it
    assert app.shared_snapshot('live', 'acct-x', fetch, fresh=True) == {'n': 2}  # the refresh button reads again
    with patch.object(app.time, 'monotonic', return_value=app.time.monotonic() + app.SHARED_SNAPSHOT_S + 1):
        assert app.shared_snapshot('live', 'acct-x', fetch) == {'n': 3}


def test_listing_quotes_take_prices_from_kiwoom_and_only_listing_fields_from_nh(monkeypatch):
    # 2026-11-20 NH call audit: the new-listing panel read every listing's NH quote once a minute in every tab
    import kiwoom_data
    answer = [{'stk_cd': '471800', 'cur_prc': '9180', 'base_pric': '9000', 'open_pric': '9050', 'high_pric': '9300', 'trde_qty': '1200'}]
    monkeypatch.setattr(kiwoom_data, 'call', lambda *a, **k: answer)
    ttls = []
    nh = {'iem_cd': '471800', 'iem_nm': '에이엘로봇', 'stck_prpr': 1, 'stck_lstn_date': '20261118', 'lstn_stcn_unit0': 5000}
    monkeypatch.setattr(app, 'display_quotes', lambda read, codes, ttl: ttls.append(ttl) or {c: dict(nh) for c in codes})
    app._read_cache().clear()
    q = app.listing_quotes(None, ['471800'])['471800']
    assert (q['stck_prpr'], q['stck_hgpr'], q['acml_vol'], q['stck_lstn_date'], q['lstn_stcn_unit0']) == (9180, 9300, 1200, '20261118', 5000)
    assert ttls == [app.LISTING_FIELDS_S]  # NH only for the day-stable fields, kept for hours
    app._read_cache().clear()


def test_minute_charts_come_from_kiwoom_shifted_to_nh_stamps_and_fall_back_to_nh(monkeypatch):
    # user 2026-11-20 '차트만 바꿔줘': display charts only; Kiwoom stamps a bar by its first minute, NH by its last
    import kiwoom_data
    rows = [{'cntr_tm': '20261120130000', 'open_pric': '+100', 'high_pric': '+102', 'low_pric': '-99', 'cur_prc': '+101', 'trde_qty': '7'},
            {'cntr_tm': '20261120080500', 'open_pric': '100', 'high_pric': '100', 'low_pric': '100', 'cur_prc': '100', 'trde_qty': '1'}]  # NXT
    monkeypatch.setattr(kiwoom_data, 'call', lambda *a, **k: rows)
    app._read_cache().clear()
    bars = app.period_bars(None, '069500', '1분')
    assert len(bars) == 1 and bars[0]['close'] == 101 and bars[0]['volume'] == 7
    assert datetime.fromtimestamp(bars[0]['time'], app.KST).strftime('%H:%M') == '13:01'
    monkeypatch.setattr(kiwoom_data, 'call', lambda *a, **k: (_ for _ in ()).throw(OSError('down')))
    app._read_cache().clear()
    asked = []
    monkeypatch.setattr(app, 'cached_tool', lambda read, tool, args, ttl=0: asked.append(args['operationId']) or {'status': 'error'})
    with pytest.raises(ValueError):
        app.period_bars(None, '069500', '1분')
    assert asked == ['krstockQuotePeriod']  # Kiwoom down: NH as before
    app._read_cache().clear()


def test_block_rows_show_the_last_cycle_and_why_each_stock_waited():
    # user 2026-11-20 '대시보드에서 차단 사유도 보이게 해줘'
    day = datetime(2026, 11, 20, tzinfo=app.KST).date()
    events = [
        {'time': '2026-11-20T13:35:40+09:00', 'event': 'v2_entry_gate', 'choice': 'INV', 'permitted': False,
         'reason': 'inverse_low_confidence', 'cycle_id': 'a'},
        {'time': '2026-11-20T13:36:17+09:00', 'event': 'v2_entry_gate', 'choice': 'WAIT', 'permitted': False, 'reason': 'jev_wait',
         'strength_reasons': {'005930': 'strength_change_out_of_range', '180640': 'trend_not_rising'}, 'ipo_reason': 'below_open', 'cycle_id': 'a'},
        {'time': '2026-11-20T13:37:50+09:00', 'event': 'v2_entry_gate', 'choice': 'WAIT', 'permitted': False, 'reason': 'jev_wait',
         'strength_reasons': {'180640': 'pick_cooldown'}, 'cycle_id': 'b'},
        {'time': '2026-11-20T13:38:00+09:00', 'event': 'v2_entry_gate', 'choice': 'INV', 'permitted': True, 'cycle_id': 'c'}]
    latest, stocks = app.block_rows(events, day)
    assert [s.get('reason') for s in latest['steps']] == ['jev_wait']  # cycle b
    hanjin = next(r for r in stocks if r['code'] == '180640')
    assert (hanjin['count'], hanjin['last_why'], hanjin['reasons']) == (2, 'pick_cooldown', {'trend_not_rising': 1, 'pick_cooldown': 1})
    assert next(r for r in stocks if r['code'] == 'INV')['last_why'] == 'inverse_low_confidence'
    assert app.entry_reason_label('trend_not_rising') == '최근 분봉 상승 아님'


def test_a_wait_after_a_refused_pick_reads_as_the_hand_off():  # user 2026-11-20 '인버스 막힌 회차 표기도 고쳐줘'
    steps = [{'choice': 'INV', 'reason': 'inverse_too_late'}, {'choice': 'WAIT', 'reason': 'jev_wait'}]
    names = {'INV': 'KODEX 인버스'}
    assert app.step_line(steps[0], steps, names) == '**Jev 선택 KODEX 인버스** → 인버스: 지수가 이미 많이 빠짐'
    assert app.step_line(steps[1], steps, names) == '**KODEX 인버스 막힘** → 강도·신규주 경로로 넘김'
    alone = [{'choice': 'WAIT', 'reason': 'jev_wait'}]
    assert app.step_line(alone[0], alone, names) == '**Jev 대기** → Jev 대기 의견'
