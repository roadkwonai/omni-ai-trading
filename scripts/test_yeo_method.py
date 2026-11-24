"""여운봉 저수지 매매법 (user request 2026-11-18, docs/yeo-reservoir-method-plan.md 0절): start all in the reservoir, buy at
−20/−30/−40% from the all-time high (40/40/20% of the reservoir at step 1) as 70 : 30 risk : safe, rebalance only once held."""
import pytest

import yeo_method as Y

CFG = Y.clean({})
ATH = lambda dd: {'ndx': {'dd': dd, 'peak': 30_000.0}}  # noqa: E731


def test_defaults_are_the_users_confirmed_rule():
    assert (CFG['market'], CFG['us_index'], CFG['risk'], CFG['band']) == ('us', 'ndx', 0.70, 0.05)
    assert 'rebalance' not in CFG and 'rebalance' not in Y.clean({'rebalance': 'quarter'})  # no monthly/quarterly choice
    assert CFG['steps'] == [[-0.20, 0.40], [-0.30, 0.40], [-0.40, 0.20]]
    assert Y.clean({'risk': 2, 'band': 0.5, 'steps': [[-0.9, 3]]})['risk'] == 0.90
    assert Y.clean({'band': 0.5})['band'] == 0.10
    assert Y.clean({'steps': [[-0.9, 3]]})['steps'] == [[-0.60, 1.0]]
    assert [p[0] for p in Y.pairs(CFG)] == ['ndx'] and Y.pairs(Y.clean({'us_index': 'both'}))[1][0] == 'spx'


def test_no_buy_before_the_first_step():
    planned, state, notes = Y.plan(CFG, {}, ATH(-0.15), {}, 500_000, '2026-11-19')
    assert planned == {} and any('다음 단계' in n for n in notes)


def test_step_one_spends_forty_percent_of_the_reservoir_at_seventy_thirty():
    planned, state, notes = Y.plan(CFG, {}, ATH(-0.21), {}, 500_000, '2026-11-19')
    assert planned == {'NDX': pytest.approx(140_000), 'NDXMIX': pytest.approx(60_000)}
    assert state['ndx']['used'] == [-0.20] and state['ndx']['base'] == 500_000
    assert any('여운봉 단계 1' in n and '^NDX 전고점 대비 −21.0%' in n and '저수지 40%' in n and '70:30' in n for n in notes)
    # the next step uses the same base (the reservoir when step 1 came), not what is left
    planned, state, _ = Y.plan(CFG, state, ATH(-0.31), {'NDX': 140_000, 'NDXMIX': 60_000}, 300_000, '2026-11-20')
    assert sum(v for v in planned.values() if v > 0) == pytest.approx(200_000) and state['ndx']['used'] == [-0.20, -0.30]
    planned, _, _ = Y.plan(CFG, {}, ATH(-0.45), {}, 1_000_000, '2026-11-19')  # a gap down through every step at once
    assert sum(planned.values()) == pytest.approx(1_000_000)


def test_a_kofr_reservoir_is_sold_for_what_cash_cannot_pay():
    planned, _, _ = Y.plan(CFG, {}, ATH(-0.21), {'RES': 400_000}, 100_000, '2026-11-19')
    assert planned['RES'] == pytest.approx(-100_000) and planned['NDX'] == pytest.approx(140_000)


def test_a_new_high_resets_the_steps_and_never_sells_to_refill():
    state = {'ndx': {'used': [-0.20], 'base': 500_000}}
    planned, state, notes = Y.plan(CFG, state, ATH(0.0), {'NDX': 150_000, 'NDXMIX': 64_000}, 300_000, '2027-01-04')
    assert (state['ndx']['used'], state['ndx']['base']) == ([], None) and any('재설정' in n for n in notes)
    assert all(v >= 0 or k in ('NDX', 'NDXMIX') for k, v in planned.items())  # only a rebalance may sell, never to refill cash
    assert sum(planned.values()) == pytest.approx(0, abs=1)


def test_rebalance_only_once_held_immediately_past_the_band_or_every_month_and_quarter():
    """User 2026-11-19: not a choice of monthly or quarterly: every month's and every quarter's first check, and at once
    when the risk weight is 5%p or more off."""
    assert Y.plan(CFG, {}, ATH(-0.05), {}, 500_000, '2027-01-04')[0] == {}  # nothing held: nothing to rebalance
    held = {'NDX': 160_000, 'NDXMIX': 40_000}  # 80% risk: past 70 ± 5
    planned, state, notes = Y.plan(CFG, {'ndx': {'used': [-0.20], 'base': 1, 'rebalanced': '2026-Q4'}}, ATH(-0.10), held, 0, '2026-11-19')
    assert planned == {'NDX': pytest.approx(-20_000), 'NDXMIX': pytest.approx(20_000)} and any('리밸런싱: 즉시' in n for n in notes)
    calm = {'NDX': 146_000, 'NDXMIX': 54_000}  # 73%: inside the band
    assert Y.plan(CFG, {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-12'}}, ATH(-0.10), calm, 0, '2026-12-15')[0] == {}
    planned, state, notes = Y.plan(CFG, {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-11'}}, ATH(-0.10), calm, 0, '2026-12-01')
    assert planned['NDX'] == pytest.approx(-6_000) and state['ndx']['rebalanced'] == '2026-12' and any('정기(매월)' in n for n in notes)
    planned, state, notes = Y.plan(CFG, {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-12'}}, ATH(-0.10), calm, 0, '2027-01-04')
    assert planned['NDX'] == pytest.approx(-6_000) and state['ndx']['rebalanced'] == '2027-01' and any('정기(분기·매월)' in n for n in notes)
    # a book saved under the old quarterly key rebalances once at this month's first check, then waits for the next month
    planned, state, _ = Y.plan(CFG, {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-Q4'}}, ATH(-0.10), calm, 0, '2026-11-20')
    assert planned and state['ndx']['rebalanced'] == '2026-11'


def test_a_live_account_of_500_thousand_plans_whole_shares():
    shares = Y.share_plan(CFG, 500_000, {'NDX': 23_870, 'NDXMIX': 9_825})
    first = shares[0]
    assert first['krw'] == 200_000 and (first['NDX']['qty'], first['NDXMIX']['qty']) == (5, 6)  # 140,000 / 23,870 · 60,000 / 9,825


# ---- the engine (virtual book) and its records ----

def test_the_virtual_book_runs_the_method_when_chosen_and_records_the_step(tmp_path, monkeypatch):
    import json
    import longterm_evidence as E
    import vibe_longterm as L
    from test_vibe_longterm import Read, decided, krx_calendar  # noqa: F401
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    st = L.read_state(tmp_path)
    assert st['plan']['indicators']['ath']['ndx']['dd'] == 0  # the stub market keeps rising: at its high
    assert L.trade(tmp_path, Read(), now) == 'planned' and L.read_state(tmp_path)['last_plan']['orders']  # modules: deploys
    L.put_state(L.connect(tmp_path), plan={**st['plan'], 'indicators': {**st['plan']['indicators'], 'ath': {'ndx': {'dd': -0.21}}}},
                planned_day=None)
    L.save_settings(tmp_path, {'execute': True, 'books': {'actual': {'strategy': 'yeo_method'}}})
    assert L.trade(tmp_path, Read(), now) == 'traded'
    held = L.broker_for(tmp_path, Read()).holdings()
    assert set(held) == {'379810', '0261H0'} and held['379810'][0] * 10_000 == __import__('pytest').approx(28_000_000, rel=0.01)
    row = L.connect(tmp_path).execute("SELECT targets, reason, modules FROM decisions WHERE kind='order'").fetchone()
    assert json.loads(row[0])['why'] == 'yeo_step' and '여운봉 단계 1' in row[1]
    assert json.loads(row[2]) == ['yeo_method']  # the log names what traded, not the engine's modules of the day
    masters = json.loads(next(r for r in E.rows(tmp_path) if r['kind'] == 'order')['bundle'])['parts']['masters']
    assert '여운봉 저수지 매매법' in masters['text'] and not masters['steps']
    assert L.read_state(tmp_path)['book:actual']['yeo']['ndx']['used'] == [-0.2]
    assert 'yeo_owed' not in L.read_state(tmp_path)['book:actual']  # everything filled


def test_a_virtual_step_order_that_did_not_fill_is_owed_to_the_next_day(tmp_path, monkeypatch):
    import vibe_longterm as L
    from test_vibe_longterm import Read, decided, krx_calendar  # noqa: F401
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    st = L.read_state(tmp_path)
    L.put_state(L.connect(tmp_path), plan={**st['plan'], 'indicators': {**st['plan']['indicators'], 'ath': {'ndx': {'dd': -0.21}}}})
    L.save_settings(tmp_path, {'execute': True, 'books': {'actual': {'strategy': 'yeo_method'}}})
    def half_filled(broker, planned, quotes, held, execute, reserved=0, res=None):
        return [{'key': k, 'side': 'BUY', 'qty': 10, 'filled': 0 if k == 'NDXMIX' else 10, 'price': 10_000, 'number': 1}
                for k in planned], None
    monkeypatch.setattr(L, 'send_orders', half_filled)
    assert L.trade(tmp_path, Read(), now) == 'traded'
    assert L.read_state(tmp_path)['book:actual']['yeo_owed'] == {'NDXMIX': 100_000 * (1 + L.FEE)}


def test_the_replay_runs_the_real_rule_from_cash():
    from datetime import date, timedelta
    import yeo_method_replay as R
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(300)]
    data = {d: {'risk': 100 * (0.7 if 100 <= i < 200 else 1.0), 'safe': 100.0, 'dd': -0.30 if 100 <= i < 200 else 0.0} for i, d in enumerate(days)}
    method, held = R.run(data, days[0], 'method'), R.run(data, days[0], 'hold')
    assert len(method['steps']) == 2 and method['mdd'] > held['mdd']  # two steps in the fall, a shallower drawdown than holding


def test_dca_replay_ways_spend_new_money_as_named():
    # 적립식 재설계 v2 research (docs/longterm-dca-steady-plan.md 10): synthetic calm market, no step ever fires
    from datetime import date, timedelta
    import yeo_method_replay as R
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(120)]
    data = {d: {'risk': 100.0, 'safe': 100.0, 'dd': -0.05} for d in days}
    alone, now, steady = (R.run_dca(data, days[0], w, 0.01, 0.5) for w in ('alone', 'now', 'steady'))
    assert alone['avg_cash'] > 0.99  # no step: everything waits in the reservoir
    assert 0 < now['avg_cash'] < alone['avg_cash']  # only the contributions are bought
    assert steady['avg_cash'] < alone['avg_cash'] and min(steady['avg_cash'], 1) >= 0.5 - 0.01  # stops at the 50% cash floor


def test_two_indices_each_buy_their_own_share_of_the_reservoir():
    # review 2026-11-18: one running total made the second index buy only what the first left of ITS share
    planned, state, _ = Y.plan({'us_index': 'both', 'mix_ndx': 0.5}, None, {'ndx': {'dd': -0.32}, 'spx': {'dd': -0.32}}, {}, 1_000_000,
                               '2026-11-18')
    ndx = planned.get('NDX', 0) + planned.get('NDXMIX', 0)
    spx = planned.get('SPX', 0) + planned.get('SPXMIX', 0)
    assert ndx == spx == 400_000 and state['spx']['used'] == [-0.2, -0.3]


def test_what_a_step_did_not_buy_is_bought_first_the_next_day():
    # review 2026-11-18: a step's order refused or left unfilled stayed in the reservoir for good (the step was used)
    state = {'ndx': {'used': [-0.2], 'base': 500_000, 'rebalanced': '2026-11'}}  # already rebalanced this month
    planned, after, notes = Y.plan({}, state, {'ndx': {'dd': -0.21}}, {'NDX': 130_000, 'RES': 0}, 300_000, '2026-11-19', owed={'NDXMIX': 60_000})
    assert planned['NDXMIX'] == 60_000 and any('사지 못한' in n for n in notes)
    _, _, notes = Y.plan({}, state, {'ndx': {'dd': -0.21}}, {'NDX': 130_000}, 20_000, '2026-11-19', owed={'NDXMIX': 60_000})
    assert any('20,000원' in n for n in notes if '사지 못한' in n)  # never more than the reservoir holds
    assert Y.unbought([{'key': 'NDXMIX', 'side': 'BUY', 'qty': 5, 'filled': 2, 'price': 10_000},
                       {'key': 'NDX', 'side': 'SELL', 'qty': 3, 'filled': 0, 'price': 10_000}]) == {'NDXMIX': 30_000 * 1.00015}


def test_an_unfilled_rebalance_buy_is_not_owed_only_a_step_buy_is():
    """review5: a rebalance leg is paid by its own sale. When both legs go unfilled (window closed) the next day must not
    buy the rebalance leg from the reservoir; a step's unfilled leg still is owed."""
    _, state, notes = Y.plan({}, {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-10'}}, {'ndx': {'dd': -0.13}},
                             {'NDX': 1_000_000, 'NDXMIX': 0, 'RES': 5_000_000}, 0, '2026-11-20')
    assert any(n.startswith('리밸런싱') for n in notes) and Y.step_buys(state, '2026-11-20') == {}
    rows = [{'key': 'NDXMIX', 'side': 'BUY', 'qty': 30, 'filled': 0, 'price': 10_000}]
    assert Y.unbought(rows, steps=Y.step_buys(state, '2026-11-20')) == {}
    _, state, _ = Y.plan({}, {}, {'ndx': {'dd': -0.21}}, {'RES': 1_000_000}, 0, '2026-11-20')
    steps = Y.step_buys(state, '2026-11-20')
    assert steps == pytest.approx({'NDX': 280_000, 'NDXMIX': 120_000})  # 40% of the reservoir, 70 : 30
    assert Y.unbought(rows + [{'key': 'NDX', 'side': 'BUY', 'qty': 1, 'filled': 1, 'price': 20_000}], steps=steps) == \
        {'NDXMIX': pytest.approx(120_000)}  # capped at the step's leg


def test_switching_to_the_method_warns_that_held_etfs_are_rebalanced():
    planned, _, _ = Y.plan(None, {}, {'ndx': {'dd': -0.13}}, {'NDX': 1_000_000, 'RES': 0}, 0, '2026-11-20')
    assert planned['NDX'] < 0  # a book that holds the risk ETF sells part of it at the first check
    note = Y.switch_note(None)
    assert '나스닥100' in note and '70 : 30' in note and '팔' in note


# ---- 저수지 현금 → 저수지 ETF (user 2026-11-24). The next step's money stays cash: NH's 주문가능금액 does not count the
# same-day sale of an older holding (11-24, live-1: 005930 sold, 1,322,755 before and after), so the guard would refuse the step

PARK = Y.clean({'park': True})


def test_parking_is_off_unless_set_and_needs_the_reservoir_price():
    assert Y.clean({})['park'] is False and Y.clean({'park': 1})['park'] is False
    assert Y.plan(CFG, {}, ATH(-0.14), {}, 500_000, '2026-11-25', res_price=58_000)[0] == {}
    assert Y.plan(PARK, {}, ATH(-0.14), {}, 500_000, '2026-11-25')[0] == {}


def test_what_the_next_step_does_not_need_goes_into_the_reservoir_etf():
    planned, _, notes = Y.plan(PARK, {}, ATH(-0.14), {}, 500_000, '2026-11-25', res_price=58_000)
    assert planned == {'RES': pytest.approx(300_000)} and any('다음 단계 몫 200,000원은 현금' in n for n in notes)
    # held already: the target counts cash and ETF together, and nothing moves for less than a share
    assert Y.plan(PARK, {}, ATH(-0.14), {'RES': 290_000}, 210_000, '2026-11-26', res_price=58_000)[0] == {}
    assert Y.plan(PARK, {}, {}, {}, 500_000, '2026-11-25', res_price=58_000)[0] == {}  # no drawdown known: no move


def test_the_step_day_buys_with_the_cash_kept_for_it_and_parks_nothing():
    planned, state, _ = Y.plan(PARK, {}, ATH(-0.21), {'RES': 290_000}, 210_000, '2026-12-01', res_price=58_000)
    assert planned == {'NDX': pytest.approx(140_000), 'NDXMIX': pytest.approx(60_000)}  # no RES sale needed
    # the next day the second step's 200,000 is made cash again ahead of time, in whole shares rounded up
    held = {'NDX': 140_000, 'NDXMIX': 60_000, 'RES': 290_000}
    planned, _, _ = Y.plan(PARK, state, ATH(-0.22), held, 10_000, '2026-12-02', res_price=58_000)
    assert planned == {'RES': -58_000 * 4}  # 190,000 short → 4 shares


def test_after_the_last_step_everything_left_is_parked_and_a_short_step_sells_whole_shares_up():
    done = {'ndx': {'used': [-0.20, -0.30, -0.40], 'base': 500_000, 'rebalanced': '2026-12'}}
    planned, _, _ = Y.plan(PARK, done, ATH(-0.41), {'NDX': 70_000, 'NDXMIX': 30_000}, 100_000, '2026-12-03', res_price=58_000)
    assert planned == {'RES': pytest.approx(100_000)}
    planned, _, _ = Y.plan(PARK, {}, ATH(-0.31), {'RES': 290_000}, 210_000, '2026-12-01', res_price=58_000)  # a gap through two steps
    assert planned['RES'] == -58_000 * 4 and sum(v for v in planned.values() if v > 0) == pytest.approx(400_000)


def test_planning_a_step_never_marks_it_used_in_the_state_it_was_given():
    # 2026-11-24 rehearsal: the caller's 'used' list was appended to, so a step none of whose orders went out was lost
    given = {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-11'}}
    _, new, _ = Y.plan(CFG, given, ATH(-0.21), {}, 500_000, '2026-12-01')
    assert new['ndx']['used'] == [-0.20] and given == {'ndx': {'used': [], 'base': None, 'rebalanced': '2026-11'}}


def test_only_a_longterm_book_set_to_the_method_ever_has_a_reservoir():
    # user 2026-11-24: never on a Jev account (실계좌 1)
    from pathlib import Path
    import vibe_longterm as L
    settings = {'books': {'live-2': L.clean_book({'strategy': 'yeo_method', 'yeo': {'park': True}}), 'other': L.clean_book({'strategy': 'engine', 'yeo': {'park': True}})}}
    assert L.yeo_settings(settings, 'live-2')['park'] and L.yeo_settings(settings, 'other') is None and L.yeo_settings(settings, 'live-1') is None
    here = Path(__file__).parent
    for name in ('nh_live_follow.py', 'nh_paper_isolated.py', 'nh_live_shadow.py', 'nh_guarded_live.py'):
        text = (here / name).read_text(encoding='utf-8')
        assert 'yeo_method' not in text and 'yeo_orders' not in text, name
