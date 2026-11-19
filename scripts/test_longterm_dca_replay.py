from datetime import date, timedelta

import longterm_dca_replay as R
from vibe_longterm_replay import PROXY
import vibe_longterm as L


def panel(days=420):
    """A gently rising market with one sharp fall, every proxy and VIX present."""
    out, start = {}, date(2020, 1, 1)
    for i in range(days):
        usd = 100 * (1 + i / 1000) * (0.75 if 300 <= i < 330 else 1.0)
        out[start + timedelta(days=i)] = {**{n: usd * 1300 for n in PROXY}, 'VIX': 40 if 300 <= i < 305 else 18, 'NDX_USD': usd}
    return out


def test_every_profile_and_auto_run_through_the_engine_and_never_lose_a_contribution():
    p = panel()
    for variant in R.PROFILE_VARIANTS:
        r = R.run_engine(p, variant)
        assert r['contributed'] > 0 and r['final'] > 0 and r['label']
        assert r['regime_days'] and set(r['regime_days']) <= set(L.REGIMES)
    yeo, dalio = R.run_engine(p, 'yeo'), R.run_engine(p, 'dalio')
    assert yeo['avg_cash'] > dalio['avg_cash']  # 여운봉 keeps money waiting for the steps
    runs = {v: R.run_engine(p, v) for v in ('bogle_buffett', 'marks')}  # each master runs its own way (v3), not one calendar
    assert all(r['buy_days'] > 2 * 7 for r in runs.values())  # the master buys weekly, not once on each of the 7 contribution days


def test_a_preset_module_set_runs_through_the_engine_and_without_bogle_mostly_waits():
    p = panel()
    rows = {r['variant']: r for r in R.preset_rows(p)}
    assert set(rows) == set(L.BOOK_PRESETS) and all(r['label'] == L.BOOK_PRESETS[k][0] for k, r in rows.items())
    assert rows['yeo_only']['avg_cash'] > rows['bogle_yeo']['avg_cash']  # 보글 없음: the money waits for the drawdown steps


def test_the_steady_proposal_buys_weekly_keeps_the_cash_target_and_never_goes_all_in():
    # D1 (docs/longterm-dca-steady-plan.md): research rule only, longterm_dca is untouched
    from datetime import date
    import longterm_dca_replay as R
    cfg = {'mult': R.STEADY['steady_marks'][1], 'cash': 0.20, 'months': 24}
    want = {'NDX': 0.5, 'SPX': 0.5, 'RES': 0.0}
    state = {}
    first = R.steady_buy(cfg, state, date(2026, 11, 16), {'day_ret': -0.01}, 'correction', want, 1e8, 1e8)
    base = 0.8e8 / (24 * R.WEEKS_PER_MONTH)
    assert abs(sum(first.values()) - min(base * 1.2 * 1.2, 1.5 * base)) < 1  # correction ×1.2, down day ×1.2, capped at 1.5 B
    assert R.steady_buy(cfg, state, date(2026, 11, 17), {}, 'correction', want, 1e8, 1e8) == {}  # once a week
    assert R.steady_buy(cfg, {}, date(2026, 11, 16), {}, 'bull', want, 1e8, 0.2e8) == {}  # cash already at the 20% target
    calm = R.steady_buy(cfg, {}, date(2026, 11, 16), {'day_ret': 0.01}, 'overheat', want, 1e8, 1e8)
    assert 0 < sum(calm.values()) < base  # overheat slows down (×0.6 × up day 0.8) but does not stop
