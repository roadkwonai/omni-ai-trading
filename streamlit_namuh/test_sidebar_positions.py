from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import trading_positions


def _sidebar():
    import sidebar

    sidebar.positions_panel('/unused', {'005930': '삼성전자'})


def test_click_engine_position_opens_trade_history():
    data = {'jev': {'status': 'ok', 'holdings': [{'symbol': '005930', 'qty': 2}], 'stops': [], 'unknown_sells': 0},
            'vibe': {'status': 'ok', 'holdings': [], 'stops': [], 'unknown_sells': 0}}
    with patch.object(trading_positions, 'snapshot', return_value=data):
        app = AppTest.from_function(_sidebar).run()
        assert not app.exception
        app.button[0].click().run()
        assert not app.exception
        assert app.session_state['main_tab'] == 'AI·실행 기록'
        assert app.session_state['record_source'] == 'jev'
        assert app.session_state['trade_history'] == {'engine': 'jev', 'code': '005930'}


def test_repeated_stops_are_grouped_and_link_to_one_symbol():
    stops = [{'symbol': '005930', 'qty': 100, 'time': f'2026-11-16T{time}:00+09:00'}
             for time in ('11:25', '11:33', '11:29')]
    data = {'jev': {'status': 'ok', 'holdings': [{'symbol': '005930', 'qty': 17}], 'stops': stops},
            'vibe': {'status': 'ok', 'holdings': [], 'stops': []}}
    with patch.object(trading_positions, 'snapshot', return_value=data):
        app = AppTest.from_function(_sidebar).run()
        assert not app.exception
        assert len(app.button) == 1
        assert '보유 17주' in app.button[0].label
        assert '11:33 외 2건' in app.button[0].label
        app.button[0].click().run()
        assert app.session_state['trade_history'] == {'engine': 'jev', 'code': '005930'}


def test_frozen_vibe_book_is_not_listed():
    stop = {'symbol': '005930', 'qty': 1, 'time': '2026-11-16T11:33:00+09:00'}
    data = {engine: {'status': 'ok', 'holdings': [], 'stops': [stop]} for engine in ('jev', 'vibe')}
    with patch.object(trading_positions, 'snapshot', return_value=data):
        app = AppTest.from_function(_sidebar).run()
        assert [b.key for b in app.button] == ['side_position_jev_005930']  # vibe became the long-term engine (2026-11-17)
        assert any('장기투자' in m.value for m in app.markdown)


def test_profitable_sale_is_labeled_profit_and_opens_history():
    data = {'jev': {'status': 'ok', 'holdings': [], 'stops': [], 'sells': [
        {'symbol': '005930', 'qty': 3, 'time': '2026-11-16T11:33:00+09:00', 'outcome': '익절'}]},
        'vibe': {'status': 'ok', 'holdings': [], 'stops': [], 'sells': []}}
    with patch.object(trading_positions, 'snapshot', return_value=data):
        app = AppTest.from_function(_sidebar).run()
        assert not app.exception
        assert '오늘 익절 3주' in app.button[0].label
        app.button[0].click().run()
        assert app.session_state['trade_history'] == {'engine': 'jev', 'code': '005930'}


def test_long_term_side_lines_show_shadow_gap_and_story_warnings():
    import sidebar
    state = {'nav_latest': {'day': '2026-11-18', 'actual': 100_120_000, 'base': 100_000_000},
             'story': {'stories': {'QUANT': {'status': 'watch'}, 'SEMI': {'status': 'intact'}, 'SPACE': {'status': 'broken'}}}}
    assert sidebar.longterm_side_lines(state) == ['순자산 1.001억 · 기본 그림자 대비 +120,000원 (2026-11-18)',
                                                  '스토리 ⚠ 미국양자컴퓨팅TOP10 주의 · 미국우주테크 훼손']
    assert sidebar.longterm_side_lines({}) == []
    paid = {**state, 'nav_latest': {**state['nav_latest'], 'contributed': 1_000_000}}
    assert sidebar.longterm_side_lines(paid)[0] == '순자산 1.001억 · 적립 누계 100만 · 기본 그림자 대비 +120,000원 (2026-11-18)'


def _status(alerts):
    import sidebar
    sidebar.render(['종합 현황'], {'공유 모의 ETF 실행기': '종료'}, {}, alerts)


def test_a_stopped_runner_is_red_only_when_alerts_flag_it():
    # after hours 종료 is normal (gray); in the session alerts() names it and the line turns red
    import re
    color = lambda ui: re.search(r'background:(#[0-9a-f]{6})" aria-hidden="true"></span>모의 실행기',  # noqa: E731
                                 ''.join(m.value for m in ui.markdown)).group(1)
    assert color(AppTest.from_function(_status, args=([],)).run()) == '#64748b'
    assert color(AppTest.from_function(_status, args=(['공유 모의 ETF 실행기: 종료 (장중)'],)).run()) == '#ef4444'


def _longterm_panel(root):
    import sidebar
    sidebar.longterm_panel(root)


def test_the_long_term_panel_names_a_yeo_method_etf(tmp_path):
    # review4 D-2: a 여운봉 매매법 book's plan holds NDXMIX (0261H0); LT.ASSETS[key] raised KeyError in the sidebar
    import vibe_longterm as LT
    root = tmp_path / 'nhplug'
    LT.put_state(LT.connect(root / 'vibe-longterm'), plan={'day': '2026-11-20', 'regime': 'bear', 'levels': []},
                 last_plan={'day': '2026-11-20', 'want': {'RES': 0.55, 'NDX': 0.22, 'NDXMIX': 0.095, 'SPX': 0.075},
                            'weights': {'RES': 0.8, 'NDX': 0.07, 'SPX': 0.075}, 'why': 'yeo_step'})
    at = AppTest.from_function(_longterm_panel, args=(root,), default_timeout=30).run()
    assert not at.exception, at.exception[0].value
    assert any('KODEX 미국나스닥100미국채혼합50 · 현재 0% / 목표 10%' in c.value for c in at.caption)
