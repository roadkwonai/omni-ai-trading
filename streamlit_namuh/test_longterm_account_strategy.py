import longterm_account_view as V
import vibe_longterm as L
from test_vibe_longterm import ind


def test_compare_shows_the_accounts_target_beside_the_virtual_books():
    story = {k: {'status': 'watch', 'tilt': 0, 'reason': ''} for k in L.SATS}
    plan = {'indicators': ind(), 'regime': 'bull', 'active': ['bogle_core', 'lynch_story'], 'weights': None, 'day': '2026-11-18'}
    rows = V.target_compare(plan, story, {}, ['bogle_core', 'yeo_reservoir'])
    quant = next(r for r in rows if r['종목'] == L.ASSETS['QUANT'][1])
    assert quant['가상 계좌(엔진)'] == '4.0%' and quant['이 계좌'] == '8.0%' and quant['차이'] == '+4.0%p'
    assert len(rows) == len(L.ASSETS)


def test_the_panel_switches_between_the_engine_and_its_own_set_and_keeps_the_set(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))

    def view(root):
        import longterm_account_view
        longterm_account_view.strategy_panel('live-2', 'p_', root)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception and at.radio(key='p_lt_book_strategy_live-2').value == 'engine'
    at.radio(key='p_lt_book_strategy_live-2').set_value('custom').run()
    engine = L.active_modules(L.load_settings(tmp_path), [])
    assert all(at.toggle(key=f'p_lt_book_mod_live-2_{m}').value == (m in engine) for m in L.MODULES)  # starts from the engine
    at.selectbox(key='p_lt_book_preset_live-2').set_value('yeo_only').run()
    assert at.toggle(key='p_lt_book_mod_live-2_yeo_reservoir').value and not at.toggle(key='p_lt_book_mod_live-2_bogle_core').value
    assert any('기준 비중 모듈(강력 추천)가 꺼져' in w.value for w in at.warning)
    assert all(at.toggle(key=f'p_lt_book_mod_live-2_{m}').help for m in L.MODULES)
    assert any('강력 추천' in m.value for m in at.markdown)
    at.button(key='p_lt_book_save_live-2').click().run()
    assert L.book_strategy_label(L.load_settings(tmp_path), 'live-2') == '저수지 분할(일반)만 (기준 비중 없음)'
    at.radio(key='p_lt_book_strategy_live-2').set_value('engine').run()
    at.button(key='p_lt_book_save_live-2').click().run()
    saved = L.load_settings(tmp_path)['books']['live-2']
    assert saved['strategy'] == 'engine' and saved['modules']['yeo_reservoir'] is True  # the chosen set is kept


def test_the_recommendation_fill_only_prefills(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))

    def view():
        import longterm_account_view
        longterm_account_view.module_toggles('t', {'williams_rebound': True}, 'correction')
    at = AppTest.from_function(view).run()
    assert any('추천 밖인데 켜짐' in i.value for i in at.info)
    at.button(key='t_fill').click().run()
    assert all(at.toggle(key=f't_{m}').value == (m in L.recommended_set('correction')) for m in L.MODULES)


def test_the_engine_tab_fills_the_recommendation_without_saving_and_warns_without_bogle(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))

    def view(root):
        import vibe_longterm_view
        import vibe_longterm as L
        settings = L.load_settings(root)
        vibe_longterm_view.strategy(root, settings, {'regime': 'correction'}, L.recommended_set('correction'), [])
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception and any('강력 추천' in m.value for m in at.markdown)
    before = L.load_settings(tmp_path)['modules']
    at.button(key='lt_apply').click().run()
    assert all(at.toggle(key=f'lt_mod_{m}').value == (m in L.recommended_set('correction')) for m in L.MODULES)
    assert L.load_settings(tmp_path)['modules'] == before  # only prefilled
    at.toggle(key='lt_mod_bogle_core').set_value(False).run()
    assert any('기준 비중 모듈(강력 추천)가 꺼져' in w.value for w in at.warning)


def test_a_strategy_change_that_lowers_targets_lists_what_may_be_sold():
    """Review 2026-11-18: switching an invested account to 여운봉만 sells most of it the next day; the panel names it first."""
    plan = {'indicators': ind(), 'regime': 'bull', 'active': ['bogle_core', 'dalio_rebalance'], 'weights': None, 'day': '2026-11-18'}
    cuts = V.target_cuts(plan, {}, {}, ['bogle_core', 'dalio_rebalance'], ['yeo_reservoir'])
    assert {n for n, _, _ in cuts} == {a[1] for k, a in L.ASSETS.items() if k != 'RES'} and all(a < b for _, b, a in cuts)
    assert V.target_cuts(plan, {}, {}, ['bogle_core'], ['bogle_core', 'dalio_rebalance']) == []


def test_the_yeo_method_is_a_strategy_choice_with_its_settings_and_summary(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import longterm_account_view as A
    monkeypatch.setattr(A, 'nh_price', lambda code: {'379810': 23_870, '0261H0': 9_825}.get(code))
    monkeypatch.setattr('src.config.paths.get_runtime_root', lambda: tmp_path)

    def view(root):
        import longterm_account_view
        longterm_account_view.strategy_panel('live-2', 'p_', root, capital=500_000)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.radio(key='p_lt_book_strategy_live-2').set_value('yeo_method').run()
    assert not at.exception
    text = ' '.join(m.value for m in at.markdown) + ' '.join(c.value for c in at.caption)
    assert '저수지' in text and '보유가 생긴 뒤' in text
    risk, safe = L.asset_name('NDX'), L.asset_name('NDXMIX')  # the columns name the ETFs (user 2026-11-18)
    table = next(d.value for d in at.dataframe if f'{risk} 주 수' in d.value.columns)
    assert list(table[f'{risk} 주 수'])[:1] == [5] and list(table[f'{safe} 주 수'])[:1] == [6]  # 500,000 × 40% as 70 : 30
    assert any('종목' in d.value.columns and '0261H0' in list(d.value['코드']) for d in at.dataframe)
    at.slider(key='p_yeo_live-2_risk').set_value(80).run()
    assert at.button(key='p_lt_book_save_live-2').disabled and any('팔고' in w.value for w in at.warning)  # review5: it rebalances what it holds
    at.checkbox(key='p_lt_book_yeo_ok_live-2').check().run()
    at.button(key='p_lt_book_save_live-2').click().run()
    saved = L.load_settings(tmp_path)['books']['live-2']
    assert saved['strategy'] == 'yeo_method' and saved['yeo']['risk'] == 0.8 and L.book_modules(L.load_settings(tmp_path), 'live-2', {}) == []


def test_the_virtual_book_can_run_the_yeo_method(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    import longterm_account_view as A
    monkeypatch.setattr(A, 'nh_price', lambda code: None)

    def view(root):
        import vibe_longterm_view
        import vibe_longterm as L
        vibe_longterm_view.strategy(root, L.load_settings(root), {'regime': 'correction'}, L.recommended_set('correction'), [])
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.radio(key='lt_actual_way').set_value('yeo_method').run()
    at.button(key='lt_save_way').click().run()
    assert not at.exception and L.yeo_settings(L.load_settings(tmp_path), 'actual') is not None


def test_the_account_strategy_has_no_numbers_of_its_own(tmp_path, monkeypatch):
    # S2 removed (user 2026-11-19): every account uses the engine's base weights and numbers
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))

    def view(root):
        import longterm_account_view
        longterm_account_view.strategy_panel('live-2', 'p_', root)
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception
    assert not any((w.key or '').startswith('p_lt_book_numbers') for w in at.radio)  # no per-account numbers (user 2026-11-19)
    assert not any('lt_book_w_' in (w.key or '') for w in at.number_input)


def test_the_yeo_method_lists_the_etfs_it_buys_with_their_names(tmp_path):
    # user 2026-11-18: "여운봉 저수지 매매법 선택했을 때 선정되는 종목명이 안 보임"
    import yeo_method as Y
    rows = V.yeo_instruments(Y.clean(None))
    assert [(r['역할'], r['코드']) for r in rows] == [('위험(성장)', '379810'), ('안전(채권 혼합)', '0261H0'), ('저수지(대기)', '449170')]
    assert rows[0]['종목'] == L.asset_name('NDX') and rows[1]['종목'] == 'KODEX 미국나스닥100미국채혼합50' and rows[0]['비율'] == '70%'
    both = V.yeo_instruments(Y.clean({'us_index': 'both'}))
    assert {r['코드'] for r in both} >= {'379810', '0261H0', '379800', '438080'}  # S&P500 pair too, each with its reservoir share


def test_the_yeo_methods_mixed_etf_shows_in_the_allocation_picks_and_charts():
    # user 2026-11-18: "0261H0이 매수 종목에 안 보임 — 여운봉 저수지 매매법의 핵심 종목"
    rows = V.allocation_comparison({'NDX': 0.7, 'NDXMIX': 0.3}, {'0261H0': {'평가금액': 98_000}},
                                  500_000, {}, '449170')
    mixed = next(row for row in rows if row['종목'] == '0261H0')
    assert mixed['평가금액'] == 98_000 and mixed['목표 비중'] == 30.0
    import app
    assert any('0261H0' in label for label in app.chart_universe('longterm', [], {}))
