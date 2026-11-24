import longterm_dca as D
import longterm_dca_view as V


def test_books_are_the_virtual_book_and_long_term_live_accounts():
    records = {'paper-shared': {'kind': 'paper', 'engine': 'jev'}, 'live-1': {'kind': 'live', 'engine': 'jev'},
               'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}}
    assert [k for k, _ in V.books(records)] == ['actual', 'live-2']


def test_schedule_text_reads_like_the_setting():
    cfg = D.clean({'on': True, 'mode': 'yeo', 'amount_krw': 500_000, 'frequency': 'weekly', 'weekday': 2, 'time': '10:30'})
    assert V.schedule_text(cfg) == '매주 수요일 · 하락일 우선(정한 날 뒤 첫 하락일) · 10:30 · 500,000원 · 수동 투자'  # a 2026-11-17 manual 혼합 (v3)
    assert V.way_label(D.clean({'profile': 'yeo'})) == '거장 투자 · 여운봉 · 언제 거장 · 얼마나 거장'  # an old 여운봉 profile goes to the master
    assert V.way_label(D.clean({})) == '거장 투자 · 레이 달리오 · 언제 내가 · 얼마나 내가'

def test_saving_shows_a_toast_that_survives_the_rerun(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.render(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.toggle(key='dca_on_actual').set_value(True).run()
    at.button(key='dca_save_actual').click().run()
    assert not at.exception
    import vibe_longterm as L
    assert L.load_settings(tmp_path)['dca']['actual']['on'] is True
    assert any('저장했습니다' in t.value for t in at.toast)


def test_the_bundle_goes_to_the_engine_only_after_the_check(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    at.selectbox(key='dca_bundle_profile').set_value('yeo').run()
    assert at.button(key='dca_bundle_save').disabled  # the live long-term account follows the engine: check first
    at.checkbox(key='dca_bundle_ok').check().run()
    at.button(key='dca_bundle_save').click().run()
    modules = L.load_settings(tmp_path)['modules']
    assert modules['yeo_reservoir'] and modules['dalio_rebalance'] and not modules['lynch_story'] and modules['bogle_core']

def test_manual_saves_my_calendar_way_and_hides_the_master(tmp_path, monkeypatch):
    # v3 (user approval 2026-11-18): 수동 투자 = every part mine; the engine and the live account read the same saved value
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    at.segmented_control(key='dca_style_actual').set_value('manual').run()
    assert not [s for s in at.selectbox if s.key == 'dca_profile_actual']  # no master to choose
    at.segmented_control(key='dca_mewhen_actual').set_value('dip').run()
    at.button(key='dca_save_actual').click().run()
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert not at.exception and (saved['style'], saved['when_by'], saved['size_by'], saved['me_when']) == ('manual', 'me', 'me', 'dip')
    import sqlite3
    assert sqlite3.connect(tmp_path / 'h.sqlite3').execute('SELECT COUNT(*) FROM changes').fetchone()[0] >= 1  # config_store history

def test_buy_limits_are_saved_kept_and_shown(tmp_path, monkeypatch):
    # user 2026-11-18: 적립 종목 선택 · 종목 수 · 종목당 주식 수 · 전체 주식 수, each optional
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    import longterm_dca_view as V
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.render(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.toggle(key='dca_pick_on_actual').set_value(True).run()
    at.multiselect(key='dca_picks_actual').set_value(['NDX', 'SPX']).run()
    at.toggle(key='dca_max_per_symbol_on_actual').set_value(True).run()
    at.number_input(key='dca_max_per_symbol_actual').set_value(40).run()
    at.button(key='dca_save_actual').click().run()
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert not at.exception
    assert (saved['pick_on'], saved['picks'], saved['max_per_symbol_on'], saved['max_per_symbol']) == (True, ['NDX', 'SPX'], True, 40)
    assert not saved['max_total_on'] and not saved['max_symbols_on']
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.number_input(key='dca_amount_actual').set_value(200_000).run()
    at.button(key='dca_save_actual').click().run()
    assert L.load_settings(tmp_path)['dca']['actual']['picks'] == ['NDX', 'SPX']  # saving the amount keeps the limits
    text = V.limits_text(saved)
    assert '2종목' in text and '종목당 40주' in text
    assert V.limits_text(L.longterm_dca.clean({})) == ''


# ---- user approval 2026-11-18 (docs/longterm-account-settings-plan.md 2-A, 3-A, 4-B②) ----

def dca_tab(root):
    import longterm_dca_view
    import vibe_longterm as L
    longterm_dca_view.render(root, L.load_settings(root))


def test_a_watchlist_code_is_checked_added_and_picked_for_the_dca(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import longterm_watchlist as W
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    monkeypatch.setattr('longterm_dca_view.nh_name', lambda code: {'005930': '삼성전자'}.get(code))
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    at.text_input(key='lt_watch_code').input('005931').run()
    at.button(key='lt_watch_add').click().run()
    assert W.load(tmp_path) == {} and at.error  # NH does not know it
    at.text_input(key='lt_watch_code').input('005930').run()
    at.button(key='lt_watch_add').click().run()
    assert not at.exception and W.load(tmp_path) == {'005930': '삼성전자'}
    at.toggle(key='dca_pick_on_actual').set_value(True).run()
    at.multiselect(key='dca_picks_actual').set_value(['NDX', '005930']).run()
    at.button(key='dca_save_actual').click().run()
    assert L.load_settings(tmp_path)['dca']['actual']['picks'] == ['NDX', '005930']
    at.selectbox(key='lt_watch_remove').set_value('005930').run()
    at.button(key='lt_watch_remove_go').click().run()
    assert not at.exception and W.load(tmp_path) == {}


def test_a_masters_pick_adds_its_domestic_etf_and_remembers_why(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    picks = [{'master': 'buffett', 'ticker': 'AMP', 'name': 'Ameriprise', 'etfs': {'IVV': 0.31}},
             {'master': 'tenbagger', 'ticker': 'TINY', 'name': 'Tiny', 'etfs': {'IWO': 0.1}}]  # no domestic ETF: not offered
    monkeypatch.setattr('us_ideas.status', lambda root=None: {'ready': True, 'day': '2026-11-13', 'picks': picks})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    at.toggle(key='dca_pick_on_actual').set_value(True).run()
    at.multiselect(key='dca_picks_actual').set_value(['NDX']).run()
    assert at.multiselect(key='dca_master_picks_actual').options == ['AMP · 워런 버핏 → KODEX 미국S&P500']
    at.multiselect(key='dca_master_picks_actual').set_value(['buffett:AMP']).run()
    at.button(key='dca_save_actual').click().run()
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert not at.exception and saved['picks'] == ['NDX', 'SPX'] and 'AMP' in saved['pick_from']['SPX']
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    assert any('AMP' in c.value and '주문하지 않' in c.value for c in at.caption)  # the label, reference only


def test_carried_parts_under_one_share_are_shown(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    L.put_state(L.connect(tmp_path), **{'dca:actual': {'contributed_krw': 30_000, 'carry': {'QUANT': 6_316.0}}})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    assert any('1주 미만 이월 6,316원' in c.value and 'SOL 미국양자컴퓨팅TOP10' in c.value for c in at.caption)


# ---- user 2026-11-18 (docs/longterm-dca-timing-plan.md 2·7): 투자 시점 first, 거장 판단 with a buy frequency ----

def test_a_master_way_saves_who_decides_each_part_and_keeps_every_other_field(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    L.save_settings(tmp_path, {'dca': {'actual': {'on': True, 'profile': 'yeo', 'timing': 'master', 'buy_every': 'month', 'pick_on': True,
                                                  'picks': ['NDX', 'SPX'], 'max_total_on': True, 'max_total': 300, 'amount_krw': 300_000}}})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    assert at.segmented_control(key='dca_whenby_actual').value == 'master' and at.segmented_control(key='dca_sizeby_actual').value == 'master'
    assert any('1.5배' in df.value.to_string() for df in at.dataframe)  # 여운봉's multipliers by regime (투매 1.5)
    at.selectbox(key='dca_profile_actual').set_value('marks').run()
    at.segmented_control(key='dca_sizeby_actual').set_value('me').run()
    at.button(key='dca_save_actual').click().run()
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert (saved['style'], saved['profile'], saved['when_by'], saved['size_by'], saved['buy_every']) == ('master', 'marks', 'master', 'me', 'month')
    assert (saved['picks'], saved['max_total_on'], saved['max_total'], saved['amount_krw']) == (['NDX', 'SPX'], True, 300, 300_000)

def test_a_yeo_book_reads_the_masters_size_from_its_value_and_saves_a_tune(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    L.save_settings(tmp_path, {'books': {'actual': {'strategy': 'yeo_method'}},
                               'dca': {'actual': {'on': True, 'profile': 'yeo', 'when_by': 'master', 'size_by': 'master'}}})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    assert any('장부 가치' in c.value and '현금 하한' in c.value for c in at.caption)
    at.toggle(key='dca_tune_actual_on').set_value(True).run()
    at.number_input(key='dca_tune_actual_floor').set_value(30).run()
    at.button(key='dca_save_actual').click().run()
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert not at.exception and saved['tune'] == {'floor': 0.3}

def test_shares_way_saves_from_the_card_and_shows_the_strategy_line(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.render(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert any('들어오는 적립금만' in i.value and '장기투자 → 전략' in i.value for i in at.info)
    at.segmented_control(key='dca_size_actual').set_value('shares').run()
    at.number_input(key='dca_shares_actual').set_value(1).run()
    assert not any(s.key == 'dca_when_actual' for s in at.segmented_control)  # the timing is not used by the shares way
    at.button(key='dca_save_actual').click().run()
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert (saved['size'], saved['shares_n']) == ('shares', 1)
    assert V.schedule_text(D.clean(saved)).startswith('매월 1일 · 계속(정한 날마다) · 10:00 · 종목별 1주')


def test_the_card_shows_the_applied_setting_and_the_example(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    assert any('적용 설정 요약' in m.value for m in at.markdown) and any('이번 회차 예시' in m.value for m in at.markdown)
    assert not at.exception

def test_a_bundle_can_go_to_one_live_account_strategy_only(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.bundle(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.selectbox(key='dca_bundle_profile').set_value('yeo').run()
    at.selectbox(key='dca_bundle_target').set_value('live-2').run()
    assert any('books.live-2' in c.value for c in at.caption) and at.dataframe
    before = L.load_settings(tmp_path)['modules']
    at.checkbox(key='dca_bundle_ok').check().run()
    at.button(key='dca_bundle_save').click().run()
    s = L.load_settings(tmp_path)
    assert s['modules'] == before and L.book_modules(s, 'live-2', {'active': []}) == sorted(D.PROFILES['yeo']['modules'])


# ---- 엔진 모듈 묶음 반영: what it is, what is on, what changes (user request 2026-11-18) ----

HISTORY = [('2026-11-18T12:30:00+09:00', 'vibe-longterm', 'modules.williams_trend', 'false', 'true', 'chat:user-approved 2026-11-18 12주 분할·필터'),
           ('2026-11-17T09:00:00+09:00', 'vibe-longterm', 'modules.lynch_story', 'false', 'true', 'dashboard')]


def test_module_rows_show_what_each_does_who_turned_it_on_and_what_the_bundle_changes():
    import vibe_longterm as L
    now = {m: m in ('bogle_core', 'dalio_rebalance', 'williams_trend', 'lynch_story', 'buffett_lowturn') for m in L.MODULES}
    want = {m: m in D.PROFILES['dalio']['modules'] for m in L.MODULES}
    rows = {r['key']: r for r in V.module_rows(now, want, HISTORY, {'lynch_story': ['양자 주의 → 절반']}, 'modules.')}
    assert rows['williams_trend']['지금'] == '켬' and rows['williams_trend']['반영 후'] == '끔'
    assert rows['williams_trend']['하는 일'] == L.MODULES['williams_trend'][2]
    assert '12주 분할' in rows['williams_trend']['마지막 변경'] and rows['lynch_story']['오늘 영향'] == '양자 주의 → 절반'
    assert V.approved_off(now, want, HISTORY, 'modules.') == ['williams_trend']  # turned on with the user's approval in chat
    assert V.approved_off(now, now, HISTORY, 'modules.') == []


def test_the_bundle_section_explains_itself_and_stops_for_a_yeo_method_account(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import vibe_longterm as L
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {'live-2': {'kind': 'live', 'engine': 'longterm', 'label': '실계좌 2'}})
    L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'yeo_method'}}})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.bundle(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception and any('사는 쪽' in c.value and '파는 쪽' in c.value for c in [*at.caption, *at.markdown])
    assert any('지금 켜진 모듈' in m.value for m in at.markdown)
    at.selectbox(key='dca_bundle_target').set_value('live-2').run()
    assert any('여운봉 저수지 매매법' in i.value and '적용되지 않' in i.value for i in at.info)
    assert not any(b.key == 'dca_bundle_save' for b in at.button)
    at.button(key='dca_bundle_guide').click().run()
    assert not at.exception and {'이게 뭔가요', '거장별 묶음', '모듈 8개', '반영 대상', '되돌리기'} <= {t.label for t in at.tabs}


def test_each_part_shows_whether_the_master_or_i_decide_it():
    # user 2026-11-18: when / how much / what / what time, each automatic (the master) or mine (v3)
    auto = V.auto_manual_rows(D.clean({'profile': 'marks', 'when_by': 'master', 'size_by': 'master', 'buy_every': 'day', 'time_mode': 'auto'}))
    assert [r['방식'] for r in auto] == ['자동 · 거장', '자동 · 거장', '자동 · 계좌 전략', '자동 · 거장']
    assert '하락일 우선' in auto[0]['내용'] and '배' in auto[1]['내용']
    mine = V.auto_manual_rows(D.clean({'size': 'shares', 'shares_n': 3, 'pick_on': True, 'picks': ['NDX'], 'time': '13:00'}))
    assert [r['방식'] for r in mine] == ['수동 · 내가'] * 4 and '종목별 3주' in mine[1]['내용'] and mine[3]['내용'] == '13:00'
    assert V.schedule_text(D.clean({'frequency': 'daily', 'time_mode': 'auto'})).startswith('매일 · 계속(정한 날마다) · 시각 자동')

def test_the_shares_way_is_mine_and_the_fill_button_sets_every_part_to_the_master(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})
    at = AppTest.from_function(dca_tab, args=(tmp_path,)).run()
    at.segmented_control(key='dca_size_actual').set_value('shares').run()
    at.segmented_control(key='dca_whenby_actual').set_value('master').run()
    at.segmented_control(key='dca_timemode_actual').set_value('auto').run()
    assert not any(s.key == 'dca_time_actual' for s in at.selectbox)  # the master picks the time
    at.button(key='dca_save_actual').click().run()
    import vibe_longterm as L
    saved = L.load_settings(tmp_path)['dca']['actual']
    assert (saved['size'], saved['when_by'], saved['size_by'], saved['time_mode']) == ('shares', 'master', 'me', 'auto')  # my N shares on its days
    at.button(key='dca_all_auto_actual').click().run()
    assert at.segmented_control(key='dca_size_actual').value == 'amount' and at.segmented_control(key='dca_sizeby_actual').value == 'master'
    assert at.segmented_control(key='dca_whenby_actual').value == 'master' and not at.exception

def test_the_buy_time_choice_has_a_guide_with_its_diagram(tmp_path, monkeypatch):
    # user 2026-11-18: a pop-up next to 거장 자동(장중 시황) explaining the data and the decision
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.render(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    at.button(key='dca_time_guide_actual').click().run()
    assert not at.exception
    text = ' '.join(m.value for m in at.markdown)
    assert '어제 종가' in text and D.TIMES[1] in text and 'NH' in text
    # the 적립식 flow, the buy-time decision and the whole day: Archify (iframe), or Graphviz when Archify is missing
    assert len(at.get('iframe')) + len(at.get('graphviz_chart')) >= 3
    import longterm_inputs_guide as IG
    a, b = IG._example_series(True), IG._example_series(False)
    assert any(r['가격'] <= r['어제 종가'] for r in a) and all(r['가격'] > r['어제 종가'] for r in b)  # the two cases they show


def test_carried_parts_are_named_not_coded():
    import longterm_dca_view as V
    text = V.carry_text({'carry': {'NDXMIX': 5_866, '379800': 100}}, {'379800': '내 종목'})
    assert 'NDXMIX' not in text and '혼합' in text and '내 종목' in text
