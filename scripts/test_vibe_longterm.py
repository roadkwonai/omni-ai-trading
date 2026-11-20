import json
from datetime import date, datetime, timedelta

import pytest

import vibe_longterm as L

KST = L.KST
DAY = date(2026, 11, 18)


@pytest.fixture(autouse=True)
def krx_calendar(monkeypatch):
    """The KRX holiday table as the engine caches it, so no test asks KRX."""
    monkeypatch.setattr(L, '_HOLIDAYS', {year: frozenset({date(2026, 12, 25)}) for year in range(2024, 2031)}, raising=False)
    import accounts
    monkeypatch.setattr(accounts, 'load', lambda *a, **k: {})  # close() runs no live account's shadow unless a test passes one


def ind(**kw):
    base = {'as_of': '2026-11-17', 'ndx': 100.0, 'dd': -0.02, 'gap': 0.05, 'slope': 0.01, 'day_ret': 0.0, 'vix': 18.0,
            'usdkrw': 1400.0, 'season_month_mean': 0.01, 'sats': {k: {'gap': 0.05, 'slope': 0.01, 'below_days': 0} for k in L.SATS}}
    return {**base, **kw}


def test_raw_regime_order_and_confirmation_needs_persistence():
    assert L.raw_regime(ind(vix=40, dd=-0.2), 'bull') == 'panic'
    assert L.raw_regime(ind(gap=-0.01, slope=-0.01), 'bull') == 'bear'
    assert L.raw_regime(ind(dd=-0.12), 'bull') == 'correction'
    assert L.raw_regime(ind(), 'correction') == 'recovery'
    assert L.raw_regime(ind(gap=0.2, vix=13), 'bull') == 'overheat'
    assert L.raw_regime(ind(), 'bull') == 'bull'
    s = L.confirm(None, 'bull', '2026-11-01')
    assert s['regime'] == 'bull'
    for i in range(9):
        s = L.confirm(s, 'correction', f'2026-11-{2 + i:02d}')
    assert s['regime'] == 'bull' and s['pending_days'] == 9
    assert L.confirm(s, 'correction', '2026-11-11')['regime'] == 'correction'
    assert L.confirm(s, 'correction', s['day']) == s  # the same day is counted once
    p = L.confirm(L.confirm(L.confirm(None, 'bull', 'a'), 'panic', 'b'), 'panic', 'c')
    assert p['regime'] == 'panic'  # panic needs two days


def test_targets_apply_modules_in_order_and_sum_to_one():
    base, _, _ = L.targets(ind(), ['bogle_core'], 'bull', {}, {}, '2026-11-18')
    assert base == {k: a[3] for k, a in L.ASSETS.items()}
    stories = {'SEMI': {'status': 'broken', 'tilt': 0}, 'QUANT': {'status': 'watch', 'tilt': 0}, 'SPACE': {'status': 'intact', 'tilt': 1}}
    want, _, notes = L.targets(ind(), ['bogle_core', 'lynch_story'], 'bull', {}, stories, 'd')
    assert want['SEMI'] == 0 and want['QUANT'] == pytest.approx(0.04) and want['SPACE'] == pytest.approx(0.0875)
    assert sum(want.values()) == pytest.approx(1) and any(m == 'lynch_story' and '훼손' in text for m, text in notes)
    want, s, _ = L.targets(ind(dd=-0.31), ['bogle_core', 'yeo_reservoir'], 'bear', {}, {}, 'd')
    assert want['RES'] == pytest.approx(0.0) and want['NDX'] == pytest.approx(0.375) and s['tiers'] == [0, 1]
    want, s, _ = L.targets(ind(dd=0.0), ['bogle_core', 'yeo_reservoir'], 'bull', s, {}, 'd2')
    assert s['tiers'] == [] and want['RES'] == pytest.approx(0.15)  # a new high re-arms the tiers
    want, _, _ = L.targets(ind(), ['bogle_core', 'marks_pendulum'], 'overheat', {}, {}, 'd')
    assert want['RES'] == pytest.approx(0.20)
    sats = {k: {'gap': -0.05, 'slope': -0.01, 'below_days': 6} for k in L.SATS}
    want, s, _ = L.targets(ind(sats=sats), ['bogle_core', 'williams_trend'], 'bear', {}, {}, 'd')
    assert want['SEMI'] == pytest.approx(0.05) and 'SEMI' in s['trend_cut']


def test_rebound_opens_on_a_crash_day_and_ends_after_ten_days():
    mods = ['bogle_core', 'williams_rebound']
    want, s, _ = L.targets(ind(day_ret=-0.04, vix=30), mods, 'panic', {}, {}, 'd0')
    assert want['NDX'] == pytest.approx(0.35)
    for i in range(1, 10):
        want, s, _ = L.targets(ind(), mods, 'panic', s, {}, f'd{i}')
    assert 'rebound' in s
    want, s, _ = L.targets(ind(), mods, 'panic', s, {}, 'd10')
    assert 'rebound' not in s and want['NDX'] == pytest.approx(0.30)


def test_trade_targets_initial_monthly_band_and_module_change():
    want = {k: a[3] for k, a in L.ASSETS.items()}
    s = {}
    assert L.trade_targets(want, {}, s, ['dalio_rebalance'], '2026-11-18', invested=False)[1] == 'initial_deployment'
    assert L.trade_targets(want, want, s, ['dalio_rebalance'], '2026-11-19', invested=True)[1] == 'hold'
    drift = {**want, 'NDX': 0.40, 'RES': 0.05}
    assert L.trade_targets(want, drift, s, ['dalio_rebalance'], '2026-11-20', invested=True)[1] == 'hold'  # same month
    assert L.trade_targets(want, drift, s, ['dalio_rebalance'], '2026-12-01', invested=True)[1] == 'monthly_band'
    new = {**want, 'SEMI': 0.05, 'RES': 0.20}
    banded = {**want, 'NDX': 0.302, 'RES': 0.148}  # the band filled to within MIN_TRADE_SHARE
    target, why = L.trade_targets(new, banded, s, ['dalio_rebalance'], '2026-12-02', invested=True)
    assert why.startswith('module_change') and target['SEMI'] == 0.05 and target['NDX'] == 0.302  # only the change trades


def test_orders_cap_buys_and_monthly_non_reservoir_sells():
    nav = 100_000_000
    values = {'NDX': 50_000_000, 'RES': 50_000_000}
    target = {'NDX': 0.20, 'SPX': 0.30, 'SEMI': 0.10, 'QUANT': 0.0, 'SPACE': 0.0, 'RES': 0.40}
    s = {}
    out = L.orders(target, values, nav, ['buffett_lowturn'], s, '2026-11', 25_000_000, 0)
    assert out['NDX'] == pytest.approx(-10_000_000)  # 30M wanted, 10% of NAV allowed this month
    assert out['RES'] == pytest.approx(-10_000_000)
    assert sum(v for v in out.values() if v > 0) == pytest.approx(20_000_000)  # only what the sells freed
    again = L.orders(target, values, nav, ['buffett_lowturn'], s, '2026-11', 25_000_000, 0)
    assert 'NDX' not in again  # the month's room is used up


def test_parse_story_validates_and_story_check_falls_back():
    text = 'noise {"x": 1} ' + json.dumps({'market_view': '장기 우상향', 'stories': {
        k: {'status': 'intact', 'tilt': 0, 'reason': '수요 유지'} for k in L.SATS}})
    assert L.parse_story(text)['stories']['SEMI']['status'] == 'intact'
    with pytest.raises(ValueError):
        L.parse_story('{"stories": {"SEMI": {"status": "moon", "tilt": 0, "reason": ""}}}')
    now = datetime(2026, 11, 18, 8, 45, tzinfo=KST)
    prev = {'time': (now - timedelta(days=3)).isoformat(), 'stories': {'SEMI': {'status': 'watch', 'tilt': 0, 'reason': 'r'}}}
    def boom(text):
        raise ValueError('down')
    stories, _, source = L.story_check(ind(), 'bull', prev, now, ask=boom)
    assert stories['SEMI']['status'] == 'watch' and source.startswith('previous')
    stories, _, source = L.story_check(ind(), 'bull', None, now, ask=boom)
    assert stories['SEMI']['status'] == 'intact' and source.startswith('default')


def test_settings_save_bogle_as_chosen_and_record_history(tmp_path, monkeypatch):
    # 2026-11-18 (user): 보글 코어 is strongly recommended and on by default, but the user may turn it off
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    assert L.load_settings(tmp_path)['mode'] == 'manual' and L.load_settings(tmp_path)['modules']['bogle_core'] is True
    saved = L.save_settings(tmp_path, {'mode': 'auto', 'auto_live': True, 'modules': {'bogle_core': False, 'yeo_reservoir': True}})
    assert saved['modules']['bogle_core'] is False and saved['modules']['yeo_reservoir'] is True
    assert L.load_settings(tmp_path) == saved
    assert L.active_modules(saved, ['bogle_core', 'marks_pendulum']) == ['bogle_core', 'marks_pendulum']
    import sqlite3
    with sqlite3.connect(tmp_path / 'h.sqlite3') as db:
        assert db.execute("SELECT COUNT(*) FROM changes WHERE kind='vibe-longterm'").fetchone()[0] >= 2


class Read:
    """NH read-only stand-in: fresh in-session quotes for every asset."""
    def __init__(self, price=10_000):
        self.price = price

    def call_tool(self, tool, params):
        code = params['stock_code']
        now = datetime.now(KST)
        return {'status': 'ok', 'data': {'rsp_cd': '00000', 'Output_0': {
            'iem_cd': code, 'market_status': '1', 'hoga_bsop_hour': now.strftime('%H:%M:%S'),
            'bidp': self.price, 'askp': self.price, 'stck_prpr': self.price}}}


def fake_fetch(symbol, years=2):
    start = DAY - timedelta(days=400)
    return {start + timedelta(days=i): 100.0 + i * 0.1 for i in range(400)}


def test_decide_trade_close_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    answer = json.dumps({'market_view': '강세', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    assert L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: answer) == 'decided'
    assert L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: answer) == 'already_decided'
    now = now.replace(hour=9, minute=40)
    assert L.trade(tmp_path, Read(), now) == 'planned'  # dry run
    db = L.connect(tmp_path)
    kinds = [r[0] for r in db.execute('SELECT kind FROM decisions ORDER BY rowid')]
    assert kinds == ['regime', 'macro', 'news', 'story', 'map', 'plan']  # map: module impacts and levels (2026-11-18)
    st = L.get_state(db)
    assert set(st['plan']['impacts']) == set(L.MODULES) - {'bogle_core'}
    assert st['plan']['levels'] and not L.level_problems(st['plan']['levels'], L.CAPITAL)
    plan = st['last_plan']
    assert plan['why'] == 'initial_deployment' and sum(o['qty'] * o['price'] for o in plan['orders']) <= 25_000_000
    # executed on a fresh day: virtual fills with the fee
    st = L.get_state(db)
    L.put_state(db, plan={**st['plan']}, traded_day=None)
    assert L.trade(tmp_path, Read(), now, execute=True) == 'traded'
    broker = L.broker_for(tmp_path, Read())
    assert broker.holdings() and broker.cash() < L.CAPITAL - 24_000_000
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40)) == 'closed'
    books = {r[0] for r in db.execute('SELECT book FROM nav')}
    assert books == {'actual', 'base', 'auto'}
    latest = L.read_state(tmp_path, ('nav_latest',))['nav_latest']
    assert set(latest) == {'day', 'actual', 'base', 'auto', L.CONTRIBUTED} and latest[L.CONTRIBUTED] == 0
    assert latest['actual'] == pytest.approx(
        dict(db.execute("SELECT book, value FROM nav WHERE book='actual'").fetchall())['actual'])


def test_settings_weights_tuning_and_execute(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    base = L.load_settings(tmp_path)
    assert base['execute'] is False and base['tuning']['band'] == 0.05
    bad = {**base['weights'], 'SEMI': 0.20, 'RES': 0.05}  # satellites 35%
    with pytest.raises(ValueError, match='위성 합계'):
        L.save_settings(tmp_path, {**base, 'weights': bad})
    good = {**base['weights'], 'NDX': 0.35, 'RES': 0.10}
    saved = L.save_settings(tmp_path, {**base, 'weights': good, 'execute': True, 'tuning': {'band': 0.5, 'confirm_days': 3}})
    assert saved['tuning']['band'] == 0.10 and saved['tuning']['confirm_days'] == 5 and saved['execute'] is True
    want, _, _ = L.targets(ind(), ['bogle_core'], 'bull', {}, {}, 'd', saved['weights'])
    assert want['NDX'] == pytest.approx(0.35) and want['RES'] == pytest.approx(0.10)
    s = L.confirm(L.confirm(None, 'bull', 'a'), 'correction', 'b', days=2)
    assert L.confirm(s, 'correction', 'c', days=2)['regime'] == 'correction'


def test_journal_rows_from_the_virtual_account(tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path / 'virtual-account.sqlite3') as db:
        db.execute('CREATE TABLE orders (number INTEGER PRIMARY KEY, day TEXT, symbol TEXT, side TEXT, qty INTEGER, price INTEGER, '
                   'filled INTEGER, cancelled INTEGER, fill_price REAL, time TEXT)')
        db.executemany('INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)', [
            (1, 'd', '379810', 'BUY', 10, 100, 10, 0, 100.0, '2026-11-18T09:40'),
            (2, 'd', '379810', 'SELL', 4, 110, 4, 0, 110.0, '2026-12-01T09:40'),
            (3, 'd', '379800', 'BUY', 5, 50, 0, 5, None, '2026-12-01T09:41')])  # cancelled: not a fill
    rows = L.journal_rows(tmp_path)
    assert [r['side'] for r in rows] == ['BUY', 'SELL'] and rows[1]['pnl'] == pytest.approx(40) and rows[1]['after'] == 6


def test_read_state_is_empty_before_the_first_run_and_filters_keys(tmp_path):
    assert L.read_state(tmp_path) == {}
    L.put_state(L.connect(tmp_path), plan={'day': 'd'}, other=1)
    assert L.read_state(tmp_path, ('plan',)) == {'plan': {'day': 'd'}}
    assert L.read_state(tmp_path)['other'] == 1


# ---- review 2026-11-17: defects pinned before the fix ----

def test_an_expired_story_is_not_used_for_trading(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    old = (now - timedelta(days=40)).isoformat()
    L.put_state(L.connect(tmp_path), story={'time': old, 'source': 'llm', 'market_view': '',
                                            'stories': {k: {'status': 'broken', 'tilt': 0, 'reason': 'x'} for k in L.SATS}})
    def down(text):
        raise ValueError('llm down')
    L.decide(tmp_path, now, fetch=fake_fetch, ask=down)
    stories = L.read_state(tmp_path)['story']['stories']
    assert {v['status'] for v in stories.values()} == {'intact'}  # what the record shows is what trading uses


def test_a_module_change_keeps_trading_until_the_target_is_reached():
    want = {k: a[3] for k, a in L.ASSETS.items()}
    s = {}
    L.trade_targets(want, want, s, ['buffett_lowturn'], '2026-11-18', invested=True)
    new = {**want, 'SEMI': 0.0, 'RES': 0.25}
    target, why = L.trade_targets(new, want, s, ['buffett_lowturn'], '2026-11-19', invested=True)
    assert why.startswith('module_change') and target['SEMI'] == 0.0
    half = {**want, 'SEMI': 0.05, 'RES': 0.20}  # the day's orders only got half way
    target, why = L.trade_targets(new, half, s, ['buffett_lowturn'], '2026-11-20', invested=True)
    assert why.startswith('module_change') and target['SEMI'] == 0.0
    done = {**want, 'SEMI': 0.0, 'RES': 0.25}
    assert L.trade_targets(new, done, s, ['buffett_lowturn'], '2026-11-21', invested=True)[1] == 'hold'


def test_a_new_high_restores_the_reservoir_slowly_not_by_selling_the_core():
    mods = ['bogle_core', 'yeo_reservoir', 'dalio_rebalance']
    s = {}
    want, s, _ = L.targets(ind(dd=-0.31), mods, 'bear', s, {}, 'd1')
    L.trade_targets(want, want, s, mods, '2026-11-18', invested=True)
    want, s, _ = L.targets(ind(dd=0.0), mods, 'bull', s, {}, 'd2')
    held = {**want, 'NDX': 0.375, 'SPX': 0.375, 'RES': 0.0}
    target, why = L.trade_targets(want, held, s, mods, '2026-11-19', invested=True)
    assert target is None and why == 'hold'  # the monthly band brings it back, not an immediate sale
    assert L.trade_targets(want, held, s, mods, '2026-12-01', invested=True)[1] == 'monthly_band'


def test_orders_split_the_buy_budget_across_targets():
    target = {k: a[3] for k, a in L.ASSETS.items()}
    out = L.orders(target, {}, 100_000_000, [], {}, '2026-11', 25_000_000, 100_000_000)
    assert sum(out.values()) == pytest.approx(25_000_000) and len(out) == len(L.ASSETS)
    assert out['NDX'] == pytest.approx(out['SPX']) and out['NDX'] > out['SEMI'] > out['SPACE']


def test_a_capped_monthly_sale_is_reported():
    s = {}
    L.orders({'NDX': 0.0, 'SPX': 0.3, 'SEMI': 0.1, 'QUANT': 0.08, 'SPACE': 0.07, 'RES': 0.45},
             {'NDX': 30_000_000, 'SPX': 30_000_000, 'SEMI': 10_000_000, 'QUANT': 8_000_000, 'SPACE': 7_000_000, 'RES': 15_000_000},
             100_000_000, ['buffett_lowturn'], s, '2026-11', 25_000_000, 0)
    assert s['sell_capped_krw'] == pytest.approx(20_000_000)


def test_auto_live_needs_auto_mode_and_partial_saves_keep_other_values(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    L.save_settings(tmp_path, {**L.load_settings(tmp_path), 'mode': 'auto', 'auto_live': True,
                               'modules': {**L.DEFAULT_SETTINGS['modules'], 'yeo_reservoir': True}})
    manual = L.save_settings(tmp_path, {'mode': 'manual'})
    assert manual['auto_live'] is False  # leaving auto drops the live switch, so coming back asks again
    assert manual['modules']['yeo_reservoir'] is True  # a partial save keeps the rest


def test_regime_confirmation_counts_us_sessions_not_krx_days(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    answer = json.dumps({'market_view': '', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    def falling(symbol, years=2):  # the same US closes on two KRX days (a US holiday between them)
        start = DAY - timedelta(days=400)
        return {start + timedelta(days=i): 200.0 - i * 0.2 for i in range(400)}
    for day in (DAY, DAY + timedelta(days=1)):
        now = datetime.combine(day, datetime.min.time(), KST).replace(hour=8, minute=45)
        L.decide(tmp_path, now, fetch=falling, ask=lambda text: answer)
    regime = L.read_state(tmp_path)['regime']
    assert regime['day'] == (DAY - timedelta(days=1)).isoformat()  # keyed by the US close, counted once


def test_why_labels_are_korean():
    assert L.why_label('initial_deployment') == '초기 투입'
    assert L.why_label('module_change:NDX,RES') == '모듈 변화(KODEX 미국나스닥100, TIGER KOFR금리액티브(합성))'
    assert L.why_label('hold') == '유지'


class RisingRead(Read):
    """The ask moves up between the plan's quote and the fill: limit orders rest and must be cancelled."""
    def call_tool(self, tool, params):
        self.price += 50
        return super().call_tool(tool, params)


def test_unfilled_orders_are_cancelled_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    answer = json.dumps({'market_view': '', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: answer)
    assert L.trade(tmp_path, RisingRead(), now.replace(hour=9, minute=40), execute=True) == 'traded'
    orders = L.read_state(tmp_path)['last_plan']['orders']
    assert orders and all(o['filled'] == 0 and o['cancelled'] == o['qty'] for o in orders)
    broker = L.broker_for(tmp_path, Read())
    assert broker.holdings() == {} and broker.cash() == L.CAPITAL  # nothing rests, nothing was paid


# ---- level map (docs/longterm-strategy-plan.md 2) ----

def closes(n=300, start=100.0, step=0.1):
    return [start + i * step for i in range(n)]


def level_map(**kw):
    base = dict(closes={k: closes() for k in L.ASSETS}, ind=ind(dd=-0.05, gap=0.04), regime='bull',
                weights={k: a[3] for k, a in L.ASSETS.items()}, holdings={}, nav=100_000_000,
                tuning=L.DEFAULT_SETTINGS['tuning'], active=['bogle_core', 'dalio_rebalance', 'yeo_reservoir'], book_state={})
    return L.levels(**{**base, **kw})


def test_reservoir_tiers_are_rule_levels_sized_from_the_account():
    lv = [x for x in level_map() if x['master'] == 'yeo_reservoir']
    assert {(x['asset'], x['side'], x['kind']) for x in lv} == {('NDX', 'buy', 'rule'), ('SPX', 'buy', 'rule')}
    ndx = sorted((x for x in lv if x['asset'] == 'NDX'), key=lambda x: -x['price'])
    cur = closes()[-1]
    assert ndx[0]['price'] == pytest.approx(cur * 0.80 / 0.95, rel=1e-3) and ndx[1]['price'] == pytest.approx(cur * 0.70 / 0.95, rel=1e-3)
    assert ndx[0]['size']['krw'] == 3_750_000 and ndx[0]['active'] is True
    used = level_map(book_state={'tiers': [0]})
    assert [x['status'] for x in used if x['master'] == 'yeo_reservoir' and x['asset'] == 'NDX' and x['price'] > cur * 0.75] == ['filled']


def test_dalio_band_prices_put_the_weight_on_the_band_edges():
    cur = closes()[-1]
    qty = int(30_000_000 // cur)
    lv = level_map(holdings={'NDX': (qty, cur)})
    band = {x['side']: x['price'] for x in lv if x['master'] == 'dalio_rebalance' and x['asset'] == 'NDX'}
    rest = 100_000_000 - qty * cur
    for side, edge in (('buy', 0.25), ('sell', 0.35)):
        p = band[side]
        assert qty * p / (rest + qty * p) == pytest.approx(edge, rel=1e-3)  # prices are kept to 0.1 won
    assert not [x for x in level_map() if x['master'] == 'dalio_rebalance']  # nothing held: no band yet


def test_lynch_take_profits_need_a_holding_and_swing_pullbacks_need_an_uptrend():
    cur = closes()[-1]
    lv = level_map(holdings={'SEMI': (1000, 100.0)})
    tp = sorted(x['price'] for x in lv if x['master'] == 'lynch_story' and x['asset'] == 'SEMI')
    assert tp == pytest.approx([120.0, 140.0]) and all(x['kind'] == 'advice' for x in lv if x['master'] == 'lynch_story')
    pull = [x for x in level_map() if x['horizon'] == '스윙' and x['side'] == 'buy' and x['asset'] == 'SEMI']
    assert pull and pull[0]['price'] == pytest.approx(sum(closes()[-50:]) / 50, rel=1e-3) and pull[0]['kind'] == 'advice'
    falling = {k: list(reversed(closes())) for k in L.ASSETS}
    assert not [x for x in level_map(closes=falling) if x['horizon'] == '스윙' and x['side'] == 'buy']


def test_every_level_is_complete_and_inside_the_account():
    lv = level_map(holdings={'SEMI': (1000, 100.0), 'NDX': (100_000, 130.0)})
    assert lv and all(set(x) >= {'asset', 'horizon', 'master', 'side', 'price', 'size', 'condition', 'kind', 'status', 'case', 'active'} for x in lv)
    assert all(0 < x['price'] and 0 <= x['size']['krw'] <= 100_000_000 for x in lv)
    assert L.level_problems(lv, 100_000_000) == []
    bad = [{**lv[0], 'size': {'weight': 2.0, 'krw': 200_000_000}}]
    assert L.level_problems(bad, 100_000_000)


def test_levels_with_nothing_to_trade_are_left_out():
    lv = level_map()  # nothing held: no take-profits and no overheat trims on satellites
    assert not [x for x in lv if x['size']['krw'] == 0]
    assert not [x for x in lv if x['master'] == 'marks_pendulum' and x['asset'] in L.SATS]


def test_dca_on_the_virtual_book_deposits_buys_and_keeps_its_waiting_money_out_of_the_engine(tmp_path, monkeypatch):
    # user 2026-11-17: 적립식 runs only when set; the waiting pool is not the engine's money to rebalance
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    answer = json.dumps({'market_view': '강세', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: answer)
    assert L.dca(tmp_path, Read(), now.replace(hour=10)) == 'off'
    L.save_settings(tmp_path, {'execute': True, 'dca': {'actual': {'on': True, 'profile': 'dalio', 'when_by': 'me', 'size_by': 'master',
                                                                     'amount_krw': 2_000_000, 'day': 1, 'time': '10:00'}}})
    old = L.longterm_dca.clean({'on': True, 'mode': 'yeo'})
    assert (old['style'], old['me_when']) == ('manual', 'dip')  # a 2026-11-17 manual 혼합 is read as my down-day calendar (v3)
    assert L.dca(tmp_path, Read(), now.replace(hour=9, minute=50)) == 'waiting'  # before its time
    broker = L.broker_for(tmp_path, Read())
    before = broker.cash()
    assert L.dca(tmp_path, Read(), now.replace(hour=10)) == 'dca_traded'
    state = L.get_state(L.connect(tmp_path))['dca:actual']
    assert state['contributed_krw'] == 2_000_000 and 0 < state['waiting_krw'] < 2_000_000  # the master buys part, the rest stays its pool
    assert broker.cash() > before  # deposited 2,000,000, part of it bought
    assert L.dca(tmp_path, Read(), now.replace(hour=10, minute=30)) == 'dca_checked'  # once a day
    kinds = [r[0] for r in L.connect(tmp_path).execute('SELECT kind FROM decisions ORDER BY rowid')]
    assert kinds[-1] == 'dca'


# ---- review 2026-11-17 (2): money paths across retries, crashes and concurrent runs ----

ANSWER = json.dumps({'market_view': '', 'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})


def decided(tmp_path, monkeypatch):
    """Today's plan is made (08:45); returns that time."""
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: ANSWER)
    return now


def account_value(root):
    """Cash plus holdings at the stub price (10,000) of the virtual account."""
    broker = L.broker_for(root, Read())
    return broker.cash() + sum(q * 10_000 for q, _ in broker.holdings().values())


class DownRead(Read):
    """Quotes unavailable (a market data hiccup)."""
    def call_tool(self, tool, params):
        return {'status': 'error'}


def fail_first_log(monkeypatch):
    """The first decision write fails as if another process held the database."""
    real, calls = L.log, []
    def log(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            import sqlite3
            raise sqlite3.OperationalError('database is locked')
        return real(*args, **kwargs)
    monkeypatch.setattr(L, 'log', log)


def dca_on(tmp_path):
    L.save_settings(tmp_path, {'execute': True, 'dca': {'actual': {'on': True, 'mode': 'regular', 'amount_krw': 2_000_000,
                                                                     'day': 1, 'time': '10:00'}}})


def test_dca_deposits_once_per_period_when_its_quotes_fail_and_it_is_retried(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_on(tmp_path)
    for minute in (0, 5, 10):  # run() retries a failed step every five minutes
        with pytest.raises(ValueError):
            L.dca(tmp_path, DownRead(), now.replace(minute=minute))
    assert L.dca(tmp_path, Read(), now.replace(minute=15)) == 'dca_traded'
    assert account_value(tmp_path) == pytest.approx(L.CAPITAL + 2_000_000, abs=1_000)  # one contribution, less fees
    assert L.read_state(tmp_path)['dca:actual']['contributed_krw'] == 2_000_000


def test_dca_deposits_and_counts_the_contribution_once_when_its_record_fails(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_on(tmp_path)
    fail_first_log(monkeypatch)
    with pytest.raises(Exception):
        L.dca(tmp_path, Read(), now)
    L.dca(tmp_path, Read(), now.replace(minute=5))
    assert account_value(tmp_path) == pytest.approx(L.CAPITAL + 2_000_000, abs=1_000)
    assert L.read_state(tmp_path)['dca:actual']['contributed_krw'] == 2_000_000


def test_the_days_orders_go_out_once_when_the_record_fails_and_trade_is_retried(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    fail_first_log(monkeypatch)
    with pytest.raises(Exception):
        L.trade(tmp_path, Read(), now, execute=True)
    L.trade(tmp_path, Read(), now.replace(minute=45), execute=True)
    bought = L.CAPITAL - L.broker_for(tmp_path, Read()).cash()
    assert 0 < bought <= 25_000_000 * (1 + L.FEE)  # one day's buy cap, not two


class NestedRead(Read):
    """A second run starts while this one is between its once-a-day check and its orders."""
    def __init__(self, other):
        super().__init__()
        self.other = other

    def call_tool(self, tool, params):
        other, self.other = self.other, None
        if other:
            other()
        return super().call_tool(tool, params)


def test_two_overlapping_trade_runs_send_the_days_orders_once(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    assert L.trade(tmp_path, NestedRead(lambda: L.trade(tmp_path, Read(), now, execute=True)), now, execute=True) == 'already_traded'
    bought = L.CAPITAL - L.broker_for(tmp_path, Read()).cash()
    assert 0 < bought <= 25_000_000 * (1 + L.FEE)


@pytest.mark.parametrize('krw', [5_000_000, 4_999_999])
def test_a_buy_paid_by_a_fee_reduced_sell_is_not_refused(tmp_path, krw):
    from virtual_paper_broker import VirtualBroker
    broker = VirtualBroker(tmp_path / 'v.sqlite3', Read(), cash=5_000_751, fee=L.FEE)  # 500 shares and their fee, 1 won left
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '379810', 'orr_qty': 500, 'orr_pr': 10_000}})
    quotes = {k: (10_000, 10_000) for k in L.ASSETS}
    sent, error = L.send_orders(broker, {'NDX': -krw, 'SPX': krw}, quotes, broker.holdings(), True)
    assert error is None and [(o['side'], o['status']) for o in sent] == [('SELL', 'ok'), ('BUY', 'ok')]
    assert broker.holdings()['379800'][0] > 0 and broker.cash() >= 0


def test_a_monthly_band_retries_what_is_still_off_target_the_next_day():
    want = {k: a[3] for k, a in L.ASSETS.items()}
    drift = {**want, 'NDX': 0.22, 'RES': 0.23}
    s = {'rebalance_month': '2026-11', 'last_want': want}
    assert L.trade_targets(want, drift, s, ['dalio_rebalance'], '2026-12-01', invested=True)[1] == 'monthly_band'
    target, _ = L.trade_targets(want, drift, s, ['dalio_rebalance'], '2026-12-02', invested=True)  # the buy was refused
    assert target is not None and target['NDX'] == pytest.approx(0.30)
    assert L.trade_targets(want, want, s, ['dalio_rebalance'], '2026-12-03', invested=True)[1] == 'hold'


def test_a_missing_satellite_proxy_keeps_its_trend_cut():
    mods = ['bogle_core', 'williams_trend']
    below = {k: {'gap': -0.05, 'slope': 0.0, 'below_days': 6} for k in L.SATS}
    want, s, _ = L.targets(ind(sats=below), mods, 'bull', {}, {}, 'd1')
    assert want['SEMI'] == pytest.approx(0.05)
    want, s, _ = L.targets(ind(sats={**below, 'SEMI': None}), mods, 'bull', s, {}, 'd2')
    assert want['SEMI'] == pytest.approx(0.05) and 'SEMI' in s['trend_cut']


class FallingRead(Read):
    """The bid moves down between the plan's quote and the fill: limit sells rest and are cancelled."""
    def call_tool(self, tool, params):
        self.price -= 50
        return super().call_tool(tool, params)


def test_an_unfilled_sell_leaves_the_months_sell_room(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    broker = L.broker_for(tmp_path, Read())
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '379810', 'orr_qty': 6_000, 'orr_pr': 10_000}})
    L.put_state(L.connect(tmp_path), **{'book:actual': {'rebalance_month': '2026-10'}})  # NDX 60%: the band sells it
    assert L.trade(tmp_path, FallingRead(), now, execute=True) == 'traded'
    sells = [o for o in L.read_state(tmp_path)['last_plan']['orders'] if o['side'] == 'SELL']
    assert sells and all(o['filled'] == 0 for o in sells)
    assert ((L.read_state(tmp_path)['book:actual'].get('sold') or {}).get('krw') or 0) == 0


def test_contributions_reach_the_shadows_and_are_recorded_at_close(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_on(tmp_path)
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40)) == 'closed'
    latest = L.read_state(tmp_path, ('nav_latest',))['nav_latest']
    assert latest['contributed'] == 2_000_000
    rows = dict(L.connect(tmp_path).execute('SELECT book, value FROM nav').fetchall())
    assert rows[L.CONTRIBUTED] == 2_000_000
    for book in L.BOOK_LABELS:  # flat prices: every book only paid costs, the contribution is not a gain
        assert L.profit(latest[book], latest['contributed']) == pytest.approx(0, abs=50_000)


def test_every_decision_kind_the_engine_logs_has_a_label():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(L))
    kinds = {c.value for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'log'
             for c in ast.walk(node.args[1]) if isinstance(c, ast.Constant)}
    assert kinds and kinds <= set(L.DECISION_KINDS)
    assert set(L.STORY_LABELS) == {'intact', 'watch', 'broken'} and set(L.BOOK_LABELS) == {'actual', 'base', 'auto', 'dca_auto'}


# ---- review 2026-11-17 (3, second review): one execute decision, dry runs, the day's targets, unrecorded claims ----

def dca_switch(tmp_path, **cfg):
    """적립식 on the virtual book; the dashboard's 가상 체결 switch stays as it is."""
    L.save_settings(tmp_path, {'dca': {'actual': {'on': True, 'mode': 'regular', 'amount_krw': 2_000_000, 'day': 1, 'time': '10:00', **cfg}}})


def test_with_the_cli_execute_the_shadows_spend_the_contributions_they_receive(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_switch(tmp_path)  # 가상 체결 switch off: --run --execute
    assert L.dca(tmp_path, Read(), now, execute=True) == 'dca_traded'
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40), execute=True) == 'closed'
    st = L.read_state(tmp_path)
    for name in ('base', 'auto', 'dca_auto'):
        assert st[f'shadow:{name}']['dca'].get('contributed_krw') == 2_000_000  # the same 적립식 rule bought with it
    assert 'dca_auto' in st['nav_latest']  # 적립 시황 자동 (2026-11-18): the regime's profile, shadow only


def test_a_dry_trade_earlier_in_the_day_does_not_block_turning_execute_on(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    assert L.trade(tmp_path, Read(), now) == 'planned'
    L.save_settings(tmp_path, {'execute': True})  # the dashboard's 가상 체결 switch at 09:50
    assert L.trade(tmp_path, Read(), now.replace(minute=55)) == 'traded'
    assert L.broker_for(tmp_path, Read()).holdings()


def test_a_dry_dca_earlier_in_the_day_does_not_block_turning_execute_on(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_switch(tmp_path)
    assert L.dca(tmp_path, Read(), now) == 'dca_planned'
    L.save_settings(tmp_path, {'execute': True})
    assert L.dca(tmp_path, Read(), now.replace(minute=30)) == 'dca_traded'
    assert L.read_state(tmp_path)['dca:actual']['contributed_krw'] == 2_000_000


def test_the_day_loop_trades_once_execute_is_turned_on_during_the_day(tmp_path, monkeypatch):
    start = decided(tmp_path, monkeypatch)
    times = iter([start.replace(hour=h, minute=m) for h, m in ((9, 40), (10, 0), (10, 1), (14, 31), (15, 40))])
    clock = {'now': start}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now']

    def sleep(seconds):
        clock['now'] = next(times)
        if clock['now'].strftime('%H:%M') == '10:00':
            L.save_settings(tmp_path, {'execute': True})  # the dashboard switch, after the 09:40 dry plan
    monkeypatch.setattr(L, 'datetime', Clock)
    monkeypatch.setattr(L, 'time', type('T', (), {'sleep': staticmethod(sleep)}))
    L.run(tmp_path, Read(), False)
    assert L.broker_for(tmp_path, Read()).holdings()  # bought the same day
    assert L.read_state(tmp_path)['nav_latest']['day'] == DAY.isoformat()


def test_band_and_pullback_levels_follow_the_days_target_not_the_configured_weight(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    broker = L.broker_for(tmp_path, Read())
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '0023A0', 'orr_qty': 300, 'orr_pr': 10_000}})
    watch = json.dumps({'market_view': '', 'stories': {k: {'status': 'watch' if k == 'QUANT' else 'intact', 'tilt': 0, 'reason': 'r'}
                                                       for k in L.SATS}})
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: watch)
    levels = [x for x in L.read_state(tmp_path)['plan']['levels'] if x['asset'] == 'QUANT']
    band = {x['side']: x for x in levels if x['master'] == 'dalio_rebalance'}
    cur, qty = fake_fetch('0023A0.KS')[max(fake_fetch('0023A0.KS'))], 300
    rest = L.CAPITAL - qty * cur
    # 주의 halves QUANT's 8% to 4% today: the band is 4% ± 5%, so no buy edge and a sell edge at 9%
    assert set(band) == {'sell'} and qty * band['sell']['price'] / (rest + qty * band['sell']['price']) == pytest.approx(0.09, rel=1e-3)
    pull = next(x for x in levels if x['case'] == 'pullback50')
    assert pull['size']['weight'] == pytest.approx((0.04 - qty * cur / L.CAPITAL) / 2, abs=1e-4)


def skips(root):
    return [r[0] for r in L.connect(root).execute("SELECT reason FROM decisions WHERE kind='skip' ORDER BY rowid")]


def test_a_trade_run_killed_between_its_claim_and_its_record_is_reported_once(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    L.claim(L.connect(tmp_path), f'trade:{DAY.isoformat()}', now)  # a run took today's orders and died before recording
    assert L.trade(tmp_path, Read(), now.replace(minute=45), execute=True) == 'already_traded'
    assert L.trade(tmp_path, Read(), now.replace(minute=50), execute=True) == 'already_traded'
    assert len(skips(tmp_path)) == 1 and '기록' in skips(tmp_path)[0]


def test_a_dca_check_without_orders_also_takes_the_days_claim(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True})
    dca_switch(tmp_path, mode='dip')  # the contribution waits for a drawdown step: no order today
    L.claim(L.connect(tmp_path), f'dca:{DAY.isoformat()}', now)  # another run holds today's 적립식
    assert L.dca(tmp_path, Read(), now) == 'dca_checked'
    assert not (L.read_state(tmp_path).get('dca:actual') or {}).get('contributed_krw')  # not counted a second time
    assert L.dca(tmp_path, Read(), now.replace(minute=5)) == 'dca_checked' and len(skips(tmp_path)) == 1


def test_the_krx_holiday_table_is_asked_once_per_year_per_process(monkeypatch):
    import sys
    from types import SimpleNamespace
    asked = []
    monkeypatch.setitem(sys.modules, 'kr_swing_v5_daily_data',
                        SimpleNamespace(krx_holidays=lambda years: asked.append(years) or {date(2026, 12, 25)}))
    monkeypatch.setattr(L, '_HOLIDAYS', {}, raising=False)
    assert [L.trading_day(d) for d in (date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 28))] == [True, False, True]
    assert asked == [{2026}]


def test_a_failed_krx_holiday_read_is_not_asked_again_for_ten_minutes(monkeypatch):
    # review4 D-9: while KRX was down every trading_day call waited on the 15-second read again (the audit asks day by day)
    import sys
    from types import SimpleNamespace
    asked, clock = [], [1_000.0]
    def down(years):
        asked.append(years)
        raise OSError('KRX down')
    monkeypatch.setitem(sys.modules, 'kr_swing_v5_daily_data', SimpleNamespace(krx_holidays=down))
    monkeypatch.setattr(L, '_HOLIDAYS', {}, raising=False)
    monkeypatch.setattr(L, '_HOLIDAY_FAILED', {}, raising=False)
    monkeypatch.setattr(L.time, 'monotonic', lambda: clock[0])
    assert [L.trading_day(date(2026, 12, d)) for d in (24, 25, 28)] == [True, True, True]  # unknown: weekdays count
    assert asked == [{2026}]
    clock[0] += 601
    monkeypatch.setitem(sys.modules, 'kr_swing_v5_daily_data', SimpleNamespace(krx_holidays=lambda years: asked.append(years) or {date(2026, 12, 25)}))
    assert L.trading_day(date(2026, 12, 25)) is False and asked == [{2026}, {2026}]


def test_the_days_buys_never_spend_the_money_waiting_for_a_drawdown_step(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    broker = L.broker_for(tmp_path, Read())
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '379810', 'orr_qty': 6_000, 'orr_pr': 10_000}})
    waiting = broker.cash()  # every won not in NDX waits for a 적립식 drawdown step
    L.put_state(L.connect(tmp_path), **{'book:actual': {'rebalance_month': '2026-10'}, 'dca:actual': {'waiting_krw': waiting}})
    assert L.trade(tmp_path, FallingRead(), now, execute=True) == 'traded'  # the band's sell rests unfilled, its buys do not
    assert L.broker_for(tmp_path, Read()).cash() >= waiting


# ---- user approval 2026-11-18 (docs/longterm-account-settings-plan.md 2-A, 4-B②): 관심목록 sleeve and carried 1주 미만 몫 ----

def sleeve_on(tmp_path, picks=('NDX', '005930'), **cfg):
    import longterm_watchlist as W
    W.save(tmp_path, {'005930': '삼성전자'})
    L.save_settings(tmp_path, {'execute': True, 'dca': {'actual': {'on': True, 'mode': 'regular', 'amount_krw': 2_000_000, 'day': 1,
                                                                     'time': '10:00', 'pick_on': True, 'picks': list(picks), **cfg}}})


def test_the_sleeve_is_bought_by_the_dca_counted_in_the_nav_and_never_sold_by_the_engine(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    sleeve_on(tmp_path)
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'
    held = L.broker_for(tmp_path, Read()).holdings()
    assert held['005930'][0] == held['379810'][0] == 99  # 1,000,000 each at 10,000 (the fee leaves a share short)
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40)) == 'closed'
    latest = L.read_state(tmp_path, ('nav_latest',))['nav_latest']
    assert latest['sleeve'] == 990_000  # 99 shares at the 10,000 close
    assert L.profit(latest['actual'], latest['contributed']) == pytest.approx(0, abs=1_000)  # the sleeve is in the NAV
    assert L.book_holdings(tmp_path, sleeve=True) == {'005930': (99, 10_000)} and '005930' not in str(L.book_holdings(tmp_path))
    L.put_state(L.connect(tmp_path), plan={**L.read_state(tmp_path)['plan'], 'day': '2026-11-19'}, traded_day=None)
    assert L.trade(tmp_path, Read(), now.replace(day=19, hour=9, minute=40), execute=True) == 'traded'
    last = L.read_state(tmp_path)['last_plan']
    assert all(o['code'] != '005930' for o in last['orders']) and L.broker_for(tmp_path, Read()).holdings()['005930'][0] == 99
    assert last['nav'] < latest['actual'] - 900_000  # the engine's NAV leaves the sleeve out: it never rebalances it


def test_the_shadows_mirror_the_sleeve_so_the_books_compare(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    sleeve_on(tmp_path)
    L.dca(tmp_path, Read(), now)
    L.close(tmp_path, Read(), now.replace(hour=15, minute=40))
    st = L.read_state(tmp_path)
    for name in ('base', 'auto'):  # the book's own (mine, at once) way: the whole contribution, sleeve included
        assert st[f'shadow:{name}']['book']['qty'].get('005930') == pytest.approx(99, rel=0.02)
    assert 0 < st['shadow:dca_auto']['book']['qty'].get('005930', 0) < 99  # 시황 자동 = the master's small part (v3)
    for book in L.BOOK_LABELS:  # flat prices: every book paid only costs, sleeve included
        assert L.profit(st['nav_latest'][book], st['nav_latest']['contributed']) == pytest.approx(0, abs=50_000)


def test_a_part_under_one_share_is_carried_on_the_virtual_book_and_bought_next_time(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True, 'dca': {'actual': {'on': True, 'mode': 'regular', 'amount_krw': 30_000, 'day': 1,
                                                                     'time': '10:00', 'pick_on': True, 'picks': ['NDX', 'QUANT']}}})
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'  # NDX 23,684 buys 2 shares; QUANT 6,316 is under one 10,000 share
    state = L.read_state(tmp_path)['dca:actual']
    assert state['carry']['QUANT'] == pytest.approx(30_000 * 0.08 / 0.38) and state['contributed_krw'] == 30_000
    assert state['carry']['NDX'] == pytest.approx(30_000 * 0.30 / 0.38 - L.longterm_dca.share_cost(10_000, 2), abs=1)  # over whole shares
    assert L.broker_for(tmp_path, Read()).cash() >= L.longterm_dca.reserved(state)
    L.put_state(L.connect(tmp_path), plan={**L.read_state(tmp_path)['plan'], 'day': '2026-12-18'})
    assert L.dca(tmp_path, Read(), now.replace(month=12)) == 'dca_traded'
    assert L.broker_for(tmp_path, Read()).holdings()['0023A0'][0] == 1  # 6,316 carried + 6,316 = one share
    assert L.read_state(tmp_path)['dca:actual']['carry']['QUANT'] < 10_000  # only the part over that one share is carried again


def test_the_days_buys_never_spend_carried_money(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    broker = L.broker_for(tmp_path, Read())
    broker.call_tool('call_api', {'operationId': 'krstockOrderCashBuy', 'input': {'iem_cd': '379810', 'orr_qty': 6_000, 'orr_pr': 10_000}})
    carried = broker.cash()
    L.put_state(L.connect(tmp_path), **{'book:actual': {'rebalance_month': '2026-10'}, 'dca:actual': {'carry': {'QUANT': carried}}})
    assert L.trade(tmp_path, FallingRead(), now, execute=True) == 'traded'
    assert L.broker_for(tmp_path, Read()).cash() >= carried


# ---- user 2026-11-18 (docs/longterm-dca-timing-plan.md 7): 거장 판단 on the virtual book and its shadows ----

def test_the_master_on_the_virtual_book_buys_a_small_part_once_a_week_and_the_shadows_follow(tmp_path, monkeypatch):
    # v3 (user approval 2026-11-18): the master's size = the 적립금 pace x 0.7 x its regime multiplier, on its day
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True, 'dca': {'actual': {'on': True, 'profile': 'yeo', 'when_by': 'master', 'size_by': 'master',
                                                                     'buy_every': 'week', 'amount_krw': 2_000_000, 'day': 1, 'time': '10:00'}}})
    db = L.connect(tmp_path)
    plan = {**L.get_state(db)['plan'], 'regime': 'bear', 'indicators': {**L.get_state(db)['plan']['indicators'], 'day_ret': -0.01}}
    L.put_state(db, plan=plan)
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'
    base = 2_000_000 * 0.7 / 4.35
    state = L.read_state(tmp_path)['dca:actual']
    assert state['contributed_krw'] == 2_000_000 and state['last_buy'] == '2026-11-18'
    assert state['waiting_krw'] == pytest.approx(2_000_000 - base * 1.4)  # 여운봉 in a bear market: x1.4
    reason = db.execute("SELECT reason FROM decisions WHERE kind='dca' ORDER BY rowid DESC").fetchone()[0]
    assert '여운봉 · 약세' in reason and '× 1.4배' in reason
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40)) == 'closed'
    st = L.read_state(tmp_path)
    assert st['shadow:base']['dca']['waiting_krw'] == pytest.approx(2_000_000 - base * 1.4)  # the book's own master
    assert st['shadow:dca_auto']['dca']['waiting_krw'] == pytest.approx(2_000_000 - base)  # 시황 자동 in a bear market = 보글·버핏 x1.0
    L.put_state(db, plan={**plan, 'day': '2026-11-19'}, closed_day=None)
    assert L.dca(tmp_path, Read(), now.replace(day=19)) == 'dca_checked'  # Thursday: the week's buy is done
    reason = db.execute("SELECT reason FROM decisions WHERE kind='dca' ORDER BY rowid DESC").fetchone()[0]
    assert '오늘은 사지 않음' in reason
    assert L.read_state(tmp_path)['dca:actual']['waiting_krw'] == pytest.approx(2_000_000 - base * 1.4)


def test_deploy_weeks_spreads_the_rest_of_the_initial_deployment_one_buy_per_week():
    """User 2026-11-18: the rest of the initial deployment over 12 weeks, one buy a week (0 keeps the daily cap)."""
    nav = 100_000_000
    plan = {'indicators': ind(), 'regime': 'bull', 'weights': {k: a[3] for k, a in L.ASSETS.items()},
            'tuning': {**L.DEFAULT_SETTINGS['tuning'], 'deploy_weeks': 12}}
    values = {}  # a fresh book: its first week buys (an invested one counts this week as bought)
    cash = nav - sum(values.values())
    mods = ['bogle_core', 'dalio_rebalance']
    _, _, why, _, planned, s = L.plan_orders(plan, mods, {'deploying': True}, {}, values, nav, cash, '2026-11-19')
    rest = nav - sum(values.values())  # every target, the reservoir included
    assert why == 'initial_deployment' and sum(planned.values()) == pytest.approx(rest / 12, rel=0.01)
    _, _, _, _, again, s = L.plan_orders(plan, mods, s, {}, values, nav, cash, '2026-11-20')  # same ISO week: nothing more
    assert again == {}
    _, _, _, _, week2, s = L.plan_orders(plan, mods, s, {}, values, nav, cash, '2026-11-23')  # next week: 11 weeks left
    assert sum(week2.values()) == pytest.approx(rest / 11, rel=0.01)
    old = {**plan, 'tuning': L.DEFAULT_SETTINGS['tuning']}  # 0: the daily cap as before
    _, _, _, _, fast, _ = L.plan_orders(old, mods, {'deploying': True}, {}, values, nav, cash, '2026-11-19')
    assert sum(fast.values()) == pytest.approx(0.25 * nav, rel=0.01)


def test_each_long_term_live_account_runs_its_own_strategy_as_a_shadow_at_close(tmp_path, monkeypatch):
    # user 2026-11-18: 실계좌 2 needs its own virtual run (the account plans only while live orders are off, and plans the
    # same first buys every day, so its plans cannot be summed). The account's modules on its own capital, whole shares,
    # kept apart from the engine's NAV (a 1억 basis) in account_nav.
    now = decided(tmp_path, monkeypatch)
    L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'custom', 'modules': {'yeo_reservoir': True}}}})
    accounts = {'live-2': {'budget': {'capital_krw': 500_000}}}
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40), accounts=accounts) == 'closed'
    shadow = L.read_state(tmp_path, ('account:live-2',))['account:live-2']
    assert shadow['capital'] == 500_000 and shadow['start'] == DAY.isoformat() and shadow['modules'] == ['yeo_reservoir']
    assert all(abs(q - round(q)) < 0.01 for q in shadow['book']['qty'].values())  # whole shares (within the slippage)
    rows = L.connect(tmp_path).execute("SELECT day, account, value, capital FROM account_nav WHERE account = 'live-2'").fetchall()
    assert rows == [(DAY.isoformat(), 'live-2', pytest.approx(500_000, abs=1_000), 500_000)]
    books = {b for (b,) in L.connect(tmp_path).execute('SELECT book FROM nav')}
    assert 'live-2' not in books and not any(b.startswith('account') for b in books)
    assert L.account_nav(tmp_path, 'live-2') == [(DAY.isoformat(), pytest.approx(500_000, abs=1_000), 500_000)]


def test_a_restart_after_a_window_does_not_report_a_step_that_already_ran_today(tmp_path, monkeypatch):
    # 2026-11-18 13:53: the dashboard restart after the 08:45 decision logged "decide missed" (and a Discord alert)
    start = decided(tmp_path, monkeypatch)
    L.trade(tmp_path, Read(), start.replace(hour=9, minute=40))
    times = iter([start.replace(hour=15, minute=40)])
    clock = {'now': start.replace(hour=14, minute=50)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now']

    def sleep(seconds):
        clock['now'] = next(times)
    monkeypatch.setattr(L, 'datetime', Clock)
    monkeypatch.setattr(L, 'time', type('T', (), {'sleep': staticmethod(sleep)}))
    L.run(tmp_path, Read(), False)
    missed = [r for (r,) in L.connect(tmp_path).execute("SELECT reason FROM decisions WHERE kind = 'missed'")]
    assert not any(r.startswith('decide') for r in missed)  # today's plan exists: not missed
    assert not any(r.startswith('trade') for r in missed)   # the 09:40 plan ran (a dry plan still ran)


def test_an_error_after_the_first_order_still_settles_it_so_the_dca_never_buys_it_twice(tmp_path):
    """Review 2026-11-18: an exception mid-send skipped settle(), so add_unfilled counted a filled buy as unfilled."""
    from virtual_paper_broker import VirtualBroker
    import longterm_dca as D

    class Flaky(VirtualBroker):
        def call_tool(self, tool, params):
            if params.get('operationId') == 'krstockOrderCashBuy' and params['input']['iem_cd'] == '379800':
                raise RuntimeError('database is locked')
            return super().call_tool(tool, params)
    broker = Flaky(tmp_path / 'v.sqlite3', Read(), cash=10_000_000, fee=L.FEE)
    quotes = {k: (10_000, 10_000) for k in L.ASSETS}
    sent, error = L.send_orders(broker, {'NDX': 90_000, 'SPX': 95_000}, quotes, {}, True)
    assert 'locked' in error and sent[0]['key'] == 'NDX' and sent[0]['filled'] == 8
    state, _ = D.add_unfilled({}, sent)
    assert 'NDX' not in state.get('retry', {}) and state['retry']['SPX'] > 0  # the refused one is retried, the filled one not
    unknown = [{'key': 'NDX', 'side': 'BUY', 'qty': 3, 'price': 1, 'number': 7}]  # sent, settle failed: fill unknown
    assert D.add_unfilled({}, unknown) == ({}, '')


def test_a_book_already_invested_counts_this_week_as_its_first_deploy_week():
    """Review 2026-11-18: 11-18 bought under the daily cap, so 11-19 (same week) must not buy a weekly share on top."""
    nav = 100_000_000
    plan = {'indicators': ind(), 'regime': 'bull', 'weights': {k: a[3] for k, a in L.ASSETS.items()},
            'tuning': {**L.DEFAULT_SETTINGS['tuning'], 'deploy_weeks': 12}}
    values = {'NDX': 7_500_000, 'SPX': 7_500_000}
    mods = ['bogle_core', 'dalio_rebalance']
    _, _, _, notes, planned, s = L.plan_orders(plan, mods, {'deploying': True}, {}, values, nav, nav - 15_000_000, '2026-11-19')
    assert planned == {} and any('이미' in text for _, text in notes)
    _, _, _, _, nxt, _ = L.plan_orders(plan, mods, s, {}, values, nav, nav - 15_000_000, '2026-11-23')
    assert sum(nxt.values()) == pytest.approx((nav - 15_000_000) / 11, rel=0.01)  # week 2 of 12


def test_a_book_has_no_numbers_of_its_own(tmp_path, monkeypatch):
    # user 2026-11-19: the per-account weights and numbers (S2) are gone; every book uses the engine's
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    assert 'numbers' not in L.clean_book({'numbers': {'weights': {'NDX': 1.0}}})
    L.save_settings(tmp_path, {'books': {'live-2': {'numbers': {'weights': {'NDX': 1.0}}}}})
    assert 'numbers' not in L.load_settings(tmp_path)['books']['live-2']
    assert not hasattr(L, 'book_plan')


def test_an_account_on_the_yeo_method_runs_that_rule_in_its_shadow(tmp_path, monkeypatch):
    # 실계좌 2 moved to the 여운봉 저수지 매매법 the same day: its shadow follows that rule, not an empty module set
    now = decided(tmp_path, monkeypatch)
    L.save_settings(tmp_path, {'books': {'live-2': {'strategy': 'yeo_method'}}})
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40), accounts={'live-2': {'budget': {'capital_krw': 500_000}}}) == 'closed'
    shadow = L.read_state(tmp_path, ('account:live-2',))['account:live-2']
    assert shadow['last']['why'].startswith('yeo_') and shadow['strategy'] == L.book_strategy_label(L.load_settings(tmp_path), 'live-2')


# ---- S3 (docs/longterm-strategy-settings-redesign.md, user 2026-11-18): each master's own numbers as settings ----

def test_module_params_default_to_the_researched_numbers_and_stay_in_order(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    defaults = L.clean_params(None)
    assert defaults['yeo_reservoir'] == {'step_1': -0.20, 'step_2': -0.30, 'per_step': 0.075}
    assert defaults['williams_rebound'] == {'drop': -0.03, 'vix': 25, 'move': 0.05, 'hold': 10}
    assert L.clean_params({'yeo_reservoir': {'per_step': 0.9}})['yeo_reservoir']['per_step'] == 0.15  # nearest edge
    swapped = L.clean_params({'yeo_reservoir': {'step_1': -0.30, 'step_2': -0.20}})['yeo_reservoir']
    assert (swapped['step_1'], swapped['step_2']) == (-0.20, -0.30)  # a shallower second step: that module's defaults
    assert L.load_settings(tmp_path)['params'] == defaults
    L.save_settings(tmp_path, {'params': {'yeo_reservoir': {'per_step': 0.10}}})
    saved = L.load_settings(tmp_path)['params']
    assert saved['yeo_reservoir'] == {'step_1': -0.20, 'step_2': -0.30, 'per_step': 0.10} and saved['marks_pendulum'] == defaults['marks_pendulum']


def test_targets_read_the_module_params_and_the_defaults_change_nothing():
    deep = ind(dd=-0.25)
    old = L.targets(deep, ['bogle_core', 'yeo_reservoir'], 'correction', {}, {}, 'd')[0]
    same = L.targets(deep, ['bogle_core', 'yeo_reservoir'], 'correction', {}, {}, 'd', params=L.clean_params(None))[0]
    assert old == same
    more = L.targets(deep, ['bogle_core', 'yeo_reservoir'], 'correction', {}, {}, 'd',
                     params={'yeo_reservoir': {'per_step': 0.10}})[0]
    assert more['NDX'] - old['NDX'] == pytest.approx((0.10 - 0.075) / 2)  # one step reached, split over the two core ETFs
    rebound = ind(day_ret=-0.025, vix=26)
    assert L.targets(rebound, ['bogle_core', 'williams_rebound'], 'bull', {}, {}, 'd')[0]['NDX'] == pytest.approx(0.30)
    assert L.targets(rebound, ['bogle_core', 'williams_rebound'], 'bull', {}, {}, 'd',
                     params={'williams_rebound': {'drop': -0.02}})[0]['NDX'] == pytest.approx(0.35)


def test_the_morning_plan_carries_the_params_its_orders_use(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    L.save_settings(tmp_path, {'params': {'lynch_story': {'watch': 0.25}}})
    now = decided(tmp_path, monkeypatch)
    assert L.read_state(tmp_path)['plan']['params']['lynch_story']['watch'] == 0.25


def test_every_recipe_runs_as_a_virtual_book_at_close_for_the_comparison(tmp_path, monkeypatch):
    # S5 (docs/longterm-strategy-settings-redesign.md, user 2026-11-18): each recipe (the account presets and the 여운봉
    # 매매법) on the same 1억 and the same closes, so the recipes compare over the same days
    now = decided(tmp_path, monkeypatch)
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40), accounts={}) == 'closed'
    rows = {a: (v, c) for _, a, v, c in L.connect(tmp_path).execute('SELECT day, account, value, capital FROM account_nav')}
    assert set(rows) == {f'recipe:{k}' for k in L.RECIPES} and all(c == L.CAPITAL for _, c in rows.values())
    st = L.read_state(tmp_path)
    assert st['recipe:yeo_method']['last']['why'].startswith('yeo_')
    assert st['recipe:bogle_dalio']['modules'] == list(L.BOOK_PRESETS['bogle_dalio'][1])
    assert L.account_nav(tmp_path, 'recipe:bogle_dalio')[0][2] == L.CAPITAL


def test_the_automatic_buy_time_waits_on_a_rising_day_and_buys_at_the_last_check(tmp_path, monkeypatch):
    # user 2026-11-18: 매수 시각 자동 — the stub price is 10,000; yesterday's close 9,000 means today is up: wait till 14:20
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True, 'dca': {'actual': {'on': True, 'mode': 'regular', 'amount_krw': 2_000_000,
                                                                     'frequency': 'daily', 'time_mode': 'auto'}}})
    st = L.read_state(tmp_path)
    L.put_state(L.connect(tmp_path), plan={**st['plan'], 'indicators': {**st['plan']['indicators'], 'krx_close': {k: 9_000 for k in L.ASSETS}}})
    assert L.dca(tmp_path, Read(), now) == 'waiting'                      # 10:00, above yesterday's close
    assert L.dca(tmp_path, Read(), now.replace(minute=3)) == 'waiting'   # not a look minute: no quotes read
    assert L.dca(tmp_path, Read(), now.replace(hour=14, minute=20)) == 'dca_traded'  # the last check buys anyway


class KrxOnlyRead(Read):
    """NH answers only real KRX codes: an order key such as 'NDXMIX' is no stock code."""
    def call_tool(self, tool, params):
        if params['stock_code'] in L.yeo_method.EXTRA:
            return {'status': 'error', 'data': {}}
        return super().call_tool(tool, params)


def test_close_prices_a_shadow_holding_a_yeo_etf_by_its_code(tmp_path, monkeypatch):
    # 2026-11-20 15:40: shadow:dca_auto (여운봉 매매법) held 'NDXMIX' and the close asked NH for the key -> whole close failed
    now = decided(tmp_path, monkeypatch)
    db = L.connect(tmp_path)
    L.put_state(db, **{'shadow:dca_auto': {'book': {'cash': 1_000_000.0, 'qty': {'NDXMIX': 10}, 'cost': {'NDXMIX': 100_000.0}}}})
    assert L.close(tmp_path, KrxOnlyRead(), now.replace(hour=15, minute=40)) == 'closed'
