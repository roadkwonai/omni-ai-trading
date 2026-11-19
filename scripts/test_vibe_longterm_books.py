"""A strategy (master module set) per long-term book (user request 2026-11-18): a live account may trade 여운봉 only while
the virtual book keeps the engine's modules."""
import longterm_live as LL
import vibe_longterm as L
from test_vibe_longterm import ind


def test_books_default_to_the_engine_and_a_custom_set_always_keeps_bogle(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    s = L.load_settings(tmp_path)
    plan = {'active': ['bogle_core', 'dalio_rebalance', 'lynch_story']}
    assert s['books'] == {} and L.book_modules(s, 'live-2', plan) == plan['active']
    assert L.book_strategy_label(s, 'live-2') == '엔진(가상 계좌)과 같음'
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom', 'modules': {'yeo_reservoir': True, 'bogle_core': True}}}})
    assert L.book_modules(s, 'live-2', plan) == ['bogle_core', 'yeo_reservoir']
    assert L.book_strategy_label(s, 'live-2') == '기준 비중 + 저수지 분할(일반)'  # the preset it matches
    assert L.load_settings(tmp_path)['books']['live-2']['strategy'] == 'custom'
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'nonsense'}}})
    assert s['books']['live-2']['strategy'] == 'engine' and L.book_modules(s, 'live-2', plan) == plan['active']
    assert {'yeo_only', 'marks_yeo'} <= set(L.BOOK_PRESETS) and 'bogle_core' not in L.BOOK_PRESETS['yeo_only'][1]


def test_a_custom_label_names_the_masters_when_no_preset_matches(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom', 'modules': {'williams_trend': True, 'lynch_story': True}}}})
    label = L.book_strategy_label(s, 'live-2')
    assert label.startswith('직접') and '윌리엄스' in label and '린치' in label


def test_the_live_account_plans_with_its_own_modules():
    story = {k: {'status': 'watch', 'tilt': 0, 'reason': ''} for k in L.SATS}
    plan = {'indicators': ind(), 'regime': 'bull', 'active': ['bogle_core', 'lynch_story'], 'weights': None, 'tuning': None}
    quotes = {k: (10_000, 10_000) for k in L.ASSETS}
    _, engine, _ = LL.day_orders(plan, story, {}, {}, quotes, 10_000_000, 10_000_000, '2026-11-18')
    _, own, _ = LL.day_orders(plan, story, {}, {}, quotes, 10_000_000, 10_000_000, '2026-11-18', modules=['bogle_core', 'yeo_reservoir'])
    assert engine['want']['QUANT'] == L.ASSETS['QUANT'][3] / 2  # 피터 린치 halves a theme on watch
    assert own['want']['QUANT'] == L.ASSETS['QUANT'][3] and own['modules'] == ['bogle_core', 'yeo_reservoir']


# ---- 보글 코어 선택 (user 2026-11-18): strongly recommended, no longer forced ----

def test_without_bogle_the_base_waits_in_the_reservoir_and_only_module_adds_are_bought():
    alone = L.targets(ind(dd=-0.25), ['yeo_reservoir'], 'bear', {}, {}, '2026-11-18')[0]
    assert alone['NDX'] == alone['SPX'] == 0.0375 and all(alone[k] == 0 for k in L.SATS)  # one −20% step: 7.5% into the core
    assert abs(alone['RES'] - (1 - 0.075)) < 1e-9
    calm = L.targets(ind(), ['marks_pendulum'], 'overheat', {}, {}, '2026-11-18')[0]
    assert min(calm.values()) >= 0 and calm['RES'] == 1.0  # a cut below zero stays at zero
    assert L.targets(ind(), ['bogle_core'], 'bull', {}, {}, '2026-11-18')[0]['NDX'] == L.ASSETS['NDX'][3]


def test_bogle_can_be_turned_off_for_the_engine_and_an_account_and_is_on_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    assert not L.MODULES['bogle_core'][5] and L.load_settings(tmp_path)['modules']['bogle_core'] is True
    s = L.save_settings(tmp_path, {'modules': {'bogle_core': False}})
    assert s['modules']['bogle_core'] is False and 'bogle_core' not in L.active_modules(s, ['bogle_core'])
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom', 'modules': {'yeo_reservoir': True, 'bogle_core': False}}}})
    assert L.book_modules(s, 'live-2', {'active': []}) == ['yeo_reservoir']
    assert L.base_dependent(['yeo_reservoir', 'dalio_rebalance']) == ['dalio_rebalance']  # 안내: needs a base weight
    assert L.base_dependent(['bogle_core', 'dalio_rebalance']) == []


def test_each_master_gets_one_grade_for_the_regime_from_one_rule():
    grades = {m: L.module_grade(m, 'correction') for m in L.MODULES}
    assert grades['bogle_core'][0] == grades['dalio_rebalance'][0] == '강력 추천'
    assert grades['yeo_reservoir'][0] == '추천' and grades['williams_rebound'][0] == '비권장' and grades['marks_pendulum'][0] == '선택'
    assert L.module_grade('williams_rebound', 'panic')[0] == '추천'  # recommended in that regime
    assert all(g in L.GRADES and why and ev for g, why, ev in grades.values())
    assert L.recommended_set('correction') == sorted({'bogle_core', *L.REGIMES['correction'][2]})


def test_a_custom_account_without_its_own_set_starts_from_the_engine(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    plan = {'active': ['bogle_core', 'dalio_rebalance']}
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom'}}})
    assert s['books']['live-2']['modules'] is None and L.book_modules(s, 'live-2', plan) == plan['active']
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom', 'modules': {'yeo_reservoir': True}}}})
    s = L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'engine'}}})  # back to the engine: the chosen set is kept
    assert s['books']['live-2']['modules']['yeo_reservoir'] is True and L.book_modules(s, 'live-2', plan) == plan['active']


# ---- review 2026-11-18 (3rd, TDD) ----

def flat_plan():
    return {'indicators': {'dd': -0.05, 'gap': 0.01, 'day_ret': 0, 'vix': 18, 'sats': {}}, 'regime': 'bull', 'weights': None, 'params': None,
            'tuning': {**L.DEFAULT_SETTINGS['tuning'], 'deploy_weeks': 12}}


def test_a_deployment_that_ended_long_ago_does_not_shorten_a_new_one():
    # a book that was deploying, ran another strategy, and comes back months later starts a fresh 12 weeks
    state = {'deploying': True, 'deploy': {'start': '2026-11-16', 'last': '2026-11-16'}, 'last_want': None}
    *_, planned, _ = L.plan_orders(flat_plan(), ['bogle_core'], state, {}, {'NDX': 25_000_000}, 100_000_000, 75_000_000, '2027-06-07')
    assert sum(v for v in planned.values() if v > 0) <= 75_000_000 / 12 * 1.5
    done = {'deploying': False, 'deploy': {'start': '2026-11-16', 'last': '2026-11-16'}}
    *_, after = L.plan_orders(flat_plan(), ['bogle_core'], done, {}, {k: 1 for k in L.ASSETS}, 100, 0, '2026-11-18')
    assert 'deploy' not in after  # a finished deployment leaves no start behind


def test_the_account_shadow_trades_whole_shares_and_can_sell_its_last_one():
    prices = {k: 10_000 for k in L.ASSETS}
    plan = {**flat_plan(), 'tuning': dict(L.DEFAULT_SETTINGS['tuning'])}
    saved, _ = L.account_shadow(None, 1_000_000, ['bogle_core'], plan, {}, prices, '2026-11-18')
    assert all(float(q).is_integer() for q in saved['book']['qty'].values()) and saved['book']['qty']['SPACE'] >= 1
    broken = {k: {'status': 'broken', 'tilt': 0} for k in L.SATS}
    after, _ = L.account_shadow(saved, 1_000_000, ['bogle_core', 'lynch_story'], plan, broken, prices, '2026-11-19')
    assert after['book']['qty']['SPACE'] == 0
    old = {**saved, 'book': {**saved['book'], 'qty': {**saved['book']['qty'], 'SPACE': 0.9995}}}  # a share bought with slippage before
    after, _ = L.account_shadow(old, 1_000_000, ['bogle_core', 'lynch_story'], plan, broken, prices, '2026-11-19')
    assert after['book']['qty']['SPACE'] == 0


def test_the_regime_dca_shadow_runs_the_virtual_books_own_strategy(tmp_path, monkeypatch):
    # review 2026-11-18: with the virtual book on 여운봉 저수지 매매법, 적립 시황 자동 still ran the engine's modules
    from test_vibe_longterm import Read, decided, dca_switch, krx_calendar  # noqa: F401
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_switch(tmp_path)
    L.save_settings(tmp_path, {'books': {'actual': {'strategy': 'yeo_method'}}})
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40), execute=True) == 'closed'
    st = L.read_state(tmp_path)
    assert 'yeo' in st['shadow:dca_auto']['state'] and 'yeo' not in st['shadow:base']['state']  # base and auto stay module books
