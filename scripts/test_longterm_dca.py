from datetime import date, datetime

import pytest

import longterm_dca as D
import vibe_longterm as L

KST = L.KST
KRX_HOLIDAYS = frozenset({date(2026, 12, 25), date(2026, 12, 31), date(2027, 1, 1)})


@pytest.fixture(autouse=True)
def krx_calendar(monkeypatch):
    """The KRX holiday table as the engine caches it, so no test asks KRX."""
    monkeypatch.setattr(L, '_HOLIDAYS', {year: KRX_HOLIDAYS for year in range(2024, 2031)}, raising=False)
WANT = {'NDX': 0.30, 'SPX': 0.30, 'SEMI': 0.10, 'QUANT': 0.08, 'SPACE': 0.07, 'RES': 0.15}
CFG = {**D.DEFAULT, 'on': True, 'amount_krw': 1_000_000, 'frequency': 'monthly', 'day': 18, 'time': '10:00'}
AT = lambda d, h=10, m=0: datetime(2026, 11, d, h, m, tzinfo=KST)


def plan(cfg, state, now, dd=-0.02, gap=0.03, regime='correction', values=None, cash=5_000_000, day_ret=0.0, vix=18.0, profile=None):
    return D.plan(cfg, state, now, {'dd': dd, 'gap': gap, 'day_ret': day_ret, 'vix': vix}, regime, WANT, values or {}, cash,
                  profile=profile)


def test_off_or_not_due_does_nothing():
    assert plan({**CFG, 'on': False}, {}, AT(18))[0] == {}
    assert plan(CFG, {}, AT(17))[0] == {}  # monthly on the 18th
    assert plan(CFG, {}, AT(18, 9, 50))[0] == {}  # before the set time


def test_clean_keeps_values_in_range():
    cfg = D.clean({'on': True, 'profile': 'weird', 'amount_krw': 5, 'day': 31, 'time': '08:00'})
    assert (cfg['style'], cfg['profile'], cfg['amount_krw'], cfg['day'], cfg['time']) == ('master', 'dalio', 10_000, 28, '09:40')
    assert D.clean({'frequency': 'weekly', 'weekday': 9})['weekday'] == 4


def test_money_that_arrived_is_spent_like_a_contribution_whatever_the_calendar():
    # a shadow book gets the virtual book's contribution on the day it arrives, not on its own calendar
    orders, state, _ = D.plan({**CFG, 'day': 28}, {}, AT(18), {'dd': -0.02, 'gap': 0.03}, 'correction', WANT, {},
                              5_000_000, arrived=700_000)
    assert round(sum(orders.values())) == 700_000 and state['contributed_krw'] == 700_000
    assert D.plan(CFG, {}, AT(18), {'dd': -0.02}, 'correction', WANT, {}, 5_000_000, arrived=0)[0] == {}


def test_weekly_contributions_come_once_per_week():
    cfg = {**CFG, 'frequency': 'weekly', 'weekday': 2}  # Wednesday
    assert not D.due(cfg, {}, AT(17))  # Tuesday 2026-11-17
    assert D.due(cfg, {}, AT(18)) and not D.due(cfg, {'last_contribution': '2026-11-18'}, AT(20))
    assert D.due(cfg, {'last_contribution': '2026-11-18'}, AT(25))
    assert D.period(cfg, AT(18)) == D.period(cfg, AT(20)) != D.period(cfg, AT(25))


# ---- review 2026-11-17 (2nd): no contribution period may pass without its contribution ----

def test_a_set_day_after_the_months_last_trading_day_contributes_on_that_last_day():
    cfg = {**CFG, 'day': 28}  # February 2027 trades last on Friday the 26th (the 27th and 28th are a weekend)
    paid = {'last_contribution': '2027-01-28'}
    at = lambda m, d: datetime(2027, m, d, 10, 0, tzinfo=KST)
    assert not D.due(cfg, paid, at(2, 25)) and D.due(cfg, paid, at(2, 26))
    done = {'last_contribution': '2027-02-26'}
    assert not D.due(cfg, done, at(3, 26)) and D.due(cfg, done, at(3, 29))  # March keeps its own day (the 28th is a Sunday)


def test_a_weekly_contribution_on_a_holiday_comes_on_the_weeks_last_trading_day():
    cfg = {**CFG, 'frequency': 'weekly', 'weekday': 4}  # Fridays
    at = lambda m, d: datetime(2026, m, d, 10, 0, tzinfo=KST)
    assert not D.due(cfg, {'last_contribution': '2026-12-11'}, at(12, 17))  # Friday the 18th trades
    paid = {'last_contribution': '2026-12-18'}
    assert not D.due(cfg, paid, at(12, 23)) and D.due(cfg, paid, at(12, 24))  # Christmas Friday: Thursday the 24th
    paid = {'last_contribution': '2026-12-24'}
    assert not D.due(cfg, paid, at(12, 29)) and D.due(cfg, paid, at(12, 30))  # 12-31 and 1-1 closed: Wednesday the 30th


def test_f1_the_monthly_band_has_nothing_to_buy_back_after_a_contribution_day():
    """F1 fixed: overheated and +100% on NDX, the 적립식 sells nothing, so the book stays on target for the band."""
    nav = 100_000_000
    values = {k: w * nav for k, w in WANT.items()}
    orders, state, _ = D.plan({**CFG, 'day': 1, 'take_profit': True}, {'last_contribution': '2026-11-01'}, AT(18),
                              {'dd': -0.01, 'gap': 0.20}, 'overheat', WANT, values, cash=0)
    assert orders == {} and state['waiting_krw'] == 0
    ind = {'as_of': '2026-11-30', 'ndx': 100.0, 'dd': -0.01, 'gap': 0.20, 'slope': 0.01, 'day_ret': 0.0, 'vix': 13.0,
           'usdkrw': 1400.0, 'sats': {k: {'gap': 0.05, 'slope': 0.01, 'below_days': 0} for k in L.SATS}}
    plan_ = {'indicators': ind, 'regime': 'bull', 'weights': WANT, 'tuning': L.DEFAULT_SETTINGS['tuning']}
    *_, planned, _ = L.plan_orders(plan_, ['bogle_core', 'dalio_rebalance', 'buffett_lowturn'], {'rebalance_month': '2026-11'},
                                   {}, values, nav, 0, '2026-12-01')
    assert planned == {}


def test_next_contribution_day_follows_the_calendar():
    assert D.next_day(D.clean({**CFG, 'day': 18}), date(2026, 11, 18)) == date(2026, 11, 18)
    assert D.next_day(D.clean({**CFG, 'day': 18}), date(2026, 11, 19)) == date(2026, 12, 18)
    assert D.next_day(D.clean({**CFG, 'day': 5}), date(2026, 12, 20)) == date(2027, 1, 5)
    assert D.next_day(D.clean({**CFG, 'frequency': 'weekly', 'weekday': 4}), date(2026, 11, 18)) == date(2026, 11, 20)


# user 2026-11-18: optional limits on what the 적립식 buys, each switched on and off on its own
PRICES = {'NDX': 24_000, 'SPX': 22_000, 'SEMI': 34_000, 'QUANT': 16_000, 'SPACE': 6_000, 'RES': 112_000}


def limited(state=None, shares=None, **limits):
    cfg = D.clean({**CFG, **limits})
    return D.plan(cfg, state or {}, AT(18), {'dd': -0.02, 'gap': 0.03, 'day_ret': 0.0, 'vix': 18.0}, 'correction', WANT, {},
                  5_000_000, shares=shares or {}, prices=PRICES)


def spent(result):
    """KRW a plan put to work per ETF: its whole-share orders plus the part over whole shares it carried (D5 2026-11-20)."""
    orders, state = result[0], result[1]
    keys = set(orders) | set(state.get('carry') or {})
    return {k: orders.get(k, 0) + (state.get('carry') or {}).get(k, 0) for k in keys}


def test_limits_are_off_by_default_and_change_nothing():
    cfg = D.clean(CFG)
    assert not any(cfg[k] for k in ('pick_on', 'max_symbols_on', 'max_per_symbol_on', 'max_total_on'))
    assert spent(limited()) == pytest.approx(plan(CFG, {}, AT(18))[0])


def test_only_the_chosen_etfs_are_bought_in_their_target_proportion():
    orders = spent(limited(pick_on=True, picks=['NDX', 'SEMI']))
    assert set(orders) == {'NDX', 'SEMI'} and round(orders['NDX'] / orders['SEMI'], 6) == 3.0
    assert round(sum(orders.values())) == 1_000_000
    assert D.clean({**CFG, 'pick_on': True, 'picks': ['NDX', 'TSLA', 'NDX']})['picks'] == ['NDX']  # unknown and repeated dropped


def test_a_symbol_count_limit_keeps_the_largest_targets():
    orders, _, _ = limited(max_symbols_on=True, max_symbols=2)
    assert set(orders) == {'NDX', 'SPX'}
    orders, _, _ = limited(pick_on=True, picks=['SEMI', 'QUANT', 'SPACE'], max_symbols_on=True, max_symbols=1)
    assert set(orders) == {'SEMI'}


def test_share_limits_cap_the_buy_and_leave_the_rest_in_cash():
    orders, state, notes = limited(shares={'NDX': 9}, max_per_symbol_on=True, max_per_symbol=10)
    assert int(orders['NDX'] // (PRICES['NDX'] * (1 + L.FEE))) == 1  # 9 held + 1 bought = the cap, sized as the order is
    assert any('주식 수 제한' in n for n in notes) and state['contributed_krw'] == 1_000_000
    orders, _, _ = limited(shares={'NDX': 3, 'SPX': 2}, max_total_on=True, max_total=6)
    assert sum(int(orders[k] // (PRICES[k] * (1 + L.FEE))) for k in orders) == 1  # 5 held of 6 in all


def test_a_share_limit_without_a_price_buys_nothing_of_that_etf():
    cfg = D.clean({**CFG, 'max_per_symbol_on': True, 'max_per_symbol': 10})
    orders, _, _ = D.plan(cfg, {}, AT(18), {'dd': -0.02}, 'correction', WANT, {}, 5_000_000, shares={}, prices={'NDX': 24_000})
    assert set(orders) == {'NDX'}


def test_a_limit_that_does_not_bind_adds_no_note():
    result = limited(max_per_symbol_on=True, max_per_symbol=10_000)
    assert spent(result) == pytest.approx(plan(CFG, {}, AT(18))[0]) and not any('주식 수 제한' in n for n in result[2])


# ---- user approval 2026-11-18 (docs/longterm-account-settings-plan.md 2-A, 4-B②): 관심목록 sleeve, 1주 미만 몫 이월 ----

SLEEVE_PRICES = {**PRICES, '005930': 50_000, '000660': 120_000}


def sleeve(picks, cash=5_000_000, state=None, prices=SLEEVE_PRICES, **cfg):
    cfg = D.clean({**CFG, 'pick_on': True, 'picks': picks, **cfg})
    return D.plan(cfg, state or {}, AT(18), {'dd': -0.02, 'gap': 0.03, 'day_ret': 0.0, 'vix': 18.0}, 'correction', WANT, {},
                  cash, shares={}, prices=prices)


def test_watchlist_codes_are_kept_as_picks_but_engine_codes_and_junk_are_not():
    cfg = D.clean({**CFG, 'pick_on': True, 'picks': ['NDX', '005930', '379810', 'abc', '005930', '0183J0', 'TSLA']})
    assert cfg['picks'] == ['NDX', '005930']  # 379810 / 0183J0 are the engine's own ETFs (pick them by key)
    assert D.sleeve(cfg) == ['005930'] and D.sleeve(D.clean({**CFG, 'picks': ['005930']})) == []  # picks count only with pick_on


def test_a_watchlist_code_gets_the_average_weight_of_the_chosen_equity_etfs():
    orders = spent(sleeve(['NDX', 'SEMI', '005930']))
    assert set(orders) == {'NDX', 'SEMI', '005930'}
    assert orders['005930'] == pytest.approx(1_000_000 * 0.20 / 0.60)  # (0.30 + 0.10) / 2 next to 0.30 and 0.10
    assert round(sum(orders.values())) == 1_000_000


def test_only_watchlist_codes_split_the_contribution_equally_and_never_take_the_waiting_money():
    orders = spent(sleeve(['005930', '000660']))
    assert orders == {'005930': pytest.approx(500_000), '000660': pytest.approx(500_000)}
    orders, state, _ = sleeve(['005930', '000660'], profile='yeo', state={'waiting_krw': 600_000, 'last_contribution': '2026-11-02'})
    assert orders == {} and state['waiting_krw'] == 600_000  # drawdown money is for the engine's equity ETFs only


def test_a_watchlist_code_without_a_price_is_not_bought_and_says_so():
    orders, state, notes = sleeve(['NDX', '005930'], prices=PRICES)
    assert set(orders) == {'NDX'} and not (state.get('carry') or {}).get('005930')
    assert any('시세' in n for n in notes)


def test_an_order_under_one_share_is_carried_and_joins_the_next_contribution():
    cfg = {**CFG, 'pick_on': True, 'picks': ['NDX', '000660']}  # 000660 at 120,000: its 500,000 half is 4 shares
    prices = {**SLEEVE_PRICES, 'NDX': 600_000}  # NDX's 500,000 is under one share
    orders, state, notes = D.plan(D.clean(cfg), {}, AT(18), {'dd': -0.02}, 'correction', WANT, {}, 5_000_000, shares={}, prices=prices)
    assert set(orders) == {'000660'} and state['carry'] == {'NDX': 500_000, '000660': pytest.approx(500_000 - D.share_cost(120_000, 4))}
    assert state['contributed_krw'] == 1_000_000 and any('이월' in n for n in notes)
    december = datetime(2026, 12, 18, 10, 0, tzinfo=KST)
    orders, state, _ = D.plan(D.clean(cfg), state, december, {'dd': -0.02}, 'correction', WANT, {}, 5_000_000, shares={}, prices=prices)
    assert orders['NDX'] == D.share_cost(600_000, 1)  # 500,000 carried + 500,000 = 1 share, the rest carried again
    assert state['carry']['NDX'] == pytest.approx(1_000_000 - D.share_cost(600_000, 1))


def test_carried_money_is_reserved_like_waiting_money():
    state = {'carry': {'SEMI': 100_000}, 'last_contribution': '2026-10-18'}
    orders, after, notes = plan(CFG, state, AT(18), cash=1_050_000)  # 950,000 free once the carry is set aside
    assert orders == {} and after.get('last_contribution') == '2026-10-18' and any('여유 자금' in n for n in notes)
    assert D.reserved(after) == 100_000 and D.reserved({'waiting_krw': 30.0, 'carry': {'NDX': 70.0}}) == 100.0


def test_the_carry_of_an_etf_no_longer_chosen_goes_back_to_cash_on_the_next_contribution():
    orders, state, notes = limited(state={'carry': {'SEMI': 100_000}, 'last_contribution': '2026-10-18'}, pick_on=True, picks=['NDX'])
    assert set(orders) == {'NDX'} and 'SEMI' not in state['carry'] and any('이월' in n and '해제' in n for n in notes)


def test_without_prices_nothing_is_carried():
    # the replay (no quotes) keeps buying fractions as before
    orders, state, _ = plan(CFG, {}, AT(18))
    assert round(sum(orders.values())) == 1_000_000 and not state.get('carry')


# ---- user 2026-11-18 (docs/longterm-dca-timing-plan.md 7): 투자 시점 · 거장 판단 with a buy frequency ----

def judged(profile='yeo', every='day', **cfg):
    return {**CFG, 'profile': profile, 'timing': 'master', 'buy_every': every, **cfg}


def test_master_timing_keeps_the_limits_and_the_carry():
    orders = spent(limited(pick_on=True, picks=['NDX', 'SEMI'], timing='master', buy_every='day'))
    assert set(orders) == {'NDX', 'SEMI'} and round(sum(orders.values())) == 1_000_000
    cfg = D.clean(judged('dalio', 'day'))
    prices = {**PRICES, 'NDX': 500_000}  # NDX's 300,000 share of a contribution is under one share
    go = lambda state, when: D.plan(cfg, state, when, {'dd': -0.02}, 'correction', WANT, {}, 5_000_000, shares={}, prices=prices)
    orders, state, _ = go({}, AT(18))
    assert 'NDX' not in orders and state['carry']['NDX'] == pytest.approx(300_000)
    orders, state, _ = go(state, AT(19))
    assert orders == {} and state['carry']['NDX'] == pytest.approx(300_000)  # nothing to buy: the carry waits for a buy
    orders, state, _ = go(state, datetime(2026, 12, 18, 10, 0, tzinfo=KST))
    assert orders['NDX'] == D.share_cost(500_000, 1)  # 300,000 carried + 300,000 = one share, the rest carried again
    assert state['carry']['NDX'] == pytest.approx(600_000 - D.share_cost(500_000, 1))


def test_a_master_profile_gets_a_grade_for_the_regime():
    import vibe_longterm as L
    assert D.profile_grade(D.AUTO['correction'], 'correction')[0] == '강력 추천'
    assert D.profile_grade('williams', 'correction')[0] == '비권장'
    assert D.profile_grade('dalio', 'correction')[0] == '추천'
    assert all(D.profile_grade(k, r)[0] in L.GRADES and D.profile_grade(k, r)[1] for k in D.PROFILES for r in L.REGIMES)


# ---- 적립식 money an order did not buy (user approval 2026-11-18, 점검 보고서 개선안 8) ----

def test_money_an_order_did_not_buy_is_kept_for_the_dca_and_bought_at_the_next_check():
    _, state, _ = plan(CFG, {}, AT(18))
    sent = [{'key': 'NDX', 'side': 'BUY', 'qty': 12, 'price': 23_820, 'filled': 0, 'cancelled': 12},
            {'key': 'SEMI', 'side': 'BUY', 'qty': 3, 'price': 33_715, 'filled': 1, 'cancelled': 2},
            {'key': 'SPACE', 'side': 'BUY', 'qty': 11, 'price': 5_925, 'filled': 11}]
    state, note = D.add_unfilled(state, sent)
    assert state['retry'] == {'NDX': pytest.approx(12 * 23_820 * (1 + L.FEE)), 'SEMI': pytest.approx(2 * 33_715 * (1 + L.FEE))}
    assert '다음 확인 때 다시' in note and D.reserved(state) >= sum(state['retry'].values())  # the engine never spends it
    orders, after, notes = plan(CFG, state, AT(19))  # the next trading day: no contribution due, the retry is bought
    assert orders['NDX'] == pytest.approx(state['retry']['NDX']) and after['retry'] == {} and any('다시 삼' in n for n in notes)
    assert D.add_unfilled({}, [{'key': 'NDX', 'side': 'BUY', 'qty': 1, 'price': 1, 'filled': 1}]) == ({}, '')


def test_a_retry_for_an_etf_the_strategy_does_not_buy_goes_back_to_the_cash():
    # 2026-11-18 v2 (docs/longterm-dca-steady-plan.md 10-3 #5): never into an ETF the book's strategy does not manage
    orders, after, notes = D.plan({**CFG, 'pick_on': True, 'picks': ['SPX']}, {'retry': {'NDX': 300_000.0}, 'last_contribution': '2026-11-18'},
                                  AT(19), {'dd': -0.02}, 'correction', WANT, {}, 5_000_000)
    assert orders == {} and after['retry'] == {} and after['waiting_krw'] == 0 and any('현금(저수지)으로' in n for n in notes)


# ---- 종목별 수량 기준 (user request 2026-11-18): each chosen ETF N shares on the set day ----

PRICES = {'NDX': 24_000, 'SPX': 22_000, 'SEMI': 34_000, 'QUANT': 15_500, 'SPACE': 6_000, 'RES': 112_000}
SHARES = {**CFG, 'size': 'shares', 'shares_n': 2, 'pick_on': True, 'picks': ['NDX', 'SPX']}


def test_clean_keeps_the_size_way_and_bounds_the_share_count():
    assert D.clean({})['size'] == 'amount' and D.clean({})['shares_n'] == 1
    assert D.clean({'size': 'shares', 'shares_n': 5000})['shares_n'] == 1000 and D.clean({'size': 'x'})['size'] == 'amount'


def test_the_contribution_is_n_shares_of_each_chosen_etf_at_the_days_price():
    assert D.contribution(SHARES, WANT, PRICES) == D.share_cost(24_000, 2) + D.share_cost(22_000, 2)
    assert int(D.share_cost(10_000, 3) // (10_000 * (1 + L.FEE))) == 3  # exactly n shares, not one short
    assert D.contribution(SHARES, WANT, {'NDX': 24_000}) is None  # a chosen ETF without a price: not today
    assert D.contribution(CFG, WANT, PRICES) == CFG['amount_krw']


def test_shares_way_short_of_money_or_a_price_stays_due_for_the_next_day():
    orders, state, notes = D.plan(SHARES, {}, AT(18), {'dd': -0.02}, 'correction', WANT, {}, 50_000, prices=PRICES)
    assert orders == {} and state.get('last_contribution') is None and any('건너뜀' in n for n in notes)
    orders, state, notes = D.plan(SHARES, {}, AT(18), {'dd': -0.02}, 'correction', WANT, {}, 5_000_000, prices={'NDX': 24_000})
    assert orders == {} and state.get('last_contribution') is None and any('시세' in n for n in notes)


# ---- 적용 설정과 미세 조정 (user request 2026-11-18): one merged rule for plan(), the views and every book ----


# ---- 고른 종목은 계좌 전략 목표 0%여도 산다 (재현 2026-11-18: 실계좌 2 '여운봉 저수지만' → 코어 목표 0%) ----

RES_ONLY = {'NDX': 0.0, 'SPX': 0.0, 'SEMI': 0.0, 'QUANT': 0.0, 'SPACE': 0.0, 'RES': 1.0}
PICKS = {**CFG, 'day': 1, 'pick_on': True, 'picks': ['NDX', 'SPX']}
DEC1 = datetime(2026, 12, 1, 10, 5, tzinfo=KST)


def test_chosen_etfs_are_bought_in_shares_even_when_the_strategy_target_is_zero():
    orders, state, notes = D.plan({**PICKS, 'size': 'shares', 'shares_n': 1}, {}, DEC1, {'dd': -0.02}, 'correction', RES_ONLY, {},
                                  5_000_000, prices=PRICES)
    assert orders == {'NDX': D.share_cost(24_000, 1), 'SPX': D.share_cost(22_000, 1)}
    assert state['last_contribution'] == '2026-12-01' and not any('시세' in n for n in notes)


def test_chosen_etfs_are_bought_by_amount_on_their_base_weights_when_the_strategy_target_is_zero():
    orders, state, notes = D.plan(PICKS, {}, DEC1, {'dd': -0.02}, 'correction', RES_ONLY, {}, 5_000_000)
    assert sum(orders.values()) == pytest.approx(1_000_000) and orders['NDX'] == pytest.approx(orders['SPX'])  # 30 : 30
    assert state['contributed_krw'] == 1_000_000


def test_nothing_to_buy_is_not_recorded_as_bought_and_a_missing_price_is_said_only_then():
    nothing = {**CFG, 'day': 1, 'pick_on': True, 'picks': []}
    orders, state, notes = D.plan(nothing, {}, DEC1, {'dd': -0.02}, 'correction', WANT, {}, 5_000_000)
    assert orders == {} and state.get('contributed_krw', 0) == 0 and 'last_contribution' not in state
    assert any('적립할 종목이 없어' in n for n in notes) and not any('매수' in n and '지금' in n for n in notes)
    assert D.contribution(nothing, WANT, PRICES) is None
    orders, state, notes = D.plan({**PICKS, 'size': 'shares'}, {}, DEC1, {'dd': -0.02}, 'correction', RES_ONLY, {}, 5_000_000,
                                  prices={'NDX': 24_000})
    assert orders == {} and any('시세가 없는 종목' in n and 'KODEX 미국S&P500' in n for n in notes)


# ---- user 2026-11-18: each part of the 적립식 automatic (the master) or manual (mine): 매일 cycle, buy time, and my
# shares / ETFs bought when the master says buy ----

def test_a_daily_cycle_contributes_once_every_trading_day():
    cfg = {**CFG, 'frequency': 'daily'}
    orders, state, _ = plan(cfg, {}, AT(18))
    assert round(sum(orders.values())) == 1_000_000 and D.period(D.clean(cfg), AT(18)) == '2026-11-18'
    assert plan(cfg, state, AT(18, 11))[0] == {}  # once a day
    assert round(sum(plan(cfg, state, AT(19))[0].values())) == 1_000_000  # and again the next day
    assert D.next_day(D.clean(cfg), date(2026, 11, 21)) == date(2026, 11, 23)  # Saturday: the next weekday


def test_the_automatic_buy_time_waits_for_a_price_at_or_under_yesterdays_close():
    cfg = D.clean({**CFG, 'time_mode': 'auto'})
    ind = {'krx_close': {'NDX': 24_000, 'SPX': 22_000}}
    want = {'NDX': 0.5, 'SPX': 0.5}
    assert D.start_time(cfg) == D.TIMES[0] and D.start_time(D.clean(CFG)) == '10:00'
    assert not D.ready(cfg, ind, want, {'NDX': 24_100, 'SPX': 22_050}, AT(18, 10))  # both up today: wait
    assert D.ready(cfg, ind, want, {'NDX': 23_990, 'SPX': 22_050}, AT(18, 10))       # one at or under: buy now
    assert D.ready(cfg, ind, want, {'NDX': 24_100, 'SPX': 22_050}, AT(18, 14, 20))   # the last check of the day: buy anyway
    assert D.ready(D.clean(CFG), ind, want, {'NDX': 99_999}, AT(18, 10))             # a set time: the time alone decides
    assert D.ready(cfg, {}, want, {'NDX': 24_100}, AT(18, 10))                        # no close known: no reason to wait


# ---- user 2026-11-18: the 적립식 keeps buying steadily. Waiting money is spent on any down day (the Nasdaq-100 fell in its
# last session, or a chosen ETF trades at or under yesterday's close), and at the latest after MAX_WAIT_DAYS; buying big
# drops belongs to the strategy (여운봉 매매법, 저수지 분할), not to the 적립식 ----


def test_a_chosen_etf_at_or_under_yesterdays_close_is_a_down_day():
    ind = {'day_ret': 0.01, 'krx_close': {'NDX': 24_000}}
    assert D.down_day(ind, {'NDX': 1.0}, {'NDX': 23_900}) and not D.down_day(ind, {'NDX': 1.0}, {'NDX': 24_100})
    assert D.down_day({'day_ret': -0.001}, {'NDX': 1.0}, None) and not D.down_day({}, {'NDX': 1.0}, None)


# ---- 적립식 v2 (user approval 2026-11-18, docs/longterm-dca-steady-plan.md 10-3): 바로 · 꾸준 · 예전 방식 ----




# ---- 적립식 v3 (user approval 2026-11-18, docs/longterm-dca-v3-plan.md 3·7-4) ----

STRATEGY = {k: v for k, v in WANT.items() if k != 'RES'}  # what the strategy buys (vibe_longterm.dca_targets: no KOFR)
WEEK_BASE = 1_000_000 * 0.7 / 4.35  # a monthly 100만 원 at the pace 0.7, one buy a week


def v3(state, now, regime='correction', day_ret=0.0, vix=18.0, cash=5_000_000, values=None, yeo=False, stories=None, **cfg):
    return D.plan({**CFG, **cfg}, state, now, {'day_ret': day_ret, 'vix': vix}, regime, STRATEGY, values or {}, cash, yeo=yeo, stories=stories)


def test_saved_settings_are_read_as_the_closest_v3_way():
    assert (D.clean({'profile': 'dalio'})['when_by'], D.clean({'profile': 'dalio'})['size_by']) == ('me', 'me')  # 정한 날 + 전액 바로
    for old in ({'profile': 'yeo'}, {'profile': 'marks'}, {'profile': 'williams'}, {'profile': 'dalio', 'timing': 'master'}):
        assert (D.clean(old)['when_by'], D.clean(old)['size_by']) == ('master', 'master')  # waiting or 거장 판단 ways go to the master
    manual = D.clean({'style': 'manual', 'mode': 'dip'})
    assert (manual['style'], manual['when_by'], manual['size_by'], manual['me_when']) == ('manual', 'me', 'me', 'dip')
    steady = D.clean({'when': 'steady', 'steady_rate': 0.004, 'steady_floor': 0.3})
    assert (steady['profile'], steady['when_by'], steady['tune']) == ('yeo', 'master', {'rate': 0.004, 'floor': 0.3})
    assert D.clean({'tune': {'now': 0.5, 'pace': {'bull': 0.5}}})['tune'] == {}  # pre-v3 tune fields have no v3 meaning
    assert D.clean({'style': 'manual', 'when_by': 'master', 'size_by': 'master'})['when_by'] == 'me'  # 수동 투자: every part mine


def test_mine_on_the_calendar_buys_the_whole_contribution_at_the_strategys_weights():
    orders, state, notes = v3({}, AT(18))
    assert round(sum(orders.values())) == 1_000_000 and 'RES' not in orders and state['waiting_krw'] == 0
    assert orders['NDX'] == pytest.approx(1_000_000 * 0.30 / 0.85)
    assert v3(state, AT(18, 11))[0] == {} and v3(state, AT(19))[0] == {}  # once, on the day
    assert v3({}, AT(18), cash=500_000)[0] == {}  # short of money: stays due for the next trading day


def test_mine_down_day_first_waits_for_a_down_day_at_most_max_wait_days():
    cfg = {'me_when': 'dip'}
    orders, state, _ = v3({}, AT(18), day_ret=0.01, **cfg)
    assert orders == {} and state['waiting_krw'] == 1_000_000  # an up day: the contribution waits
    orders, state, _ = v3(state, AT(19), day_ret=-0.004, **cfg)
    assert round(sum(orders.values())) == 1_000_000 and state['waiting_krw'] == 0  # the first down day buys it all
    _, state, _ = v3({}, AT(18), day_ret=0.01, **cfg)
    assert v3(state, AT(24), day_ret=0.01, **cfg)[0] == {}  # 6 days: still waiting
    assert round(sum(v3(state, AT(25), day_ret=0.01, **cfg)[0].values())) == 1_000_000  # 7 days: bought anyway (steady)


def test_the_master_buys_a_small_part_of_the_pool_once_a_week_scaled_by_the_regime():
    cfg = {'profile': 'dalio', 'when_by': 'master', 'size_by': 'master'}
    orders, state, notes = v3({}, AT(18), regime='bull', **cfg)
    assert sum(orders.values()) == pytest.approx(WEEK_BASE) and state['waiting_krw'] == pytest.approx(1_000_000 - WEEK_BASE)
    assert v3(state, AT(19), regime='bull', **cfg)[0] == {}  # once per ISO week
    hot, _, _ = v3({}, AT(18), regime='overheat', **cfg)
    assert sum(hot.values()) == pytest.approx(WEEK_BASE * 0.9)  # 달리오: overheat 0.9 (never 0, never all)
    late = {'waiting_krw': 1_500_000.0, 'contributed_krw': 1_500_000.0, 'last_contribution': '2026-11-18', 'last_check': '2026-11-19'}
    orders, _, _ = v3(late, AT(20), regime='bull', **cfg)  # 50만 beyond one contribution and today's base: an eighth of it more
    assert sum(orders.values()) == pytest.approx(WEEK_BASE + (1_500_000 - WEEK_BASE - 1_000_000) / 8)
    assert any('기본' in n and '배' in n for n in notes)


def test_a_dip_master_waits_for_a_down_day_and_buys_more_in_a_weak_market():
    cfg = {'profile': 'marks', 'when_by': 'master', 'size_by': 'master'}
    orders, state, _ = v3({}, AT(18), regime='bear', day_ret=0.01, **cfg)
    assert orders == {} and state['waiting_krw'] == 1_000_000  # 막스: not on an up day
    orders, state, _ = v3(state, AT(19), regime='bear', day_ret=-0.01, **cfg)
    assert sum(orders.values()) == pytest.approx(WEEK_BASE * 1.3)  # bear x1.3
    waited = {'waiting_krw': 900_000.0, 'last_buy': '2026-11-06', 'since': '2026-11-13', 'last_contribution': '2026-11-18'}
    assert v3(waited, AT(18), regime='bull', day_ret=0.01, **cfg)[0]  # 5 days into the wait (the 13th): bought anyway


def test_a_crash_day_boosts_williams_but_never_beyond_one_and_a_half_times_the_base():
    cfg = {'profile': 'williams', 'when_by': 'master', 'size_by': 'master'}
    orders, _, notes = v3({}, AT(18), regime='panic', day_ret=-0.04, vix=32, **cfg)
    assert sum(orders.values()) == pytest.approx(WEEK_BASE * 1.5) and any('급락일' in n for n in notes)


def test_what_a_slow_week_left_is_caught_up_an_eighth_at_a_time():
    cfg = {'profile': 'yeo', 'when_by': 'master', 'size_by': 'master'}
    yeo = dict(cash=90_000_000, values={'NDX': 10_000_000}, yeo=True)  # a 여운봉 book: its base is 0.25% of its value a week
    base = 1e8 * 0.0025
    _, state, _ = v3({}, AT(16), regime='overheat', day_ret=-0.01, **yeo, **{**cfg, 'day': 16})
    assert state['catch_krw'] == pytest.approx(base * 0.5)  # overheat x0.5 left half a base behind
    orders, state, _ = v3(state, AT(23), regime='recovery', day_ret=-0.01, **yeo, **{**cfg, 'day': 16})
    assert sum(orders.values()) == pytest.approx(base * 1.0 + base * 0.5 / 8) and state['catch_krw'] == pytest.approx(base * 0.5 * 7 / 8)


def test_a_yeo_book_spends_its_reservoir_by_its_value_and_keeps_the_masters_cash_floor():
    cfg = {'profile': 'yeo', 'when_by': 'master', 'size_by': 'master'}
    orders, _, notes = v3({}, AT(18), regime='bull', day_ret=-0.01, cash=90_000_000, values={'NDX': 10_000_000}, yeo=True, **cfg)
    assert sum(orders.values()) == pytest.approx(1e8 * 0.0025 * 0.8)  # 0.25% of the 1억 book a week, bull x0.8
    low, _, _ = v3({}, AT(18), regime='bear', day_ret=-0.01, cash=20_200_000, values={'NDX': 79_800_000}, yeo=True, **cfg)
    assert sum(low.values()) == pytest.approx(20_200_000 - 0.2 * 100_000_000, abs=1)  # 35만 wanted (x1.4), only 20만 above the 20% floor


def test_my_amount_or_my_shares_go_on_the_masters_days():
    cfg = {'profile': 'marks', 'when_by': 'master', 'size_by': 'me', 'amount_krw': 300_000}
    _, state, _ = v3({}, AT(18), regime='bull', day_ret=0.01, **cfg)
    orders, _, _ = v3(state, AT(19), regime='bull', day_ret=-0.01, **cfg)
    assert sum(orders.values()) == pytest.approx(300_000)  # my 30만 on the master's down day, from the pool
    shares = {**cfg, 'size': 'shares', 'shares_n': 2, 'pick_on': True, 'picks': ['NDX', 'SPX']}
    assert D.quantity_on_signal(D.clean({**CFG, **shares}))
    assert not D.due_today({**CFG, **shares}, {}, AT(18), {'day_ret': 0.01}, 'bull', STRATEGY, PRICES)  # up day: not the master's day
    assert D.due_today({**CFG, **shares}, {}, AT(18), {'day_ret': -0.01}, 'bull', STRATEGY, PRICES)
    orders, state, _ = D.plan({**CFG, **shares}, {}, AT(18), {'day_ret': -0.01}, 'bull', STRATEGY, {}, 5_000_000, prices=PRICES)
    assert set(orders) == {'NDX', 'SPX'} and state['contributed_krw'] == D.contribution(D.clean({**CFG, **shares}), STRATEGY, PRICES)


def test_the_regime_shadow_runs_the_regimes_master_on_the_mirrored_money():
    orders, state, notes = D.plan(CFG, {}, AT(20), {'day_ret': 0.0}, 'bear', STRATEGY, {}, 5_000_000, arrived=1_000_000, profile='auto')
    assert sum(orders.values()) == pytest.approx(WEEK_BASE) and notes and D.PROFILES[D.AUTO['bear']]['label'] in ' '.join(notes)


def test_lynch_halves_a_theme_on_watch_and_skips_a_broken_one():
    cfg = {'profile': 'lynch', 'when_by': 'me', 'size_by': 'me'}
    stories = {'SEMI': {'status': 'watch'}, 'SPACE': {'status': 'broken'}}
    plain, _, _ = v3({}, AT(18), **cfg)
    told, _, _ = v3({}, AT(18), stories=stories, **cfg)
    assert 'SPACE' not in told and told['SEMI'] / told['NDX'] == pytest.approx(plain['SEMI'] / plain['NDX'] / 2)


def test_my_tune_is_kept_to_its_ranges_and_laid_over_the_master():
    assert D.clean_tune({'rate': 0.5, 'mult': {'bear': 9, 'x': 1}, 'wait': 30, 'floor': -1, 'junk': 1}) == \
        {'rate': 0.01, 'wait': 10, 'floor': 0.05, 'mult': {'bear': 1.5}}
    r = D.rule({**CFG, 'profile': 'marks', 'tune': {'mult': {'bear': 1.5}}}, 'bear')
    assert r['mult']['bear'] == 1.5 and r['mult']['bull'] == 0.9 and r['tuned'] == ['mult']
    cfg = {'profile': 'marks', 'when_by': 'master', 'size_by': 'master', 'tune': {'mult': {'bear': 1.5}}}
    orders, _, notes = v3({}, AT(18), regime='bear', day_ret=-0.01, **cfg)
    assert sum(orders.values()) == pytest.approx(WEEK_BASE * 1.5) and any(n.startswith('내 조정') for n in notes)


def test_buy_levels_draw_the_next_buy_with_its_size_for_every_way():
    dip = D.buy_levels({**CFG, 'profile': 'marks', 'when_by': 'master', 'size_by': 'master'}, {}, 'bear', {}, 23_000, close=23_300)
    assert [x['kind'] for x in dip] == ['dip'] and dip[0]['price'] == 23_300 and '하락일' in dip[0]['label'] and '배' in dip[0]['label']
    cycle = D.buy_levels(CFG, {}, 'bull', {}, 23_000, close=23_300, today=date(2026, 11, 18))
    assert cycle[0]['kind'] == 'cycle' and cycle[0]['price'] == 23_000 and '1,000,000원' in cycle[0]['label'] and '2026-11-18' in cycle[0]['label']
    crash = D.buy_levels({**CFG, 'profile': 'williams', 'when_by': 'master', 'size_by': 'master'}, {}, 'bull', {}, 23_000, close=23_300)
    assert [x['kind'] for x in crash] == ['dip', 'crash'] and crash[1]['price'] == pytest.approx(23_000 * 0.97)


def test_effective_names_who_decides_each_part_and_buys_an_example():
    eff = D.effective({**CFG, 'profile': 'marks', 'when_by': 'master', 'size_by': 'master'}, 'bear', {}, STRATEGY, PRICES)
    fields = {f['key']: f for f in eff['fields']}
    assert fields['when']['label'].startswith('언제 · 자동') and fields['size']['label'].startswith('얼마나 · 자동')
    assert fields['mult']['value'] == '1.3배' and eff['orders'] and eff['notes'][0].startswith('예시')


def test_the_dca_never_sells():
    for profile in D.PROFILES:
        for regime in D.REGIMES:
            orders, _, _ = v3({}, AT(18), regime=regime, day_ret=-0.04, vix=40, profile=profile, when_by='master', size_by='master')
            assert all(v > 0 for v in orders.values())


def test_a_small_yeo_book_still_spends_its_contributions_and_a_picked_yeo_code_is_its_key():
    y = D.clean({**CFG, 'profile': 'yeo', 'when_by': 'master', 'size_by': 'master', 'amount_krw': 100_000})
    assert D.base_amount(y, D.rule(y), 'week', 500_000, True) == pytest.approx(100_000 * 0.7 / 4.35)  # 50만 x 0.25% is smaller
    assert D.base_amount(y, D.rule(y), 'week', 1e8, True) == pytest.approx(250_000)
    picked = D.clean({**CFG, 'pick_on': True, 'picks': ['NDX', '0261H0']})
    assert D.universe(picked, {'NDX': 0.7, 'NDXMIX': 0.3}) == {'NDX': 0.7, 'NDXMIX': 0.3} and D.sleeve(picked) == []


# ---- review 2026-11-18 (3rd, TDD): v3 defects found by the long-term review ----

def year_of_buys(cfg, regime='bull', days=365):
    """(state, KRW spent) after `days` of daily checks with 100만 원 a month, one down day in three."""
    from datetime import timedelta
    state, spent = {}, 0.0
    for i in range(days):
        now = datetime(2026, 1, 5, 10, 0, tzinfo=KST) + timedelta(days=i)
        if now.weekday() < 5:
            orders, state, _ = D.plan({**CFG, 'day': 1, **cfg}, state, now, {'day_ret': -0.01 if i % 3 == 0 else 0.01}, regime,
                                      STRATEGY, {}, 1e8)
            spent += sum(orders.values())
    return state, spent


def test_a_module_book_masters_size_never_parks_the_contributions_for_good():
    # the base is the 적립금 pace x 0.7: what that leaves waiting is caught up too, so the pool stays near two contributions
    for profile in ('bogle_buffett', 'dalio', 'marks'):
        state, spent = year_of_buys({'profile': profile, 'when_by': 'master', 'size_by': 'master'})
        assert state['waiting_krw'] < 2_500_000 and spent > 0.8 * state['contributed_krw'], profile
    for me_when in ('cycle', 'dip'):  # my day with the master's size: once per contribution, the pool bounded too
        state, _ = year_of_buys({'profile': 'dalio', 'when_by': 'me', 'size_by': 'master', 'me_when': me_when})
        assert state['waiting_krw'] < 3_500_000, me_when
    weak, _ = year_of_buys({'profile': 'marks', 'when_by': 'master', 'size_by': 'master'}, 'bear')
    strong, _ = year_of_buys({'profile': 'marks', 'when_by': 'master', 'size_by': 'master'}, 'bull')
    assert weak['waiting_krw'] < strong['waiting_krw']  # the regime still decides how fast it goes in


def test_a_dip_masters_wait_starts_with_the_new_period_not_its_last_buy():
    cfg = {'profile': 'marks', 'when_by': 'master', 'size_by': 'master'}
    bought = {'waiting_krw': 800_000.0, 'last_buy': '2026-11-13', 'last_contribution': '2026-11-02', 'last_check': '2026-11-13'}
    orders, state, _ = v3(bought, AT(16), regime='bull', day_ret=0.01, **cfg)  # Monday of a new week, an up day
    assert orders == {} and state['since'] == '2026-11-16'
    assert v3(state, AT(20), regime='bull', day_ret=0.01, **cfg)[0] == {}  # Friday: 4 days into the wait
    monthly = {**cfg, 'buy_every': 'month'}
    orders, state, _ = v3({**bought, 'last_buy': '2026-10-30'}, AT(2), regime='bull', day_ret=0.01, **monthly)
    assert orders == {} and state['since'] == '2026-11-02'  # the first check of November waits for a down day


def test_my_down_day_with_the_masters_size_buys_once_per_contribution():
    cfg = {'profile': 'dalio', 'when_by': 'me', 'size_by': 'master', 'me_when': 'dip'}
    orders, state, _ = v3({}, AT(18), day_ret=-0.01, regime='bull', **cfg)
    assert sum(orders.values()) > 0 and 'since' not in state and state['waiting_krw'] > 1_000
    for d in (19, 20, 23, 24, 25, 26, 27):  # a week of down days and up days: the rest waits for the next contribution
        orders, state, _ = v3(state, AT(d), day_ret=-0.01, regime='bull', **cfg)
        assert orders == {}, d


def test_picked_yeo_etfs_count_only_with_pick_on():
    off = D.clean({**CFG, 'pick_on': False, 'picks': ['NDX', '0261H0']})
    assert D.universe(off, {'NDX': 0.4, 'SPX': 0.2}) == {'NDX': 0.4, 'SPX': 0.2}


def test_shares_are_always_my_size_even_from_an_old_master_setting():
    cfg = D.clean({'profile': 'marks', 'size': 'shares', 'shares_n': 2})
    assert (cfg['when_by'], cfg['size_by']) == ('master', 'me') and D.quantity_on_signal(cfg)


def test_my_shares_with_down_day_first_wait_for_a_down_day():
    cfg = {'size': 'shares', 'shares_n': 1, 'me_when': 'dip', 'pick_on': True, 'picks': ['NDX']}
    run = lambda state, d, ret: D.plan({**CFG, **cfg}, state, AT(d), {'day_ret': ret}, 'bull', STRATEGY, {}, 5e6, prices=PRICES)  # noqa: E731
    orders, state, _ = run({}, 18, 0.01)
    assert orders == {} and state['since'] == '2026-11-18'  # due, but an up day
    orders, state, _ = run(state, 19, -0.01)
    assert set(orders) == {'NDX'} and state['last_contribution'] == '2026-11-19'
    _, waited, _ = run({}, 18, 0.01)
    assert run(waited, 25, 0.01)[0]  # 7 days: bought anyway


def test_lynchs_broken_theme_is_left_out_of_the_shares_contribution_too():
    cfg = D.clean({**CFG, 'profile': 'lynch', 'size': 'shares', 'shares_n': 1})
    broken = {'SPACE': {'status': 'broken'}}
    paid = D.contribution(cfg, STRATEGY, PRICES, broken)
    orders, state, _ = D.plan(cfg, {}, AT(18), {}, 'bull', STRATEGY, {}, 5e6, prices=PRICES, stories=broken)
    assert 'SPACE' not in orders and state['contributed_krw'] == paid == D.contribution(cfg, {k: v for k, v in STRATEGY.items() if k != 'SPACE'}, PRICES)


def test_an_unfilled_buy_names_a_yeo_etf_by_its_name():
    _, note = D.add_unfilled({}, [{'key': 'NDXMIX', 'side': 'BUY', 'qty': 2, 'filled': 0, 'price': 10_000}])
    assert 'NDXMIX' not in note and L.asset_name('NDXMIX') in note


def test_a_yeo_book_at_its_cash_floor_does_not_pile_up_catch_up_day_after_day():
    cfg = {'profile': 'yeo', 'when_by': 'master', 'size_by': 'master'}
    state = {}
    for d in (16, 17, 18):  # every day the 20% floor leaves nothing to spend
        orders, state, _ = v3(state, AT(d), regime='overheat', day_ret=-0.01, cash=20_000_000, values={'NDX': 80_000_000}, yeo=True, **cfg)
        assert orders == {}
    assert state['catch_krw'] == 0


# ---- 수량 기준 + 얼마나 · 거장 (user 2026-11-19): the master decides how many shares too ----

def test_shares_with_the_masters_size_keeps_the_master():
    cfg = D.clean({'size': 'shares', 'shares_n': 1, 'when_by': 'master', 'size_by': 'master', 'profile': 'yeo'})
    assert cfg['size_by'] == 'master' and not D.quantity_on_signal(cfg)


def test_the_master_sizes_a_shares_book_in_whole_shares_not_my_one_share():
    cfg = {**CFG, 'size': 'shares', 'shares_n': 1, 'size_by': 'master', 'when_by': 'me', 'profile': 'yeo',
           'pick_on': True, 'picks': ['NDX', 'SPX']}
    prices = {'NDX': 24_000, 'SPX': 22_000}
    orders, state, notes = D.plan(cfg, {}, AT(18), {'dd': -0.02, 'day_ret': -0.01}, 'correction', WANT, {}, 5_000_000,
                                  prices=prices)
    put = sum(orders.values()) + sum(state['carry'].values())  # whole shares + the part over them carried
    # the master's amount (1,000,000 a month x 0.7, one monthly buy, x 1.2 in a correction), not 1 share of each (46,000)
    assert put == pytest.approx(1_000_000 * D.PACE * 1.2, rel=0.01)
    assert {k: int(v // (prices[k] * (1 + L.FEE))) for k, v in orders.items()} == {'NDX': 17, 'SPX': 19}
    assert any('주' in n and '나스닥100' in n for n in notes if '이번 수량' in n)  # the shares it buys are shown


def test_the_deposit_of_a_shares_book_sized_by_the_master_is_the_contribution_it_counts():
    """Review 2026-11-19: with 얼마나 · 거장 the contribution is 들어오는 적립금 (amount_krw), the amount plan() adds to
    the 적립식 money, so the virtual deposit must be that too (not N shares at today's price, ~3만 원 against 100만)."""
    cfg = {**CFG, 'size': 'shares', 'shares_n': 1, 'size_by': 'master', 'when_by': 'me', 'profile': 'yeo'}
    prices = {'NDX': 24_000, 'SPX': 22_000}
    assert D.contribution(cfg, {'NDX': 0.5, 'SPX': 0.5}, prices) == 1_000_000
    mine = {**cfg, 'size_by': 'me'}
    assert D.contribution(mine, {'NDX': 0.5, 'SPX': 0.5}, prices) == D.share_cost(24_000, 1) + D.share_cost(22_000, 1)
    _, state, _ = D.plan(cfg, {}, AT(18), {'dd': -0.02, 'day_ret': -0.01}, 'correction', {'NDX': 0.5, 'SPX': 0.5}, {}, 5_000_000,
                         prices=prices)
    assert state['contributed_krw'] == D.contribution(cfg, {'NDX': 0.5, 'SPX': 0.5}, prices)


def test_the_masters_share_count_note_is_what_is_bought_after_the_limits():
    """The '이번 수량(거장)' note counts the final orders (share limits and the carry under one share applied)."""
    cfg = {**CFG, 'size': 'shares', 'shares_n': 1, 'size_by': 'master', 'when_by': 'me', 'profile': 'yeo',
           'pick_on': True, 'picks': ['NDX', 'SPX'], 'max_per_symbol_on': True, 'max_per_symbol': 5}
    prices = {'NDX': 24_000, 'SPX': 22_000}
    orders, _, notes = D.plan(cfg, {}, AT(18), {'dd': -0.02, 'day_ret': -0.01}, 'correction', WANT, {}, 5_000_000, prices=prices)
    note = next(n for n in notes if n.startswith('이번 수량'))
    assert '5주' in note and '17주' not in note and '19주' not in note


# 입금할 때만 사는 적립식 (user approval 2026-11-20, docs/longterm-dca-deposit-plan.md D1~D5)
YEO_WANT = {'NDX': 0.7, 'NDXMIX': 0.3}
DEP = {**D.DEFAULT, 'on': True, 'style': 'master', 'profile': 'yeo', 'when_by': 'master', 'size_by': 'master',
       'frequency': 'deposit', 'buy_every': 'day', 'size': 'shares', 'time_mode': 'fixed', 'time': '10:00'}


def test_deposit_frequency_is_kept_with_its_spread_and_share():
    cfg = D.clean({**DEP, 'spread_weeks': 99, 'deposit_pct': 5})
    assert (cfg['frequency'], cfg['spread_weeks'], cfg['deposit_pct']) == ('deposit', 26, 10)
    assert (D.clean(DEP)['spread_weeks'], D.clean(DEP)['deposit_pct']) == (4, 100)
    assert D.clean({**DEP, 'size_by': 'me', 'size': 'shares'})['size_by'] == 'master'  # my N shares need a calendar


def test_first_look_sets_the_capital_seen_and_adds_nothing():
    state, note = D.take_deposit(DEP, {}, 500_000, '2026-11-20', logged=True)
    assert state['capital_seen'] == 500_000 and state.get('waiting_krw', 0) == 0
    assert '기준' in note


def test_a_raised_capital_is_the_deposit_and_is_spread():
    state, note = D.take_deposit(DEP, {'capital_seen': 500_000}, 800_000, '2026-11-23', logged=True)
    assert state['waiting_krw'] == 300_000 and state['contributed_krw'] == 300_000 and state['capital_seen'] == 800_000
    assert state['deposit_base'] == pytest.approx(300_000 / 20)  # 4 weeks x 5 trading days
    assert '300,000' in note
    half, _ = D.take_deposit({**DEP, 'deposit_pct': 50}, {'capital_seen': 500_000}, 800_000, '2026-11-23', logged=True)
    assert half['waiting_krw'] == 150_000


def test_a_lowered_capital_cuts_only_the_unspent_part():
    state, _ = D.take_deposit(DEP, {'capital_seen': 800_000, 'waiting_krw': 100_000}, 600_000, '2026-11-24', logged=True)
    assert state['waiting_krw'] == 0 and state['capital_seen'] == 600_000


def test_a_capital_change_without_history_is_not_taken():
    state, note = D.take_deposit(DEP, {'capital_seen': 500_000}, 800_000, '2026-11-23', logged=False)
    assert state.get('waiting_krw', 0) == 0 and state['capital_seen'] == 500_000 and '이력' in note


def test_no_deposit_no_buy_even_on_a_yeo_book_with_cash():
    orders, _, _ = D.plan(DEP, {'capital_seen': 500_000}, AT(20), {'day_ret': -0.01}, 'correction', YEO_WANT, {}, 500_000,
                          prices={'NDX': 23_300, 'NDXMIX': 9_750}, yeo=True)
    assert orders == {}


def test_deposit_buys_spread_never_more_than_the_deposit():
    state = {'capital_seen': 800_000, 'waiting_krw': 300_000, 'contributed_krw': 300_000, 'deposit_base': 15_000}
    orders, after, _ = D.plan(DEP, state, AT(20), {'day_ret': -0.01}, 'correction', YEO_WANT, {}, 800_000,
                              prices={'NDX': 23_300, 'NDXMIX': 9_750}, yeo=True)
    spent = sum(orders.values()) + sum(after['carry'].values())
    assert spent == pytest.approx(15_000 * 1.2, abs=2)  # base x the correction multiplier
    assert after['waiting_krw'] == pytest.approx(300_000 - 18_000, abs=2)
    small = {**state, 'waiting_krw': 5_000}
    orders, after, _ = D.plan(DEP, small, AT(20), {'day_ret': -0.01}, 'correction', YEO_WANT, {}, 800_000,
                              prices={'NDX': 23_300, 'NDXMIX': 9_750}, yeo=True)
    assert sum(orders.values()) + sum(after['carry'].values()) <= 5_000 + 1


def test_my_size_on_deposit_buys_the_whole_deposit_at_once():
    cfg = {**DEP, 'size_by': 'me', 'size': 'amount', 'when_by': 'me', 'me_when': 'cycle'}
    state = {'capital_seen': 800_000, 'waiting_krw': 300_000, 'contributed_krw': 300_000}
    orders, after, _ = D.plan(cfg, state, AT(20), {}, 'correction', YEO_WANT, {}, 800_000,
                              prices={'NDX': 23_300, 'NDXMIX': 9_750}, yeo=True)
    assert sum(orders.values()) + sum(after['carry'].values()) == pytest.approx(300_000, abs=2) and after['waiting_krw'] == 0


def test_the_part_over_whole_shares_is_carried_not_lost():
    """11-20 실계좌 2: 58,800원 for 379810 at 23,315 bought 2 shares and 12,170원 fell out of the 적립식 (D5)."""
    cfg = {**CFG, 'pick_on': True, 'picks': ['NDX'], 'amount_krw': 58_800, 'frequency': 'daily'}
    orders, after, _ = D.plan(cfg, {}, AT(20), {}, 'correction', WANT, {}, 5_000_000, prices={'NDX': 23_315})
    assert orders['NDX'] == D.share_cost(23_315, 2)
    assert after['carry']['NDX'] == pytest.approx(58_800 - D.share_cost(23_315, 2))
    assert D.reserved(after) == pytest.approx(58_800 - D.share_cost(23_315, 2))


def test_the_views_example_for_a_deposit_book_buys_one_spread_part():
    out = D.effective(DEP, 'correction', {'day_ret': -0.01}, YEO_WANT, {'NDX': 23_300, 'NDXMIX': 9_750}, cash=500_000, yeo=True)
    put = sum(out['orders'].values()) + sum((out['after'].get('carry') or {}).values())
    assert put == pytest.approx(1_000_000 / 20 * 1.2, abs=2)  # 1,000,000 sample over 4 weeks of daily buys x 1.2
    assert any('입금' in f['value'] for f in out['fields'] if f['key'] == 'size')
    assert D.next_day(DEP, date(2026, 11, 21)) == date(2026, 11, 23)
