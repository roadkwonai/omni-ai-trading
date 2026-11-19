import longterm_dca as D
import longterm_dca_effective as E
import vibe_longterm as L

WANT = {'NDX': 0.30, 'SPX': 0.30, 'SEMI': 0.10, 'QUANT': 0.08, 'SPACE': 0.07, 'RES': 0.15}
PRICES = {'NDX': 24_000, 'SPX': 22_000, 'SEMI': 34_000, 'QUANT': 15_500, 'SPACE': 6_000, 'RES': 112_000}


def test_build_tune_keeps_only_values_that_differ_from_the_master():
    # v3 (2026-11-18): rate / pace / floor / wait and the multipliers by regime
    base = D.rule({**D.DEFAULT, 'profile': 'marks'}, 'correction')
    same = E.build_tune(base, {'pace': base['pace'] * 100, 'wait': base['wait']}, {r: base['mult'][r] * 100 for r in D.REGIMES})
    assert same == {}
    mine = E.build_tune(base, {'pace': 50, 'wait': 3, 'rate': 30, 'floor': 25}, {**{r: base['mult'][r] * 100 for r in D.REGIMES}, 'bear': 150})
    assert mine == {'pace': 0.5, 'wait': 3, 'rate': 0.003, 'floor': 0.25, 'mult': {'bear': 1.5}}
    assert D.clean({'when_by': 'master', 'tune': mine})['tune'] == mine


def test_summary_renders_who_decides_each_part_and_the_example(tmp_path):
    from streamlit.testing.v1 import AppTest

    def view():
        import longterm_dca as D
        import longterm_dca_effective as E
        cfg = D.clean({'on': True, 'profile': 'marks', 'when_by': 'master', 'size_by': 'master', 'tune': {'mult': {'correction': 1.5}}})
        E.summary(cfg, 'correction', {'day_ret': -0.01}, {'NDX': 0.3, 'SPX': 0.3, 'SEMI': 0.1, 'QUANT': 0.08, 'SPACE': 0.07},
                  ({'NDX': 24_000, 'SPX': 22_000, 'SEMI': 34_000, 'QUANT': 15_500, 'SPACE': 6_000}, '엔진 11-18 계획'),
                  {}, ['bogle_core', 'dalio_rebalance'], live=None)
    at = AppTest.from_function(view).run()
    assert not at.exception
    text = ' '.join(m.value for m in at.markdown)
    assert '언제 · 자동 · 거장' in text and '얼마나 · 자동 · 거장' in text and '내 조정값' in text and '반영 안 됨' in text
    assert any('이번 회차 예시' in m.value for m in at.markdown) and at.dataframe


def test_tune_editor_prefills_the_master_values_and_resets():
    from streamlit.testing.v1 import AppTest

    def view():
        import streamlit as st
        import longterm_dca as D
        import longterm_dca_effective as E
        st.session_state['tune_out'] = E.tune_editor('t', D.clean({'profile': 'marks', 'when_by': 'master', 'tune': {'wait': 3}}), 'correction')
    at = AppTest.from_function(view).run()
    assert not at.exception and at.toggle(key='t_on').value is True  # a saved tune opens the editor
    assert at.number_input(key='t_wait').value == 3 and at.session_state['tune_out'] == {'wait': 3}
    at.button(key='t_reset_all').click().run()
    assert at.number_input(key='t_wait').value == D.PROFILES['marks']['wait'] and at.session_state['tune_out'] == {}
    at.toggle(key='t_on').set_value(False).run()
    assert at.session_state['tune_out'] == {}



def test_tune_editor_keeps_a_saved_value_its_book_does_not_show_and_shows_pace_on_a_yeo_book():
    from streamlit.testing.v1 import AppTest

    def view(yeo):
        import streamlit as st
        import longterm_dca as D
        import longterm_dca_effective as E
        st.session_state['tune_out'] = E.tune_editor('t', D.clean({'profile': 'yeo', 'when_by': 'master', 'size_by': 'master',
                                                                    'tune': {'rate': 0.004, 'pace': 0.5}}), 'correction', yeo=yeo)
    at = AppTest.from_function(view, args=(False,)).run()  # a module book shows pace only: the 여운봉 rate stays saved
    assert not at.exception and at.session_state['tune_out'] == {'rate': 0.004, 'pace': 0.5}
    at = AppTest.from_function(view, args=(True,)).run()  # a 여운봉 book's size reads the pace too
    assert at.number_input(key='t_pace').value == 50 and at.session_state['tune_out'] == {'rate': 0.004, 'pace': 0.5}

def test_a_live_accounts_example_uses_its_own_strategy_target(tmp_path, monkeypatch):
    from datetime import datetime
    from test_vibe_longterm import DAY, KST, fake_fetch, ANSWER, krx_calendar  # noqa: F401
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr(L, '_HOLIDAYS', {y: frozenset() for y in range(2024, 2031)}, raising=False)
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda t: ANSWER)
    L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom', 'modules': {'yeo_reservoir': True}}}})
    monkeypatch.setattr('longterm_live.load_state', lambda root, key: {})
    _, _, want, _ = E.example_inputs(tmp_path, 'live-2')
    assert want == {}  # 저수지 분할만 (기준 비중 없음): the strategy buys nothing now, so neither does its 적립식 (v2, no KOFR)
    _, _, engine, _ = E.example_inputs(tmp_path, 'actual')
    assert engine['NDX'] > 0 and 'RES' not in engine
